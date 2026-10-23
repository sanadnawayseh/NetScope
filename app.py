import json
import math
import errno
import re
import ipaddress
import os
import platform
import socket
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response
from werkzeug.exceptions import HTTPException
from dns_diagnostics import DNSDiagnostics, DNSRequestError, MAX_REQUEST_BYTES
from traceroute_diagnostics import TraceDiagnostics, TraceRequestError
from monitoring_coordinator import Measurement, MonitoringCoordinator, MonitoringStopped, parse_worker_limit
from schema_migrations import migrate_schema
from application_logging import configure_logging
from historical_analytics import time_window, query_history, AnalyticsLimit, POINT_LIMIT, TIME_SQL
import advanced_analytics
import reporting
import time
from incident_management import configure as configure_incidents, process_pending, CONDITIONS, acknowledge, IncidentNotFound, IncidentConflict
from incident_notifications import recent_events, mark_read
from network_discovery import Discovery, DiscoveryError, echo as discovery_echo, observations as discovery_observations

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('NETSCOPE_DB', BASE / 'netscope.db'))
def setting(name, default, minimum, maximum):
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


CHECK_INTERVAL = setting('NETSCOPE_CHECK_INTERVAL', 30, 1, 3600)
ICMP_TIMEOUT_MS = int(setting('NETSCOPE_ICMP_TIMEOUT_MS', 1000, 100, 10000))
TCP_TIMEOUT = setting('NETSCOPE_TCP_TIMEOUT', 1.5, 0.1, 30)
MONITOR_WORKERS = parse_worker_limit(os.environ.get('NETSCOPE_MONITOR_WORKERS', '4'))
NETWORK_ERROR_CODES = (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.ECONNRESET, errno.ECONNABORTED, 10050, 10051, 10053, 10054, 10065)
ICMP_PROBES = 5
PACKET_FIELDS = ('icmp_sent', 'icmp_received', 'icmp_unanswered', 'packet_loss_pct',
                 'rtt_min_ms', 'rtt_max_ms', 'rtt_sample_count')
app = Flask(__name__)
app.config.update(DEBUG=False, MAX_CONTENT_LENGTH=4096)
lock = threading.Lock()
coordinator = MonitoringCoordinator(MONITOR_WORKERS, batch_lock=lock, logger=app.logger)
dns_service = DNSDiagnostics()
ALLOWED_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


@app.before_request
def local_request_boundary():
    try:
        peer = ipaddress.ip_address(request.remote_addr or '')
    except ValueError:
        return jsonify({'error': 'NetScope requires a local loopback connection'}), 403
    host = request.host.lower()
    if not peer.is_loopback or not re.fullmatch(r'(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?', host):
        return jsonify({'error': 'NetScope accepts only the local loopback dashboard host'}), 403
    if request.content_length is not None and request.content_length > app.config['MAX_CONTENT_LENGTH']:
        return jsonify({'error': 'Request body is too large (maximum 4096 bytes)'}), 413
    origin = request.headers.get('Origin')
    if ((origin is not None and origin != request.host_url.rstrip('/'))
            or request.headers.get('Sec-Fetch-Site') == 'cross-site'):
        return jsonify({'error': 'Cross-origin requests to the local dashboard are not allowed'}), 403


@app.after_request
def response_safeguards(response):
    # Windows MIME registry entries can label .js as text/plain. nosniff
    # correctly blocks that type, so make this static response deterministic.
    if request.endpoint == 'static' and response.status_code in (200, 304) and request.view_args.get('filename', '').endswith('.js'):
        response.mimetype = 'text/javascript'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    if request.endpoint=='download_report' and response.status_code==200:
        response.headers['Cache-Control']='no-store, private'
        response.headers['Content-Security-Policy']="sandbox; default-src 'none'; frame-ancestors 'none'"
    return response


@app.errorhandler(sqlite3.Error)
def database_unavailable(error):
    app.logger.exception('SQLite operation failed', extra={'event': 'database_failure'})
    return jsonify({'error': 'Database unavailable. Check local storage and retry; existing data has not been reset.'}), 503


