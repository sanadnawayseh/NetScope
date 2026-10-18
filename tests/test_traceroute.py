import json
import subprocess
import unittest
from unittest.mock import patch

import app as netscope
import traceroute_diagnostics as trace
import test_app

ONE = '''Tracing route to 127.0.0.1 over a maximum of 12 hops
  1    <1 ms    <1 ms    <1 ms  127.0.0.1
Trace complete.
'''
MULTI = '''Tracing route to 10.1.2.3 over a maximum of 12 hops
  1     1 ms     2 ms     1 ms  192.168.1.1
  2     *        *        *     Request timed out.
  3    12 ms     *       14 ms  10.1.2.3
Trace complete.
'''


class TraceParserTests(unittest.TestCase):
    def test_one_hop_success_preserves_submillisecond_bounds(self):
        result=trace.parse_tracert(ONE,'127.0.0.1')
        self.assertEqual(result['status'],'reached')
        self.assertEqual(result['hops'],[{'hop':1,'ip':'127.0.0.1','rtts':['<1 ms']*3}])

    def test_multihop_success_with_timed_out_intermediate_hop(self):
        result=trace.parse_tracert(MULTI,'10.1.2.3')
        self.assertEqual(result['status'],'reached')
        self.assertEqual(len(result['hops']),3)
        self.assertEqual(result['hops'][1],{'hop':2,'ip':None,'rtts':['*']*3})
        self.assertEqual(result['hops'][2]['rtts'],['12 ms','*','14 ms'])
        self.assertNotIn('down',result['message'])

    def test_all_timeouts_is_incomplete_not_command_failure(self):
        result=trace.parse_tracert('  1 * * * Request timed out.\n  2 * * * Request timed out.\nTrace complete.','10.1.2.3')
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(result['hops']),2)
        self.assertTrue(all(h['ip'] is None for h in result['hops']))

    def test_footer_and_destination_in_header_do_not_prove_reached(self):
        result=trace.parse_tracert('Tracing route to 10.1.2.3\n  1 1 ms 2 ms 3 ms 192.168.1.1\nTrace complete.','10.1.2.3')
        self.assertEqual(result['status'],'incomplete')

    def test_malformed_output_never_creates_fake_hops(self):
        for output in ('', 'nonsense', '  1 banana 127.0.0.1',
                       '  1 1 ms 2 ms 127.0.0.1','  1 1 ms 2 ms 3 ms 999.1.1.1',
                       '  1 * * * 127.0.0.1', '  1 127.0.0.1 reports: Destination host unreachable.',
                       '  0 1 ms 2 ms 3 ms 127.0.0.1', 'Trace complete.'):
            with self.subTest(output=output):
                result=trace.parse_tracert(output,'127.0.0.1')
                self.assertEqual(result['status'],'command_failure')
                self.assertEqual(result['hops'],[])

    def test_valid_prefix_and_malformed_row_remain_incomplete(self):
        result=trace.parse_tracert('  1 1 ms 2 ms 3 ms 192.168.1.1\n  2 malformed\n  3 1 ms 2 ms 3 ms 10.1.2.3','10.1.2.3')
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(result['hops']),1)
        self.assertIn('malformed',result['parse_warning'])

    def test_hop_limits_and_output_size_are_bounded(self):
        result=trace.parse_tracert(MULTI,'10.1.2.3',max_hops=2)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(len(result['hops']),2)
        self.assertEqual(trace.parse_tracert('a'*65537,'127.0.0.1')['status'],'command_failure')

    def test_localized_timeout_text_does_not_become_an_ip(self):
        result=trace.parse_tracert('  1 * * * Zeitüberschreitung der Anforderung.','127.0.0.1')
        self.assertEqual(result['status'],'incomplete');self.assertIsNone(result['hops'][0]['ip'])


