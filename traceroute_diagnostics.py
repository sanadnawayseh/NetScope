"""Manual Windows tracert diagnostics, isolated from monitoring and SQLite."""
import ipaddress
import os
import platform
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

MAX_HOPS = 20
DEFAULT_HOPS = 12
PROBE_TIMEOUT_MS = 500
MAX_OUTPUT_CHARS = 65536
TIMEOUT_NOTE = 'A * means that probe received no reply within its wait. Routers may filter or deprioritize these replies; it does not prove a router or network is down.'


class TraceRequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def output_text(value):
    return value.decode(errors='replace') if isinstance(value, bytes) else value or ''


def parse_tracert(output, destination, max_hops=MAX_HOPS):
    """Read numbered hop rows and three reported measurements, not the footer."""
    output = output_text(output)
    if len(output) > MAX_OUTPUT_CHARS:
        return dict(status='command_failure', hops=[], message='Trace output exceeded the supported size limit.')
    hops, malformed = [], False
    measurement = r'(\*|<\s*\d+(?:[.,]\d+)?\s*ms|\d+(?:[.,]\d+)?\s*ms)'
    pattern = re.compile(r'^\s*(\d{1,2})\s+' + measurement + r'\s+' + measurement + r'\s+' + measurement + r'\s*(.*?)\s*$', re.IGNORECASE)
    for line in output.splitlines():
        if not re.match(r'^\s*\d+\s', line):
            continue
        match = pattern.fullmatch(line)
        if not match:
            malformed = True
            continue
        number = int(match[1])
        if number != len(hops) + 1 or number > max_hops:
            malformed = True
            continue
        rtts = [re.sub(r'\s+', '', match[i]).lower().replace(',', '.') for i in (2,3,4)]
        rtts = [value[:-2] + ' ms' if value.endswith('ms') else value for value in rtts]
        tail = match[5].strip()
        try:
            address = str(ipaddress.IPv4Address(tail))
        except ipaddress.AddressValueError:
            address = None
        if address is None and any(value != '*' for value in rtts):
            malformed = True
            continue
        if address is not None and all(value == '*' for value in rtts):
            malformed = True
            continue
        # Timeout text may be localized. Never turn it into a responding IP.
        if address is None and re.search(r'\d+\.\d+\.\d+\.\d+', tail):
            malformed = True
            continue
        hops.append(dict(hop=number, ip=address, rtts=rtts))
    if not hops:
        return dict(status='command_failure', hops=[], message='No usable hop rows were returned. The command failed or its output could not be understood.')
    reached = not malformed and hops[-1]['ip'] == destination
    return dict(status='reached' if reached else 'incomplete', hops=hops,
                message='Destination reached.' if reached else 'Destination was not confirmed within this trace. Hop limits, filtered replies, or routing conditions may prevent completion.',
                parse_warning='Some hop output was malformed and was excluded.' if malformed else None)


class TraceDiagnostics:
    def __init__(self, validator):
        self.validator = validator
        self.slot = threading.Lock()
        self.quota_lock = threading.Lock()
        self.requests = deque()

    def trace(self, ip, authorized=False, max_hops=DEFAULT_HOPS):
        if authorized is not True:
            raise TraceRequestError('Confirm that you own or have permission to trace this specific destination.', 403)
        if not isinstance(ip, str) or len(ip) > 15:
            raise TraceRequestError('Enter a literal loopback or RFC1918 private IPv4 address.')
        try:
            destination = self.validator(ip.strip())
        except (ValueError, TypeError) as exc:
            raise TraceRequestError(str(exc)) from exc
        if type(max_hops) is not int or not 1 <= max_hops <= MAX_HOPS:
            raise TraceRequestError('Maximum hops must be an integer between 1 and 20.')
        if platform.system().lower() != 'windows':
            raise TraceRequestError('This diagnostic currently requires Windows tracert.', 501)
        if not self.slot.acquire(blocking=False):
            raise TraceRequestError('A traceroute is already running. Wait for it to finish.', 429)
        started = time.perf_counter()
        try:
            now = time.monotonic()
            with self.quota_lock:
                while self.requests and now-self.requests[0] >= 60:
                    self.requests.popleft()
                if len(self.requests) >= 2:
                    raise TraceRequestError('Traceroute limit reached (2 per minute). Wait one minute and try again.', 429)
                self.requests.append(now)
            executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'tracert.exe')
            command = [executable, '-d', '-4', '-h', str(max_hops), '-w', str(PROBE_TIMEOUT_MS), destination]
            deadline = min(35, max_hops * 3 * PROBE_TIMEOUT_MS / 1000 + 5)
            try:
                completed = subprocess.run(command, capture_output=True, text=True, errors='replace',
                                           timeout=deadline, check=False, shell=False)
                result = parse_tracert(completed.stdout, destination, max_hops)
                if completed.returncode != 0:
                    result.update(status='command_failure', message='Windows tracert exited with an error. Check the destination and local network configuration.')
            except subprocess.TimeoutExpired as exc:
                result = parse_tracert(exc.stdout, destination, max_hops)
                result.update(status='incomplete', message='The trace reached its execution time limit and was stopped. Any rows shown are partial results; the destination was not confirmed.')
            except OSError:
                result = dict(status='command_failure', hops=[], message='Windows tracert could not be started. Check that the system command is installed and permitted.')
            result.update(destination=destination, max_hops=max_hops, probe_timeout_ms=PROBE_TIMEOUT_MS,
                          duration_ms=round((time.perf_counter()-started)*1000, 2), note=TIMEOUT_NOTE)
            return result
        finally:
            self.slot.release()
