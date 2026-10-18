"""Bounded, display-only system name resolution; never connect to results."""
import ipaddress
import re
import socket
import threading
import time
from collections import deque


LOCAL_SUFFIXES = ('localhost', 'local', 'localdomain', 'internal', 'lan', 'home',
                  'arpa', 'onion', 'corp', 'intranet', 'test', 'invalid')
MAX_REQUEST_BYTES = 1024
MAX_ADDRESSES = 32


class DNSRequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate_domain(value):
    if not isinstance(value, str) or not value or len(value) > 254:
        raise DNSRequestError('Enter a domain name of at most 253 characters, such as example.com.')
    value = value.strip()
    if value.endswith('.'):
        value = value[:-1]
    try:
        domain = value.encode('idna').decode('ascii').lower()
    except UnicodeError as exc:
        raise DNSRequestError('This domain name cannot be encoded. Check its spelling.') from exc
    labels = domain.split('.')
    if (not 1 <= len(domain) <= 253 or len(labels) < 2
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
            or labels[-1].isdigit() or len(labels[-1]) < 2):
        raise DNSRequestError('Use a complete domain name, without a URL, port, path, spaces, or IP address.')
    if any(domain == suffix or domain.endswith('.' + suffix) for suffix in LOCAL_SUFFIXES):
        raise DNSRequestError('Local-only and special-use names are not supported. Enter a public domain name.')
    return domain


def public_answers(entries):
    answers = {'a': [], 'aaaa': []}
    omitted, truncated = False, False
    for family, _, _, _, sockaddr in entries:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError, TypeError):
            continue
        mapped = getattr(address, 'ipv4_mapped', None)
        if (not address.is_global or address.is_multicast
                or (mapped is not None and not mapped.is_global)
                or '%' in str(address)):
            omitted = True
            continue
        key = 'a' if address.version == 4 else 'aaaa'
        value = str(address)
        if value not in answers[key]:
            if len(answers[key]) < MAX_ADDRESSES:
                answers[key].append(value)
            else:
                truncated = True
    return dict(answers, omitted_non_public=omitted, truncated=truncated)


def resolver_message(error):
    code = getattr(error, 'errno', None)
    if code in {socket.EAI_NONAME, getattr(socket, 'EAI_NODATA', socket.EAI_NONAME)}:
        return 'not_found', 'The system resolver found no address for this name. Check the spelling; the name may not exist or may have no A/AAAA records.'
    if code == socket.EAI_AGAIN:
        return 'temporary_failure', 'The resolver could not answer right now. Your network or configured DNS server may be unavailable; try again later.'
    return 'resolver_failure', 'The system resolver could not complete this lookup. Check your network and configured DNS server. This does not prove the domain is offline.'


class DNSDiagnostics:
    def __init__(self, timeout=5.0, requests_per_minute=6):
        self.timeout = timeout
        self.requests_per_minute = requests_per_minute
        self.slot = threading.Lock()
        self.quota_lock = threading.Lock()
        self.requests = deque()

    def lookup(self, value):
        domain = validate_domain(value)
        if not self.slot.acquire(blocking=False):
            raise DNSRequestError('A DNS lookup is still running. Wait before trying another name.', 429)
        now = time.monotonic()
        with self.quota_lock:
            while self.requests and now - self.requests[0] >= 60:
                self.requests.popleft()
            if len(self.requests) >= self.requests_per_minute:
                self.slot.release()
                raise DNSRequestError('DNS lookup limit reached (6 per minute). Wait one minute and try again.', 429)
            self.requests.append(now)
        ready, result = threading.Event(), {}
        started = time.perf_counter()

        def resolve():
            try:
                # Absolute name prevents resolver search-suffix expansion.
                # No port and no connection to any returned address.
                entries = socket.getaddrinfo(domain + '.', None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                result.update(public_answers(entries))
                if result['a'] or result['aaaa']:
                    result.update(ok=True, code='resolved', error=None)
                elif result['omitted_non_public']:
                    result.update(ok=False, code='restricted_results', error='The resolver returned only non-public addresses. They are withheld by the local-resource protection policy.')
                else:
                    result.update(ok=False, code='no_addresses', error='The lookup returned no IPv4 or IPv6 addresses. The name may have other DNS records but no usable A/AAAA address results.')
            except (socket.gaierror, OSError) as exc:
                code, message = resolver_message(exc)
                result.update(ok=False, code=code, error=message, a=[], aaaa=[])
            except Exception:
                result.update(ok=False, code='resolver_failure', error='The system resolver returned an unexpected result. Retry later or check your DNS configuration.', a=[], aaaa=[])
            finally:
                result.update(domain=domain, duration_ms=round((time.perf_counter() - started) * 1000, 2))
                # A timed-out OS resolver cannot be cancelled safely. Keep the
                # only slot occupied until it actually exits: no worker buildup.
                self.slot.release()
                ready.set()

        try:
            threading.Thread(target=resolve, daemon=True).start()
        except RuntimeError as exc:
            self.slot.release()
            raise DNSRequestError('The DNS worker could not start. Try again later.', 503) from exc
        if not ready.wait(self.timeout):
            return dict(domain=domain, ok=False, code='timeout', a=[], aaaa=[],
                        duration_ms=round((time.perf_counter() - started) * 1000, 2),
                        error='Stopped waiting for the system resolver after five seconds. It may still be resolving; another lookup is blocked until it finishes. Check your network or DNS server.')
        return result
