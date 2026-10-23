"""Explicit, bounded ICMP observations. Never schedules or enrolls targets."""
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import threading
import time
import uuid

MAX_HOSTS = 32
RETENTION = 2048
DDL = (
    """CREATE TABLE discovery_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,
        cidr TEXT NOT NULL, address TEXT NOT NULL, method TEXT NOT NULL CHECK(method='icmp_echo'),
        observed_at TEXT NOT NULL, classification TEXT NOT NULL
        CHECK(classification IN ('responding','unresponsive','unknown')),
        UNIQUE(scan_id,address))""",
    'CREATE INDEX idx_discovery_address ON discovery_observations(address,id DESC)',
)


def migrate(conn, install):
    if install:
        for sql in DDL:
            conn.execute(sql)
    for sql in DDL:
        row = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (sql.split()[2],)).fetchone()
        if row is None or ' '.join(row[0].split()) != ' '.join(sql.split()):
            raise sqlite3.DatabaseError('Unexpected discovery schema')


class DiscoveryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def scope(cidr, authorized, validator):
    if authorized is not True:
        raise DiscoveryError('Confirm authorization for this specific CIDR scan')
    if not isinstance(cidr, str) or '/' not in cidr or len(cidr) > 18:
        raise DiscoveryError('Enter a canonical private/loopback IPv4 CIDR')
    try:
        network = ipaddress.IPv4Network(cidr, strict=True)
        if str(network) != cidr:
            raise ValueError()
        validator(str(network.network_address))
        validator(str(network.broadcast_address))
    except ValueError as exc:
        raise DiscoveryError('Use an exact private/loopback IPv4 CIDR; host bits must be zero') from exc
    count = network.num_addresses if network.prefixlen >= 31 else network.num_addresses - 2
    if not 1 <= count <= MAX_HOSTS:
        raise DiscoveryError('A scan must contain 1 to 32 usable hosts')
    hosts = [validator(str(address)) for address in network.hosts()]
    return hosts


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def echo(address, timeout_ms, parser):
    """One echo, no DNS/ports; do not interpret missing replies as absence."""
    windows = platform.system() == 'Windows'
    if windows:
        command = [str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'ping.exe'),
                   '-n', '1', '-w', str(timeout_ms), address]
    else:
        command = ['ping', '-n', '-c', '1', address]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors='replace',
                                timeout=timeout_ms / 1000 + 1, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'
    if result.returncode not in (0, 1):
        return 'unknown'
    if parser(result.stdout)[0]:
        return 'responding'
    return 'unresponsive'