@app.errorhandler(HTTPException)
def http_error(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': error.description}), error.code
    return error


def target_mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        if not lock.acquire(blocking=False):
            return jsonify({'error': 'A monitoring cycle is running. Wait for it to finish before changing devices.'}), 409
        try:
            return function(*args, **kwargs)
        finally:
            lock.release()
    return guarded


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def validate_ip(value):
    """MVP supports IPv4 literals only; no public/multicast/link-local IPs."""
    if not isinstance(value, str) or not 1 <= len(value) <= 15:
        raise ValueError('Enter an IPv4 address literal')
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError('Enter an IPv4 address, such as 127.0.0.1 or 192.168.1.10') from exc
    if address.version != 4 or not any(address in network for network in ALLOWED_NETWORKS):
        raise ValueError('Only private or loopback IPv4 addresses are allowed')
    return str(address)


def validate_port(value):
    if type(value) is int:
        port = value
    elif isinstance(value, str) and re.fullmatch(r'[0-9]{1,5}', value):
        port = int(value)
    else:
        raise ValueError('Port must be an integer between 1 and 65535')
    if not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    return port


trace_service = TraceDiagnostics(validate_ip)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        if conn.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
            raise sqlite3.DatabaseError('SQLite WAL mode could not be enabled')
        # Explicit BEGIN makes SQLite DDL and user_version roll back together.
        conn.execute('BEGIN IMMEDIATE')
        if migrate_schema(conn):
            conn.execute('INSERT INTO targets(name, ip, port, created_at) VALUES(?,?,?,?)', ('Local computer', '127.0.0.1', 80, utc_now()))
        # Additive performance index; no table/data or schema-version change.
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_results_observed ON results(target_id, ({TIME_SQL}), id)')
        configure_incidents(conn)
    process_pending(get_db, check_states, observation_time, app.logger)


def parse_ping_output(output):
    """Return echo response, exact RTT, optional upper bound, diagnostic.

    Windows can exit zero for destination-unreachable replies. Require an
    echo reply (TTL) rather than treating the exit code alone as success.
    Only parse reply lines, never rounded summary averages.
    """
    replies = [line for line in output.splitlines()
               if re.search(r'\bttl\s*[=:]\s*\d+', line, re.IGNORECASE)]
    if replies:
        for line in replies:
            match = re.search(r'(?:time\s*)?([=<])\s*(\d+(?:[.,]\d+)?)\s*ms\b', line, re.IGNORECASE)
            if match:
                value = float(match[2].replace(',', '.'))
                if not math.isfinite(value) or value > 60000 or (match[1] == '<' and value <= 0):
                    continue
                if match[1] == '<':
                    return True, None, value, None
                return True, value, None, None
        return True, None, None, 'Echo response received; RTT unavailable in ping output'
    lower = output.lower()
    if 'timed out' in lower or '100% packet loss' in lower:
        return False, None, None, 'Echo request timed out (ICMP may be blocked)'
    if 'unreachable' in lower:
        return False, None, None, 'Destination unreachable'
    return False, None, None, 'No echo response received (ICMP may be blocked)'


def empty_icmp(error):
    return dict(ping_ok=False, latency_ms=None, latency_upper_bound_ms=None,
                ping_error=error, **dict.fromkeys(PACKET_FIELDS))


def parse_ping_batch(output):
    """Validate a completed five-probe batch; count echo replies, not ICMP errors.

    Windows counts some destination-unreachable messages as received packets.
    They are not echo responses and are treated as unanswered echo requests.
    Missing or inconsistent summaries never become fabricated loss values.
    """
    result = empty_icmp(None)
    lines = output.splitlines()
    replies = [parse_ping_output(line) for line in lines
               if re.search(r'\bttl\s*[=:]\s*\d+', line, re.IGNORECASE)]
    result['ping_ok'] = bool(replies)
    exact = [r[1] for r in replies if r[1] is not None and math.isfinite(r[1])]
    result['rtt_sample_count'] = len(exact)
    if replies and len(exact) == len(replies):
        result.update(latency_ms=sum(exact) / len(exact),
                      rtt_min_ms=min(exact), rtt_max_ms=max(exact))
    elif replies and all((r[1] is not None or r[2] is not None) and math.isfinite(r[1] if r[1] is not None else r[2]) for r in replies):
        result['latency_upper_bound_ms'] = sum(r[1] if r[1] is not None else r[2] for r in replies) / len(replies)
    elif replies:
        result['ping_error'] = 'Echo responses received; complete RTT statistics unavailable'

    summary = re.search(r'Sent\s*=\s*(\d+)\s*,\s*Received\s*=\s*(\d+)\s*,\s*Lost\s*=\s*(\d+)', output, re.IGNORECASE)
    unix = re.search(r'(\d+) packets transmitted,\s*(\d+) (?:packets )?received', output, re.IGNORECASE)
    if summary:
        sent, reported_received, lost = map(int, summary.groups())
        unreachable = sum('unreachable' in line.lower() for line in lines)
        valid = sent == ICMP_PROBES and 0 <= reported_received <= sent and lost == sent - reported_received
        valid = valid and len(replies) <= sent and reported_received in (len(replies), len(replies) + unreachable)
    elif unix:
        sent, reported_received = map(int, unix.groups())
        valid = sent == ICMP_PROBES and reported_received == len(replies) and 0 <= reported_received <= sent
    else:
        valid = False
    if not valid:
        result['ping_error'] = 'Incomplete or malformed ping statistics; packet loss unavailable'
        # A truncated batch cannot provide cycle RTT statistics either.
        result.update(latency_ms=None, latency_upper_bound_ms=None, rtt_min_ms=None, rtt_max_ms=None)
        return result
    received = len(replies)
    result.update(icmp_sent=sent, icmp_received=received, icmp_unanswered=sent-received,
                  packet_loss_pct=(sent-received) * 100.0 / sent)
    if not replies:
        result['ping_error'] = parse_ping_output(output)[3]
    return result


def ping(ip):
    ip = validate_ip(ip)
    windows = platform.system().lower() == 'windows'
    timeout_seconds = ICMP_TIMEOUT_MS / 1000
    if windows:
        cmd = [str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'ping.exe'), '-n', str(ICMP_PROBES), '-w', str(ICMP_TIMEOUT_MS), ip]
    elif platform.system().lower() == 'darwin':
        cmd = ['ping', '-c', str(ICMP_PROBES), '-W', str(ICMP_TIMEOUT_MS), ip]
    else:
        cmd = ['ping', '-c', str(ICMP_PROBES), '-W', str(math.ceil(timeout_seconds)), ip]
    # Allow for per-request waits and ping's one-second pacing, plus startup.
    deadline = ICMP_PROBES * (math.ceil(timeout_seconds) + 1) + 2
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, errors='replace', timeout=deadline, check=False, shell=False)
        # Partial loss may produce a nonzero exit code. The validated summary
        # and actual echo replies, not the return code, determine measurements.
        if completed.returncode not in (0, 1):
            return empty_icmp('Ping command failed; complete batch statistics unavailable')
        return parse_ping_batch((completed.stdout or '') + '\n' + (completed.stderr or ''))
    except subprocess.TimeoutExpired:
        return empty_icmp('Ping command timed out; complete batch statistics unavailable')
    except OSError:
        return empty_icmp('Ping command unavailable or could not be started')