class TraceExecutionTests(unittest.TestCase):
    def test_strict_command_and_timeout_no_shell_or_dns(self):
        service=trace.TraceDiagnostics(netscope.validate_ip)
        with patch.object(trace.platform,'system',return_value='Windows'),patch.object(trace.subprocess,'run',return_value=subprocess.CompletedProcess([],0,ONE,'')) as run:
            result=service.trace('127.0.0.1',True,20)
        command=run.call_args.args[0]
        self.assertTrue(command[0].lower().endswith('system32\\tracert.exe'))
        self.assertEqual(command[1:],['-d','-4','-h','20','-w','500','127.0.0.1'])
        self.assertIs(run.call_args.kwargs['shell'],False)
        self.assertEqual(run.call_args.kwargs['timeout'],35)
        self.assertEqual(result['status'],'reached')
        self.assertFalse(service.slot.locked())

    def test_public_invalid_and_unauthorized_targets_never_execute(self):
        bad=['8.8.8.8','169.254.169.254','0.0.0.0','224.0.0.1','192.0.2.1','::1',
             'example.com','127.0.0.1 & whoami','127.0.0.1\nwhoami',123,None]
        service=trace.TraceDiagnostics(netscope.validate_ip)
        with patch.object(trace.subprocess,'run') as run:
            for value in bad:
                with self.subTest(value=value),self.assertRaises(trace.TraceRequestError):
                    service.trace(value,True)
            for consent in (False,None,1,'true'):
                with self.subTest(consent=consent),self.assertRaises(trace.TraceRequestError) as error:
                    service.trace('127.0.0.1',consent)
                self.assertEqual(error.exception.status,403)
            run.assert_not_called()

    def test_invalid_hop_limits_never_execute(self):
        service=trace.TraceDiagnostics(netscope.validate_ip)
        with patch.object(trace.subprocess,'run') as run:
            for value in (0,21,True,1.5,'12',None):
                with self.subTest(value=value),self.assertRaises(trace.TraceRequestError):
                    service.trace('127.0.0.1',True,value)
            run.assert_not_called()

    def test_non_windows_is_explicitly_unsupported(self):
        with patch.object(trace.platform,'system',return_value='Linux'),self.assertRaises(trace.TraceRequestError) as error:
            trace.TraceDiagnostics(netscope.validate_ip).trace('127.0.0.1',True)
        self.assertEqual(error.exception.status,501)

    def test_execution_timeout_retains_partial_hops_without_claiming_success(self):
        service=trace.TraceDiagnostics(netscope.validate_ip)
        error=subprocess.TimeoutExpired('tracert',35,output=ONE.encode())
        with patch.object(trace.platform,'system',return_value='Windows'),patch.object(trace.subprocess,'run',side_effect=error):
            result=service.trace('127.0.0.1',True)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['hops'][0]['ip'],'127.0.0.1')
        self.assertIn('time limit',result['message'])
        self.assertFalse(service.slot.locked())

    def test_nonzero_exit_or_missing_command_is_command_failure(self):
        for response in (subprocess.CompletedProcess([],1,ONE,'error'),OSError('sensitive details')):
            service=trace.TraceDiagnostics(netscope.validate_ip)
            with patch.object(trace.platform,'system',return_value='Windows'),patch.object(trace.subprocess,'run',side_effect=response if isinstance(response,Exception) else None,return_value=response):
                result=service.trace('127.0.0.1',True)
            self.assertEqual(result['status'],'command_failure')
            self.assertNotIn('sensitive',str(result))
            self.assertFalse(service.slot.locked())

    def test_busy_and_rate_limit_do_not_queue_jobs(self):
        service=trace.TraceDiagnostics(netscope.validate_ip)
        with patch.object(trace.platform,'system',return_value='Windows'),patch.object(trace.subprocess,'run',return_value=subprocess.CompletedProcess([],0,ONE,'')) as run,patch.object(trace.time,'monotonic',return_value=100):
            service.slot.acquire()
            try:
                with self.assertRaises(trace.TraceRequestError) as error:
                    service.trace('127.0.0.1',True)
                self.assertEqual(error.exception.status,429)
            finally:
                service.slot.release()
            service.trace('127.0.0.1',True);service.trace('127.0.0.1',True)
            with self.assertRaises(trace.TraceRequestError) as error:
                service.trace('127.0.0.1',True)
            self.assertEqual(error.exception.status,429)
            self.assertEqual(run.call_count,2)
            self.assertFalse(service.slot.locked())
        with patch.object(trace.platform,'system',return_value='Windows'),patch.object(trace.subprocess,'run',return_value=subprocess.CompletedProcess([],0,ONE,'')),patch.object(trace.time,'monotonic',return_value=161):
            self.assertEqual(service.trace('127.0.0.1',True)['status'],'reached')


class TraceEndpointTests(unittest.TestCase):
    setUp=test_app.ApplicationTests.setUp
    tearDown=test_app.ApplicationTests.tearDown

    def post(self,**options):
        return self.client.post('/api/traceroute',json={'ip':'127.0.0.1','authorized':True,'max_hops':12},**options)

    def test_endpoint_does_not_alter_monitoring_dns_or_sqlite(self):
        before=self.client.get('/api/targets').json
        with patch.object(netscope,'trace_service',trace.TraceDiagnostics(netscope.validate_ip)),patch.object(trace.platform,'system',return_value='Windows'),patch.object(trace.subprocess,'run',return_value=subprocess.CompletedProcess([],0,ONE,'')),patch.object(netscope,'run_check') as monitor,patch.object(netscope.dns_service,'lookup') as dns:
            response=self.post()
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'reached')
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/api/targets').json,before)
        self.assertEqual(self.client.get('/api/targets/1/history').json,[])
        monitor.assert_not_called();dns.assert_not_called()

    def test_local_peer_origin_and_host_boundary(self):
        with patch.object(trace.subprocess,'run') as run:
            self.assertEqual(self.post(environ_overrides={'REMOTE_ADDR':'192.168.1.2'}).status_code,403)
            self.assertEqual(self.post(headers={'Origin':'https://attacker.example'}).status_code,403)
            self.assertEqual(self.post(headers={'Host':'attacker.example'}).status_code,403)
            run.assert_not_called()

    def test_bad_requests_and_missing_authorization_do_not_execute(self):
        with patch.object(trace.subprocess,'run') as run:
            for body in ({},[],None,{'ip':'127.0.0.1'}, {'ip':'127.0.0.1','authorized':True,'command':'whoami'}, {'ip':'8.8.8.8','authorized':True}):
                with self.subTest(body=body):
                    self.assertEqual(self.client.post('/api/traceroute',data=json.dumps(body),content_type='application/json').status_code,400)
            self.assertEqual(self.client.post('/api/traceroute',json={'ip':'127.0.0.1','authorized':False}).status_code,403)
            self.assertEqual(self.client.post('/api/traceroute',data='{',content_type='application/json').status_code,400)
            self.assertEqual(self.client.post('/api/traceroute',data='x=y').status_code,415)
            self.assertEqual(self.client.post('/api/traceroute',json={'ip':'a'*2000,'authorized':True}).status_code,413)
            run.assert_not_called()
