import socket
import subprocess
import threading
import unittest
from unittest.mock import patch

import app as netscope
import dns_diagnostics as dns
import test_app


def answer(ip, family=socket.AF_INET):
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip,0) if family==socket.AF_INET else (ip,0,0,0))


class DomainValidationTests(unittest.TestCase):
    def test_valid_domains_normalized_and_idna_encoded(self):
        for value,expected in [('Example.COM','example.com'),(' example.com. ','example.com'),
                               ('www.example.com','www.example.com'),('bücher.de','xn--bcher-kva.de')]:
            with self.subTest(value=value):
                self.assertEqual(dns.validate_domain(value),expected)

    def test_invalid_domains_are_rejected_before_resolution(self):
        bad=[None,123,[],{},'', '   ','localhost','printer.local','foo.localhost',
             'metadata.google.internal','router.home.arpa','device.lan','host.onion',
             '127.0.0.1','8.8.8.8','::1','169.254.169.254','2130706433',
             'https://example.com','example.com:80','example.com/path','user@example.com',
             'example.com?x=1','example.com; whoami','example.com\n.local','a b.com',
             '*.example.com','_service.example.com','-bad.com','bad-.com','a..com',
             'example.com..','a'*64+'.com','a'*255+'.com','test.123']
        with patch.object(dns.socket,'getaddrinfo') as resolver:
            for value in bad:
                with self.subTest(value=value),self.assertRaises(dns.DNSRequestError):
                    dns.DNSDiagnostics().lookup(value)
            resolver.assert_not_called()


class ResolverTests(unittest.TestCase):
    def test_system_resolver_absolute_name_a_and_aaaa_deduplicated(self):
        entries=[answer('8.8.8.8'),answer('8.8.8.8'),answer('2606:4700:4700::1111',socket.AF_INET6)]
        with patch.object(dns.socket,'getaddrinfo',return_value=entries) as resolver, patch.object(dns.socket,'create_connection') as connect, patch.object(subprocess,'run') as command:
            result=dns.DNSDiagnostics().lookup('Example.COM')
        resolver.assert_called_once_with('example.com.',None,socket.AF_UNSPEC,socket.SOCK_STREAM)
        connect.assert_not_called();command.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertEqual(result['a'],['8.8.8.8'])
        self.assertEqual(result['aaaa'],['2606:4700:4700::1111'])
        self.assertGreaterEqual(result['duration_ms'],0)
        self.assertEqual(result['domain'],'example.com')

    def test_one_family_can_be_absent(self):
        with patch.object(dns.socket,'getaddrinfo',return_value=[answer('8.8.8.8')]):
            result=dns.DNSDiagnostics().lookup('example.com')
        self.assertTrue(result['ok']);self.assertEqual(result['aaaa'],[])

    def test_empty_result_is_explicit_failure(self):
        with patch.object(dns.socket,'getaddrinfo',return_value=[]):
            result=dns.DNSDiagnostics().lookup('example.com')
        self.assertFalse(result['ok']);self.assertEqual(result['code'],'no_addresses')
        self.assertEqual(result['a'],[]);self.assertEqual(result['aaaa'],[])
        self.assertIn('no IPv4 or IPv6',result['error'])

    def test_failed_resolution_is_explained_without_raw_os_details(self):
        for exception,expected in [(socket.gaierror(socket.EAI_NONAME,'private machine information'),'not_found'),
                                   (socket.gaierror(socket.EAI_AGAIN,'raw detail'),'temporary_failure'),
                                   (OSError('sensitive path detail'),'resolver_failure')]:
            with self.subTest(expected=expected),patch.object(dns.socket,'getaddrinfo',side_effect=exception):
                result=dns.DNSDiagnostics().lookup('example.com')
                self.assertFalse(result['ok']);self.assertEqual(result['code'],expected)
                self.assertGreaterEqual(result['duration_ms'],0)
                self.assertNotIn('detail',result['error'])

    def test_non_public_and_metadata_addresses_are_withheld(self):
        values=['127.0.0.1','10.0.0.1','192.168.1.1','169.254.169.254','100.64.0.1','0.0.0.0','224.0.0.1','192.0.2.1']
        values6=['::1','fc00::1','fe80::1','::ffff:127.0.0.1']
        entries=[answer(ip) for ip in values]+[answer(ip,socket.AF_INET6) for ip in values6]
        with patch.object(dns.socket,'getaddrinfo',return_value=entries):
            result=dns.DNSDiagnostics().lookup('example.com')
        self.assertFalse(result['ok']);self.assertEqual(result['code'],'restricted_results')
        self.assertEqual(result['a'],[]);self.assertEqual(result['aaaa'],[])
        self.assertNotIn('169.254.169.254',str(result))

    def test_mixed_result_only_returns_public_addresses(self):
        with patch.object(dns.socket,'getaddrinfo',return_value=[answer('127.0.0.1'),answer('8.8.8.8')]):
            result=dns.DNSDiagnostics().lookup('example.com')
        self.assertTrue(result['ok']);self.assertTrue(result['omitted_non_public'])
        self.assertEqual(result['a'],['8.8.8.8'])

    def test_response_address_count_is_bounded(self):
        with patch.object(dns.socket,'getaddrinfo',return_value=[answer(f'8.8.8.{i}') for i in range(1,60)]):
            result=dns.DNSDiagnostics().lookup('example.com')
        self.assertEqual(len(result['a']),32);self.assertTrue(result['truncated'])

    def test_timeout_holds_one_worker_slot_until_os_resolution_finishes(self):
        release=threading.Event()
        service=dns.DNSDiagnostics(timeout=.01)
        def slow(*args):
            release.wait(2)
            return []
        with patch.object(dns.socket,'getaddrinfo',side_effect=slow) as resolver:
            try:
                result=service.lookup('example.com')
                self.assertEqual(result['code'],'timeout')
                with self.assertRaises(dns.DNSRequestError) as error:
                    service.lookup('example.org')
                self.assertEqual(error.exception.status,429)
                self.assertEqual(resolver.call_count,1)
            finally:
                release.set()
                self.assertTrue(service.slot.acquire(timeout=2))
                service.slot.release()

    def test_rate_limit_and_expiry(self):
        service=dns.DNSDiagnostics(requests_per_minute=2)
        with patch.object(dns.socket,'getaddrinfo',return_value=[]),patch.object(dns.time,'monotonic',return_value=100):
            service.lookup('example.com');service.lookup('example.org')
            with self.assertRaises(dns.DNSRequestError) as error:
                service.lookup('example.net')
            self.assertEqual(error.exception.status,429)
            self.assertFalse(service.slot.locked())
        with patch.object(dns.socket,'getaddrinfo',return_value=[]),patch.object(dns.time,'monotonic',return_value=161):
            self.assertEqual(service.lookup('example.net')['code'],'no_addresses')