def tcp_check(ip, port):
    ip = validate_ip(ip)
    port = validate_port(port)
    try:
        with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
            return True, None
    except OSError as exc:
        code = getattr(exc, 'winerror', None) or exc.errno
        if isinstance(exc, ConnectionRefusedError) or code in (errno.ECONNREFUSED, 10061):
            return False, 'Connection refused (port is not accepting connections)'
        if isinstance(exc, TimeoutError) or code in (errno.ETIMEDOUT, 10060):
            return False, 'Connection timed out (service did not respond)'
        if code in NETWORK_ERROR_CODES:
            return False, f'Network error: {str(exc)[:120]}'
        return False, 'TCP check unavailable (local socket error)'


def collect_measurement(target):
    """Collect only; protocol execution errors remain unknown, not offline."""
    ip = validate_ip(target['ip'])
    port = validate_port(target['port'])
    execution_failed = False
    try:
        measurements = ping(ip)
    except Exception:
        execution_failed = True
        app.logger.exception('ICMP execution failed', extra={'event': 'probe_failure', 'protocol': 'icmp'})
        measurements = empty_icmp('Ping command failed unexpectedly; check unavailable')
    try:
        tcp_ok, tcp_error = tcp_check(ip, port)
    except Exception:
        execution_failed = True
        app.logger.exception('TCP execution failed', extra={'event': 'probe_failure', 'protocol': 'tcp'})
        tcp_ok, tcp_error = False, 'TCP check unavailable (unexpected execution error)'
    ping_error = measurements['ping_error']
    error = '; '.join(f'{label}: {message}' for label, message in
                      (('ICMP', ping_error), ('TCP', tcp_error)) if message) or None
    row = dict(target_id=target['id'], checked_at=utc_now(),
               **measurements, tcp_ok=int(tcp_ok), tcp_error=tcp_error,
               error=error, latency_source='icmp_rtt')
    row['ping_ok'] = int(row['ping_ok'])
    return Measurement(row, execution_failed)


def persist_measurement(row):
    """One short transaction on the calling thread; never hold it during probes."""
    validate_measurement(row)
    with get_db() as conn:
        columns = ', '.join(row)
        placeholders = ', '.join('?' for _ in row)
        conn.execute(f'INSERT INTO results ({columns}) VALUES ({placeholders})', tuple(row.values()))
    # Separate transaction: evaluate only confirmed committed measurements.
    process_pending(get_db, check_states, observation_time, app.logger, row['target_id'])


def validate_measurement(row):
    """Reject invalid new records without rewriting legacy/unknown samples."""
    allowed = {'target_id', 'checked_at', 'ping_ok', 'tcp_ok', 'latency_ms',
               'latency_upper_bound_ms', 'ping_error', 'tcp_error', 'error',
               'latency_source', *PACKET_FIELDS}
    required = {'target_id', 'checked_at', 'ping_ok', 'tcp_ok'}
    def invalid():
        raise sqlite3.IntegrityError('Invalid monitoring measurement')
    if not isinstance(row, dict) or not required <= row.keys() or row.keys() - allowed:
        invalid()
    if type(row['target_id']) is not int or row['target_id'] <= 0:
        invalid()
    if observation_time(row['checked_at']) is None:
        invalid()
    if any(type(row[key]) is not int or row[key] not in (0, 1) for key in ('ping_ok', 'tcp_ok')):
        invalid()
    for key in ('ping_error', 'tcp_error', 'error', 'latency_source'):
        if row.get(key) is not None and not isinstance(row[key], str):
            invalid()
    for key in ('latency_ms', 'latency_upper_bound_ms', 'rtt_min_ms', 'rtt_max_ms', 'packet_loss_pct'):
        value = row.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            invalid()
    if row.get('latency_upper_bound_ms') is not None:
        if row['latency_upper_bound_ms'] <= 0 or row.get('latency_ms') is not None:
            invalid()
    for key in ('icmp_sent', 'icmp_received', 'icmp_unanswered', 'rtt_sample_count'):
        value = row.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= ICMP_PROBES):
            invalid()
    counts = [row.get(key) for key in ('icmp_sent', 'icmp_received', 'icmp_unanswered', 'packet_loss_pct')]
    if any(value is not None for value in counts):
        sent, received, unanswered, loss = counts
        if (any(value is None for value in counts) or sent != ICMP_PROBES
                or received + unanswered != sent or bool(row['ping_ok']) != (received > 0)
                or not math.isclose(loss, unanswered * 100 / sent, abs_tol=0.001)):
            invalid()
        if row.get('rtt_sample_count') is not None and row['rtt_sample_count'] > received:
            invalid()
    minimum, maximum, mean = (row.get(key) for key in ('rtt_min_ms', 'rtt_max_ms', 'latency_ms'))
    if minimum is not None and maximum is not None:
        if minimum > maximum or (mean is not None and not minimum <= mean <= maximum):
            invalid()


def run_check(target):
    """Compatible single-target wrapper, including the original return fields."""
    measurement = collect_measurement(target)
    persist_measurement(measurement.row)
    row = measurement.row
    return dict(ping_ok=bool(row['ping_ok']), latency_ms=row['latency_ms'],
                latency_upper_bound_ms=row['latency_upper_bound_ms'],
                ping_error=row['ping_error'], **{name: row[name] for name in PACKET_FIELDS},
                tcp_ok=bool(row['tcp_ok']), tcp_error=row['tcp_error'])