class Discovery:
    def __init__(self, db, validator, probe, admission, trace_slot, logger, timeout_ms=500, clock=time.monotonic):
        self.db, self.validator, self.probe = db, validator, probe
        self.admission, self.trace_slot, self.logger = admission, trace_slot, logger
        self.timeout_ms, self.clock = timeout_ms, clock
        self.guard = threading.Lock()
        self.jobs, self.requests = OrderedDict(), deque()
        self.active = None
        self.thread = None
        self.stopping = False

    def status(self, job_id):
        with self.guard:
            job = self.jobs.get(job_id)
            return {key: value for key, value in job.items() if key != 'cancel'} if job else None

    def submit(self, cidr, authorized):
        hosts = scope(cidr, authorized, self.validator)
        with self.guard:
            if self.stopping:
                raise DiscoveryError('Discovery is stopping', 503)
            if self.active is not None:
                raise DiscoveryError('A discovery scan is already active', 409)
            stamp = self.clock()
            while self.requests and stamp - self.requests[0] >= 60:
                self.requests.popleft()
            if len(self.requests) >= 2:
                raise DiscoveryError('At most two accepted discovery scans per minute', 429)
            if not self.admission.acquire(blocking=False):
                raise DiscoveryError('Monitoring is active; try again after it finishes', 409)
            if not self.trace_slot.acquire(blocking=False):
                self.admission.release()
                raise DiscoveryError('Traceroute is active; try again later', 409)
            job_id = uuid.uuid4().hex
            job = dict(job_id=job_id, cidr=cidr, state='queued', total=len(hosts), completed=0,
                       failed=0, progress_percentage=0, started_at=None, ended_at=None,
                       error=None, cancel=threading.Event())
            self.jobs[job_id] = job
            self.active = job_id
            self.requests.append(stamp)
            while len(self.jobs) > 32:
                self.jobs.popitem(last=False)
            self.thread = threading.Thread(target=self._run, args=(job, hosts), name='netscope-discovery')
            try:
                self.thread.start()
            except RuntimeError:
                job.update(state='failed', error='Discovery could not start', ended_at=now())
                self.active = None
                self.trace_slot.release()
                self.admission.release()
                raise DiscoveryError('Discovery could not start', 503)
            return job_id

    def cancel(self, job_id):
        with self.guard:
            job = self.jobs.get(job_id)
            if job:
                job['cancel'].set()
            return job is not None

    def _observe(self, job, address):
        if job['cancel'].is_set():
            return None
        classification = self.probe(address, self.timeout_ms)
        if classification not in ('responding', 'unresponsive', 'unknown'):
            raise ValueError('Invalid discovery outcome')
        return address, classification, now()

    def _persist(self, job, outcome):
        address, classification, observed = outcome
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''INSERT INTO discovery_observations
                (scan_id,cidr,address,method,observed_at,classification) VALUES(?,?,?,'icmp_echo',?,?)''',
                         (job['job_id'], job['cidr'], address, observed, classification))
            conn.execute('''DELETE FROM discovery_observations WHERE id IN
                (SELECT id FROM discovery_observations ORDER BY id DESC LIMIT -1 OFFSET ?)''', (RETENTION,))

    def _run(self, job, hosts):
        with self.guard:
            job.update(state='running', started_at=now())
        try:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='netscope-discovery-probe') as pool:
                futures = [pool.submit(self._observe, job, address) for address in hosts]
                for future in as_completed(futures):
                    try:
                        outcome = future.result()
                        if outcome is None:
                            continue
                        self._persist(job, outcome)
                        with self.guard:
                            job['completed'] += 1
                            if outcome[1] == 'unknown':
                                job['failed'] += 1
                    except Exception:
                        self.logger.exception('Discovery observation failed', extra={'event': 'discovery_failure'})
                        with self.guard:
                            job['completed'] += 1
                            job['failed'] += 1
                    with self.guard:
                        job['progress_percentage'] = round(job['completed'] * 100 / job['total'], 1)
        except Exception:
            self.logger.exception('Discovery job failed', extra={'event': 'discovery_failure'})
            with self.guard:
                job.update(error='Discovery execution failed; see local logs', state='failed')
        finally:
            with self.guard:
                if job['state'] != 'failed':
                    job['state'] = 'cancelled' if job['cancel'].is_set() else ('failed' if job['failed'] else 'completed')
                if job['failed'] and not job['error']:
                    job['error'] = 'Some observations could not execute or persist; see local logs'
                job['ended_at'] = now()
                self.active = None
                self.trace_slot.release()
                self.admission.release()

    def shutdown(self):
        with self.guard:
            self.stopping = True
            if self.active:
                self.jobs[self.active]['cancel'].set()
            thread = self.thread
        if thread:
            thread.join()


def observations(conn, limit, before=None, inventory=False):
    where, values = [], []
    if inventory:
        where.append('d.id IN (SELECT MAX(id) FROM discovery_observations GROUP BY address)')
    if before is not None:
        where.append('d.id<?')
        values.append(before)
    sql = 'SELECT d.* FROM discovery_observations d'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    rows = conn.execute(sql + ' ORDER BY d.id DESC LIMIT ?', (*values, limit+1)).fetchall()
    return dict(observations=[dict(row) for row in rows[:limit]],
                next_before_id=rows[limit-1]['id'] if len(rows)>limit else None)