class DNSEndpointTests(unittest.TestCase):
    setUp=test_app.ApplicationTests.setUp
    tearDown=test_app.ApplicationTests.tearDown

    def post(self,**options):
        return self.client.post('/api/dns',json={'domain':'example.com'},**options)

    def test_endpoint_returns_results_without_changing_sqlite_or_monitoring(self):
        before=self.client.get('/api/targets').json
        with patch.object(netscope,'dns_service',dns.DNSDiagnostics()),patch.object(dns.socket,'getaddrinfo',return_value=[answer('8.8.8.8')]),patch.object(netscope,'run_check') as monitor:
            response=self.post()
        self.assertEqual(response.status_code,200);self.assertTrue(response.json['ok'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/api/targets').json,before)
        self.assertEqual(self.client.get('/api/targets/1/history').json,[])
        monitor.assert_not_called()
        for value in ('example.com','8.8.8.8'):
            self.assertEqual(self.client.post('/api/targets',json={'name':'DNS target','ip':value,'port':80}).status_code,400)

    def test_invalid_json_and_input_never_call_resolver(self):
        with patch.object(dns.socket,'getaddrinfo') as resolver:
            for body in (None,[],{}, {'domain':None},{'domain':'http://example.com'},{'domain':'example.com','resolver':'127.0.0.1'}):
                with self.subTest(body=body):
                    response=self.client.post('/api/dns',data=__import__('json').dumps(body),content_type='application/json')
                    self.assertEqual(response.status_code,400)
            self.assertEqual(self.client.post('/api/dns',data='{',content_type='application/json').status_code,400)
            self.assertEqual(self.client.post('/api/dns',data='domain=example.com').status_code,415)
            resolver.assert_not_called()

    def test_request_size_limit(self):
        with patch.object(dns.socket,'getaddrinfo') as resolver:
            response=self.client.post('/api/dns',json={'domain':'a'*2000})
            self.assertEqual(response.status_code,413);resolver.assert_not_called()

    def test_nonlocal_peer_rebinding_host_and_cross_origin_blocked(self):
        with patch.object(dns.socket,'getaddrinfo') as resolver:
            self.assertEqual(self.post(environ_overrides={'REMOTE_ADDR':'192.168.1.2'}).status_code,403)
            self.assertEqual(self.post(headers={'Host':'attacker.example'}).status_code,403)
            self.assertEqual(self.post(headers={'Origin':'https://attacker.example'}).status_code,403)
            resolver.assert_not_called()

    def test_same_origin_is_allowed(self):
        with patch.object(netscope,'dns_service',dns.DNSDiagnostics()),patch.object(dns.socket,'getaddrinfo',return_value=[]):
            response=self.post(headers={'Origin':'http://localhost'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['code'],'no_addresses')

    def test_busy_is_json_rate_limit_response(self):
        service=dns.DNSDiagnostics();service.slot.acquire()
        try:
            with patch.object(netscope,'dns_service',service):
                response=self.post()
            self.assertEqual(response.status_code,429)
            self.assertIn('still running',response.json['error'])
        finally:
            service.slot.release()