HISTORY_LIMIT = 60


def observation_time(value):
    """Only recorded, timezone-aware instants qualify for observation windows."""
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return instant.astimezone(timezone.utc).isoformat() if instant.tzinfo else None
    except (ValueError, OverflowError):
        return None


def check_states(row):
    """Conservative classification; never infer completion from a false flag."""
    data = dict(row)
    states = {'icmp': 'unknown', 'tcp': 'unknown'}
    if data.get('latency_source') != 'icmp_rtt' or not observation_time(data.get('checked_at')):
        return states
    sent, received, unanswered = (data.get(key) for key in ('icmp_sent', 'icmp_received', 'icmp_unanswered'))
    loss = data.get('packet_loss_pct')
    counts_valid = (all(type(n) is int for n in (sent, received, unanswered))
                    and sent == ICMP_PROBES and 0 <= received <= sent
                    and unanswered == sent - received)
    if (counts_valid and data.get('ping_ok') in (0, 1)
            and bool(data['ping_ok']) == (received > 0)
            and isinstance(loss, (int, float)) and math.isfinite(loss)
            and math.isclose(loss, unanswered * 100 / sent, abs_tol=0.001)
            and not str(data.get('ping_error') or '').startswith(('Ping command', 'Incomplete or malformed'))):
        states['icmp'] = 'success' if received else 'failure'
    tcp_error = data.get('tcp_error')
    if data.get('tcp_ok') == 1 and not tcp_error:
        states['tcp'] = 'success'
    elif data.get('tcp_ok') == 0 and isinstance(tcp_error, str):
        network_code = re.search(r'\[(?:WinError|Errno) ([0-9]{1,5})\]', tcp_error)
        classified_network_error = (tcp_error.startswith('Network error: ') and network_code is not None
                                    and int(network_code[1]) in NETWORK_ERROR_CODES)
        if (tcp_error.startswith('Connection refused (')
                or tcp_error.startswith('Connection timed out (')
                or classified_network_error):
            states['tcp'] = 'failure'
    return states


def availability_summary(rows):
    """Each denominator includes only that protocol's classifiable checks."""
    classified = [(check_states(row), observation_time(dict(row).get('checked_at'))) for row in rows]
    summary = {'scope': 'latest_60_records', 'record_count': len(rows)}
    for protocol in ('icmp', 'tcp'):
        valid = [(state[protocol], instant) for state, instant in classified if state[protocol] != 'unknown']
        successful = sum(state == 'success' for state, _ in valid)
        times = [instant for _, instant in valid]
        summary[protocol] = {
            'percentage': successful * 100 / len(valid) if valid else None,
            'successful_checks': successful, 'valid_checks': len(valid),
            'excluded_checks': len(rows) - len(valid),
            'window_start': min(times) if times else None,
            'window_end': max(times) if times else None,
        }
    return summary


def history_rows(conn, target_id):
    return list(reversed(conn.execute('SELECT * FROM results WHERE target_id=? ORDER BY id DESC LIMIT ?',
                                     (target_id, HISTORY_LIMIT)).fetchall()))


def diagnostic_result(row):
    data = dict(row)
    states = check_states(row)
    data['icmp_check_status'] = states['icmp']
    data['tcp_check_status'] = states['tcp']
    data['legacy_error'] = None
    if data['checked_at'] and data['latency_source'] is None:
        # Keep original durations in SQLite, but never mislabel them as RTT.
        data['latency_ms'] = None
        data['latency_source'] = 'legacy_process_duration'
        if data['error']:
            if data['ping_ok'] and not data['tcp_ok']:
                data['tcp_error'] = data['error']
            elif data['tcp_ok'] and not data['ping_ok']:
                data['ping_error'] = data['error']
            else:
                data['legacy_error'] = data['error']
    return data


def check_all(report=False):
    result = coordinator.run_batch(load_targets, collect_measurement, persist_measurement)
    return result if result is False or report else True


def load_targets():
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM targets')]


def monitoring_loop():
    while not coordinator.stop_event.is_set():
        try:
            check_all()
        except Exception:
            app.logger.exception('Monitoring batch failed; retrying after the configured interval',
                                 extra={'event': 'monitor_loop_failure'})
        coordinator.stop_event.wait(CHECK_INTERVAL)


@app.get('/')
def index():
    return render_template('index.html', check_interval=CHECK_INTERVAL,
                           script_version=(BASE / 'static' / 'app.js').stat().st_mtime_ns,
                           notification_version=(BASE / 'static' / 'notifications.js').stat().st_mtime_ns,
                           discovery_version=(BASE / 'static' / 'discovery.js').stat().st_mtime_ns,
                           map_version=(BASE / 'static' / 'network-map.js').stat().st_mtime_ns,
                           analytics_version=(BASE / 'static' / 'analytics.js').stat().st_mtime_ns,
                           report_version=(BASE / 'static' / 'reports.js').stat().st_mtime_ns)


@app.get('/api/targets')
def targets():
    with get_db() as conn:
        conn.execute('BEGIN')
        rows = conn.execute('''SELECT t.*, r.checked_at, r.ping_ok, r.latency_ms, r.tcp_ok, r.error, r.ping_error, r.tcp_error, r.latency_source, r.latency_upper_bound_ms, r.icmp_sent, r.icmp_received, r.icmp_unanswered, r.packet_loss_pct, r.rtt_min_ms, r.rtt_max_ms, r.rtt_sample_count
                              FROM targets t LEFT JOIN results r ON r.id = (
                                SELECT MAX(id) FROM results WHERE target_id = t.id)
                              ORDER BY t.id''').fetchall()
        summaries = {row['id']: availability_summary(history_rows(conn, row['id'])) for row in rows}
    stale_after = max(90, CHECK_INTERVAL + len(rows) * (ICMP_PROBES * (math.ceil(ICMP_TIMEOUT_MS / 1000) + 1) + 2 + TCP_TIMEOUT) + 10)
    return jsonify([dict(diagnostic_result(row), stale_after_seconds=stale_after, availability=summaries[row['id']]) for row in rows])


@app.post('/api/targets')
def create_target():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400
    try:
        if not isinstance(data.get('name'), str) or not isinstance(data.get('ip'), str):
            raise ValueError('Name and IPv4 address must be text')
        name = data['name'].strip()
        if not 1 <= len(name) <= 60:
            raise ValueError('Name must be between 1 and 60 characters')
        ip = validate_ip(data['ip'].strip())
        port = validate_port(data.get('port', 80))
    except (ValueError, TypeError) as exc:
        return jsonify({'error': str(exc)}), 400
    return insert_target(name, ip, port)


@target_mutation
def insert_target(name, ip, port):
    with get_db() as conn:
        result = conn.execute('INSERT INTO targets(name,ip,port,created_at) VALUES(?,?,?,?)', (name, ip, port, utc_now()))
    return jsonify({'id': result.lastrowid}), 201


@app.delete('/api/targets/<int:target_id>')
@target_mutation
def delete_target(target_id):
    with get_db() as conn:
        result = conn.execute('DELETE FROM targets WHERE id=?', (target_id,))
    return ('', 204) if result.rowcount else (jsonify({'error': 'Target not found'}), 404)


@app.get('/api/targets/<int:target_id>/history')
def history(target_id):
    if request.args:
        if set(request.args) != {'range'} or len(request.args.getlist('range')) != 1:
            return jsonify({'error': 'Provide only one range parameter'}), 400
        try:
            start, end = time_window(request.args['range'])
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        with get_db() as conn:
            rows = conn.execute(f'''SELECT * FROM results WHERE target_id=?
                AND ({TIME_SQL})>=julianday(?) AND ({TIME_SQL})<=julianday(?)
                ORDER BY ({TIME_SQL}) DESC, id DESC LIMIT ?''', (target_id, start, end, POINT_LIMIT)).fetchall()
        return jsonify([diagnostic_result(row) for row in reversed(rows)])
    with get_db() as conn:
        rows = history_rows(conn, target_id)
    return jsonify([diagnostic_result(row) for row in rows])


@app.get('/api/history')
def historical_analytics():
    if (set(request.args) != {'targets', 'range'}
            or any(len(request.args.getlist(key)) != 1 for key in request.args)):
        return jsonify({'error': 'Provide targets and range once each'}), 400
    try:
        tokens = request.args['targets'].split(',')
        if not 1 <= len(tokens) <= 4 or any(not re.fullmatch(r'[1-9][0-9]{0,9}', token) for token in tokens):
            raise ValueError('Select one to four distinct device IDs')
        ids = [int(token) for token in tokens]
        if len(set(ids)) != len(ids):
            raise ValueError('Select distinct device IDs')
        start, end = time_window(request.args['range'])
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    deadline = time.monotonic() + 10
    try:
        with get_db() as conn:
            conn.execute('BEGIN')
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            series = []
            for target_id in ids:
                target = conn.execute('SELECT id, name FROM targets WHERE id=?', (target_id,)).fetchone()
                if target is None:
                    return jsonify({'error': 'Device not found'}), 404
                series.append(dict(target_id=target_id, name=target['name'],
                    **query_history(conn, target_id, start, end, check_states, observation_time, deadline)))
    except AnalyticsLimit as exc:
        return jsonify({'error': str(exc)}), 503
    except sqlite3.OperationalError:
        if time.monotonic() > deadline:
            return jsonify({'error': 'History query exceeded the local analysis budget; select fewer devices or a shorter range.'}), 503
        raise
    return jsonify(dict(start=start, end=end, point_limit=POINT_LIMIT, series=series))


@app.get('/api/analytics')
@app.get('/api/analytics/incidents')
def advanced_history_analytics():
    incidents_only=request.path.endswith('/incidents')
    try:
        ids,start,end,previous,as_of=advanced_analytics.window(request.args,require_targets=not incidents_only)
    except ValueError as error:
        return jsonify(error=str(error)),400
    deadline=time.monotonic()+10
    try:
        with get_db() as conn:
            conn.execute('BEGIN')
            conn.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
            result=advanced_analytics.analyze(conn,ids,start,end,previous,as_of,
                check_states,observation_time,deadline,incidents_only=incidents_only)
        return jsonify(result)
    except LookupError:
        return jsonify(error='Device not found'),404
    except AnalyticsLimit as error:
        return jsonify(error=str(error)),503
    except sqlite3.OperationalError:
        if time.monotonic()>deadline:
            return jsonify(error='Analytics time budget exceeded; select fewer devices or a shorter window'),503
        raise


@app.get('/api/reports.<fmt>')
def download_report(fmt):
    if fmt not in ('csv','pdf'):return jsonify(error='Report format must be csv or pdf'),400
    try:
        window,content=reporting.options(request.args)
    except ValueError as error:return jsonify(error=str(error)),400
    try:
        payload=reporting.generate(get_db,window,content,fmt,check_states,observation_time)
    except LookupError:return jsonify(error='Selected monitored device not found; deleted-device reports are unavailable'),404
    except AnalyticsLimit as error:return jsonify(error=str(error)),503
    except reporting.ReportError as error:return jsonify(error=str(error)),error.status
    except (OSError,ValueError):
        app.logger.exception('Report generation failed',extra={'event':'report_failure'})
        return jsonify(error='Report generation unavailable; see local logs'),503
    response=Response(payload,content_type='text/csv; charset=utf-8' if fmt=='csv' else 'application/pdf')
    response.headers['Content-Disposition']=f'attachment; filename="netscope-report.{fmt}"'
    response.headers['Cache-Control']='no-store, private'
    response.headers['Pragma']='no-cache'
    response.headers['Content-Security-Policy']="sandbox; default-src 'none'; frame-ancestors 'none'"
    return response


@app.post('/api/dns')
def dns_lookup():
    # DNS-only boundary: do not change or broaden the monitoring APIs.
    try:
        peer = ipaddress.ip_address(request.remote_addr or '')
    except ValueError:
        return jsonify({'error': 'DNS diagnostics requires a local connection'}), 403
    host = request.host.split(':')[0].lower()
    if not peer.is_loopback or host not in ('127.0.0.1', 'localhost'):
        return jsonify({'error': 'DNS diagnostics requires a loopback host and connection'}), 403
    origin = request.headers.get('Origin')
    if origin and origin != request.host_url.rstrip('/'):
        return jsonify({'error': 'DNS diagnostics must be requested from this local dashboard'}), 403
    if not request.is_json:
        return jsonify({'error': 'Send a JSON object containing a domain name'}), 415
    if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
        return jsonify({'error': 'DNS request is too large (maximum 1024 bytes)'}), 413
    raw = request.stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        return jsonify({'error': 'DNS request is too large (maximum 1024 bytes)'}), 413
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'domain'}:
            raise DNSRequestError('Provide only a domain field in a JSON object')
        result = dns_service.lookup(data['domain'])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({'error': 'The request is not valid JSON'}), 400
    except DNSRequestError as exc:
        return jsonify({'error': str(exc)}), exc.status
    response = jsonify(result)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.post('/api/traceroute')
def traceroute():
    try:
        peer = ipaddress.ip_address(request.remote_addr or '')
    except ValueError:
        return jsonify({'error': 'Traceroute requires a local connection'}), 403
    if not peer.is_loopback or request.host.split(':')[0].lower() not in ('127.0.0.1', 'localhost'):
        return jsonify({'error': 'Traceroute requires a loopback host and connection'}), 403
    origin = request.headers.get('Origin')
    if origin and origin != request.host_url.rstrip('/'):
        return jsonify({'error': 'Traceroute must be requested from this local dashboard'}), 403
    if not request.is_json:
        return jsonify({'error': 'Send a JSON object containing ip, authorized, and optional max_hops'}), 415
    if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
        return jsonify({'error': 'Traceroute request is too large (maximum 1024 bytes)'}), 413
    raw = request.stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        return jsonify({'error': 'Traceroute request is too large (maximum 1024 bytes)'}), 413
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or not {'ip', 'authorized'} <= set(data)
                or set(data) - {'ip', 'authorized', 'max_hops'}):
            raise TraceRequestError('Provide ip, authorized, and optional max_hops only')
        result = trace_service.trace(data['ip'], data['authorized'], data.get('max_hops', 12))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({'error': 'The request is not valid JSON'}), 400
    except TraceRequestError as exc:
        return jsonify({'error': str(exc)}), exc.status
    response = jsonify(result)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.post('/api/check')
def manual_check():
    try:
        result = check_all(report=True)
    except MonitoringStopped:
        return jsonify({'error': 'Monitoring is stopping; no new checks were submitted'}), 503
    if result is False:
        return jsonify({'error': 'A monitoring cycle is already running; no duplicate checks were queued'}), 409
    if result['failed']:
        return jsonify(dict(result, error=f"{result['failed']} target check(s) failed to complete. See the local server log; no missing measurements were fabricated.")), 503
    return jsonify(dict(result, message='Checks completed'))


@app.post('/api/check-jobs')
def submit_check_job():
    try:
        job = coordinator.submit_job(load_targets, collect_measurement, persist_measurement)
    except MonitoringStopped:
        return jsonify({'error': 'Monitoring is stopping; no new checks were submitted'}), 503
    if job is False:
        return jsonify({'error': 'A monitoring cycle is already running; no duplicate checks were queued'}), 409
    response = jsonify(job)
    response.status_code = 202
    response.headers['Location'] = '/api/check-jobs/' + job['job_id']
    return response


@app.get('/api/check-jobs/<job_id>')
def check_job_status(job_id):
    job = coordinator.job_status(job_id)
    if job is None:
        return jsonify({'error': 'Monitoring job not found or no longer retained'}), 404
    return jsonify(job)


def incident_response(row):
    data = dict(row)
    try:
        for field in ('triggering_measurements','thresholds','recovery'):
            data[field] = json.loads(data[field]) if data[field] is not None else None
    except (ValueError,TypeError):
        raise sqlite3.DatabaseError('Invalid incident evidence') from None
    return data


@app.get('/api/incidents')
def list_incidents():
    allowed = {'target_id','condition','state','range','start','end','limit','before_id'}
    if set(request.args)-allowed or any(len(request.args.getlist(key))!=1 for key in request.args):
        return jsonify({'error':'Unsupported or repeated incident filter'}),400
    clauses, values = [], []
    try:
        if 'start' in request.args or 'end' in request.args:
            if 'range' in request.args or not {'start','end'} <= request.args.keys():
                raise ValueError('Provide start and end together, without range')
            start,end = (observation_time(request.args[key]) for key in ('start','end'))
            if not start or not end or not 0 < (datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds() <= 30*86400:
                raise ValueError('Incident window must be positive and at most 30 days, with timezone-aware timestamps')
        else:
            start,end = time_window(request.args.get('range','30d'))
        def integer(key, default, maximum):
            value = request.args.get(key,str(default))
            if not re.fullmatch(r'[1-9][0-9]{0,18}',value) or not 1<=int(value)<=maximum:
                raise ValueError('Invalid incident numeric filter')
            return int(value)
        limit = integer('limit',100,200)
        if 'target_id' in request.args:
            clauses.append('target_id=?'); values.append(integer('target_id',1,2**63-1))
        if 'before_id' in request.args:
            clauses.append('id<?'); values.append(integer('before_id',1,2**63-1))
        for field,choices in (('condition',CONDITIONS),('state',('OPEN','ACKNOWLEDGED','RESOLVED','ACTIVE'))):
            if field in request.args:
                if request.args[field] not in choices:
                    raise ValueError('Invalid incident condition or state')
                if field=='state' and request.args[field]=='ACTIVE':
                    clauses.append("state IN ('OPEN','ACKNOWLEDGED')")
                else:
                    clauses.append(field+'=?'); values.append(request.args[field])
    except ValueError as exc:
        return jsonify({'error':str(exc)}),400
    # Include incidents whose observed lifecycle overlaps the selected window.
    clauses.extend(['julianday(first_observed)<=julianday(?)',
                    '(resolved_at IS NULL OR julianday(resolved_at)>=julianday(?))'])
    values.extend([end,start,limit+1])
    with get_db() as conn:
        rows=conn.execute('SELECT * FROM incidents WHERE '+' AND '.join(clauses)+' ORDER BY id DESC LIMIT ?',values).fetchall()
    return jsonify(dict(start=start,end=end,incidents=[incident_response(row) for row in rows[:limit]],
                        next_before_id=rows[limit-1]['id'] if len(rows)>limit else None))


@app.get('/api/incidents/<int:incident_id>')
def incident_detail(incident_id):
    if incident_id>2**63-1:
        return jsonify({'error':'Incident not found'}),404
    with get_db() as conn:
        row=conn.execute('SELECT * FROM incidents WHERE id=?',(incident_id,)).fetchone()
    return (jsonify(incident_response(row)) if row else (jsonify({'error':'Incident not found'}),404))


@app.get('/api/incidents/summary')
def incident_summary():
    counts={condition:{'OPEN':0,'ACKNOWLEDGED':0} for condition in CONDITIONS}
    with get_db() as conn:
        rows=conn.execute("SELECT condition,state,COUNT(*) AS count FROM incidents WHERE state IN ('OPEN','ACKNOWLEDGED') GROUP BY condition,state").fetchall()
    for row in rows:
        counts[row['condition']][row['state']]=row['count']
    opened=sum(c['OPEN'] for c in counts.values())
    acknowledged=sum(c['ACKNOWLEDGED'] for c in counts.values())
    return jsonify(dict(open=opened,acknowledged=acknowledged,active=opened+acknowledged,by_condition=counts))


@app.post('/api/incidents/<int:incident_id>/acknowledge')
def acknowledge_incident(incident_id):
    if not request.is_json:
        return jsonify({'error':'Send a JSON object with an optional note'}),415
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or set(data)-{'note'}:
        return jsonify({'error':'Provide only an optional note in a JSON object'}),400
    note=data.get('note')
    if note is not None and (not isinstance(note,str) or len(note)>500 or
            any((ord(c)<32 and c not in '\n\r\t') or ord(c)==127 for c in note)):
        return jsonify({'error':'Note must be text of at most 500 characters without control characters'}),400
    if incident_id>2**63-1:
        return jsonify({'error':'Incident not found'}),404
    try:
        row=acknowledge(get_db,incident_id,note.strip() or None if note is not None else None,app.logger)
    except IncidentNotFound:
        return jsonify({'error':'Incident not found'}),404
    except IncidentConflict:
        return jsonify({'error':'A resolved incident cannot be newly acknowledged'}),409
    return jsonify(incident_response(row))


@app.get('/api/notifications')
def notification_events():
    if set(request.args)-{'after_id','before_id','limit'} or any(len(request.args.getlist(key))!=1 for key in request.args):
        return jsonify({'error':'Unsupported or repeated notification parameter'}),400
    try:
        def number(key,default,low,high):
            value=request.args.get(key,str(default))
            if not re.fullmatch(r'[0-9]{1,19}',value) or not low<=int(value)<=high:
                raise ValueError('Invalid notification cursor or limit')
            return int(value)
        limit=number('limit',50,1,200)
        after=number('after_id',0,0,2**63-1) if 'after_id' in request.args else None
        before=number('before_id',0,1,2**63-1) if 'before_id' in request.args else None
        if after is not None and before is not None:
            raise ValueError('Use after_id or before_id, not both')
    except ValueError as exc:
        return jsonify({'error':str(exc)}),400
    with get_db() as conn:
        data=recent_events(conn,limit,after,before)
    return jsonify(data)


def notification_read_body():
    if not request.is_json:
        return None,(jsonify({'error':'Send a JSON object'}),415)
    data=request.get_json(silent=True)
    if not isinstance(data,dict):
        return None,(jsonify({'error':'Expected a JSON object'}),400)
    return data,None


@app.post('/api/notifications/<int:event_id>/read')
def read_notification(event_id):
    data,error=notification_read_body()
    if error:return error
    if data:
        return jsonify({'error':'Send an empty JSON object'}),400
    if event_id>2**63-1:return jsonify({'error':'Notification not found'}),404
    with get_db() as conn:
        result=mark_read(conn,event_id=event_id)
    return jsonify(result) if result is not None else (jsonify({'error':'Notification not found'}),404)


@app.post('/api/notifications/read')
def read_notifications():
    data,error=notification_read_body()
    if error:return error
    if set(data)!={'up_to_id'} or type(data['up_to_id']) is not int or not 0<=data['up_to_id']<=2**63-1:
        return jsonify({'error':'Provide an integer up_to_id snapshot cursor'}),400
    with get_db() as conn:
        result=mark_read(conn,up_to_id=data['up_to_id'])
    return jsonify(result)


discovery_service = Discovery(get_db, validate_ip,
    lambda address, timeout: discovery_echo(address, timeout, parse_ping_output),
    lock, trace_service.slot, app.logger,
    timeout_ms=int(setting('NETSCOPE_DISCOVERY_TIMEOUT_MS', 500, 100, 1000)))


@app.errorhandler(DiscoveryError)
def discovery_error(error):
    return jsonify(error=str(error)), error.status


def discovery_body(keys):
    if not request.is_json:
        raise DiscoveryError('Expected a JSON object', 415)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != set(keys):
        raise DiscoveryError('Provide exactly: ' + ', '.join(keys))
    return data


@app.post('/api/discovery/jobs')
def start_discovery():
    data = discovery_body(('cidr', 'authorized'))
    job_id = discovery_service.submit(data['cidr'], data['authorized'])
    return jsonify(discovery_service.status(job_id)), 202, {'Location': '/api/discovery/jobs/' + job_id}


@app.get('/api/discovery/jobs/<job_id>')
def discovery_job(job_id):
    result = discovery_service.status(job_id)
    if result is None:
        raise DiscoveryError('Discovery job not found (jobs are process-local)', 404)
    return jsonify(result)


@app.post('/api/discovery/jobs/<job_id>/cancel')
def cancel_discovery(job_id):
    discovery_body(())
    if not discovery_service.cancel(job_id):
        raise DiscoveryError('Discovery job not found', 404)
    return jsonify(discovery_service.status(job_id))


@app.get('/api/discovery/inventory')
@app.get('/api/discovery/observations')
def discovered_inventory():
    if set(request.args) - {'limit', 'before_id'} or any(len(request.args.getlist(k)) != 1 for k in request.args):
        raise DiscoveryError('Use only limit and before_id once')
    values = {}
    for key, maximum in (('limit', 200), ('before_id', 2**63-1)):
        raw = request.args.get(key)
        if raw is not None:
            if not re.fullmatch(r'[0-9]{1,19}', raw) or not 1 <= int(raw) <= maximum:
                raise DiscoveryError('Invalid ' + key)
            values[key] = int(raw)
    with get_db() as conn:
        return jsonify(discovery_observations(conn, values.get('limit', 50), values.get('before_id'),
                       inventory=request.path.endswith('/inventory')))


@app.post('/api/discovery/observations/<int:observation_id>/enroll')
@target_mutation
def enroll_discovery(observation_id):
    data = discovery_body(('name', 'port'))
    try:
        if not isinstance(data['name'], str) or not 1 <= len(data['name'].strip()) <= 60:
            raise ValueError('Name must be between 1 and 60 characters')
        port = validate_port(data['port'])
    except ValueError as error:
        raise DiscoveryError(str(error)) from error
    if observation_id > 2**63-1:
        raise DiscoveryError('Observation not found', 404)
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM discovery_observations WHERE id=?', (observation_id,)).fetchone()
        if row is None:
            raise DiscoveryError('Observation not found or expired', 404)
        if row['classification'] != 'responding':
            raise DiscoveryError('Only an observed responding address can be enrolled here', 409)
        ip = validate_ip(row['address'])
        existing = conn.execute('SELECT id FROM targets WHERE ip=? ORDER BY id LIMIT 1', (ip,)).fetchone()
        if existing:
            return jsonify(error='Address is already monitored', target_id=existing['id']), 409
        result = conn.execute('INSERT INTO targets(name,ip,port,created_at) VALUES(?,?,?,?)',
                              (data['name'].strip(), ip, port, utc_now()))
    return jsonify(id=result.lastrowid), 201


if __name__ == '__main__':
    configure_logging(app.logger, BASE, include_server=True)
    app.logger.info('Application starting', extra={'event': 'application_starting'})
    try:
        init_db()
    except Exception:
        app.logger.exception('Application initialization failed', extra={'event': 'startup_failure', 'phase': 'startup'})
        coordinator.shutdown()
        raise
    # Use one process and no Flask reloader to avoid duplicate background monitors.
    monitor_thread = threading.Thread(target=monitoring_loop, name='netscope-monitor')
    monitor_thread.start()
    app.logger.info('Application started', extra={'event': 'application_started', 'workers': MONITOR_WORKERS})
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
    finally:
        app.logger.info('Application stopping', extra={'event': 'application_stopping'})
        coordinator.request_stop()
        discovery_service.shutdown()
        monitor_thread.join()
        coordinator.shutdown()
        app.logger.info('Application stopped', extra={'event': 'application_stopped'})
