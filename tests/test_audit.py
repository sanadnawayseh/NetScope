import ast
import ipaddress
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import app as netscope
import test_app
import traceroute_diagnostics as trace
from test_packet_loss import windows_batch


class AuditBoundaryTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def test_all_read_routes_reject_remote_peers_and_rebinding_hosts(self):
        for path in ('/', '/api/targets', '/api/targets/1/history'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path,headers={'Host':'attacker.example'}).status_code,403)
                self.assertEqual(self.client.get(path,environ_overrides={'REMOTE_ADDR':'192.168.1.9'}).status_code,403)

    def test_monitoring_mutations_reject_cross_origin_before_work(self):
        with patch.object(netscope,'collect_measurement') as check:
            self.assertEqual(self.client.post('/api/check',headers={'Origin':'https://attacker.example'}).status_code,403)
            self.assertEqual(self.client.post('/api/targets',json={'name':'Bad origin','ip':'127.0.0.1'},headers={'Origin':'null'}).status_code,403)
            self.assertEqual(self.client.delete('/api/targets/1',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
            check.assert_not_called()
        self.assertEqual(len(self.client.get('/api/targets').json),1)

    def test_api_body_limit_and_safe_json_errors(self):
        self.assertEqual(self.client.post('/api/targets',json={'name':'a'*5000,'ip':'127.0.0.1'}).status_code,413)
        with patch.object(netscope,'collect_measurement') as check:
            self.assertEqual(self.client.post('/api/check',data='x'*5000).status_code,413)
            check.assert_not_called()
        for port in (True,False,80.5,float('inf'),[],{},'1e2','80.0'):
            with self.subTest(port=port):
                response=self.client.post('/api/targets',json={'name':'Invalid port','ip':'127.0.0.1','port':port})
                self.assertEqual(response.status_code,400)
                self.assertIn('error',response.json)
        self.assertEqual(self.client.post('/api/targets',json={'name':'Valid','ip':'127.0.0.1','port':'80'}).status_code,201)
        self.assertEqual(self.client.post('/api/targets',json={'name':None,'ip':'127.0.0.1'}).status_code,400)

    def test_mutations_during_cycle_cannot_delete_or_reuse_target_id(self):
        netscope.lock.acquire()
        try:
            self.assertEqual(self.client.delete('/api/targets/1').status_code,409)
            self.assertEqual(self.client.post('/api/targets',json={'name':'Replacement','ip':'127.0.0.2','port':80}).status_code,409)
        finally:
            netscope.lock.release()
        targets=self.client.get('/api/targets').json
        self.assertEqual(len(targets),1)
        self.assertEqual(targets[0]['ip'],'127.0.0.1')

    def test_intentionally_empty_device_list_stays_empty_on_restart(self):
        self.assertEqual(self.client.delete('/api/targets/1').status_code,204)
        netscope.init_db()
        self.assertEqual(self.client.get('/api/targets').json,[])

    def test_failed_cycle_is_not_reported_as_completed(self):
        with patch.object(netscope,'collect_measurement',side_effect=RuntimeError('failed probe')),patch.object(netscope.app.logger,'exception'):
            response=self.client.post('/api/check')
        self.assertEqual(response.status_code,503)
        self.assertIn('failed',response.json['error'])
        self.assertFalse(netscope.lock.locked())

    def test_database_failure_has_safe_json_and_does_not_reset_data(self):
        with patch.object(netscope,'get_db',side_effect=sqlite3.OperationalError('private/path/secret.db')),patch.object(netscope.app.logger,'exception'):
            response=self.client.get('/api/targets')
        self.assertEqual(response.status_code,503)
        self.assertIn('error',response.json)
        self.assertNotIn('secret.db',response.get_data(as_text=True))
        self.assertEqual(len(self.client.get('/api/targets').json),1)

    def test_headers_prevent_embedding_and_api_caching(self):
        response=self.client.get('/')
        self.assertEqual(response.headers.get('X-Frame-Options'),'DENY')
        self.assertEqual(response.headers.get('X-Content-Type-Options'),'nosniff')
        self.assertIn("frame-ancestors 'none'",response.headers.get('Content-Security-Policy',''))
        self.assertIn('no-store',self.client.get('/api/targets').headers.get('Cache-Control',''))

    def test_javascript_has_executable_mime_even_with_wrong_windows_registry(self):
        with patch('mimetypes.guess_type',return_value=('text/plain',None)):
            response=self.client.get('/static/app.js')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'text/javascript')
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        response.close()
        page=self.client.get('/').get_data(as_text=True)
        self.assertRegex(page,r'/static/app\.js\?v=[0-9]+')

    def test_latest_status_and_availability_use_same_database_snapshot(self):
        with netscope.get_db() as db:
            db.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok,latency_source,icmp_sent,icmp_received,icmp_unanswered,packet_loss_pct,tcp_error) VALUES(1,'2026-10-14T10:00:00+00:00',0,0,'icmp_rtt',5,0,5,100,'Connection refused (closed)')")
        original=netscope.get_db
        class Cursor:
            def __init__(self,cursor): self.cursor=cursor
            def fetchall(self):
                rows=self.cursor.fetchall()
                with original() as writer:
                    writer.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok,latency_source,icmp_sent,icmp_received,icmp_unanswered,packet_loss_pct) VALUES(1,'2026-10-14T10:01:00+00:00',1,1,'icmp_rtt',5,5,0,0)")
                return rows
        class Reader:
            def __init__(self,connection): self.connection=connection
            def execute(self,sql,args=()):
                cursor=self.connection.execute(sql,args)
                return Cursor(cursor) if sql.startswith('SELECT t.*') else cursor
        @contextmanager
        def concurrent_reader():
            with original() as connection: yield Reader(connection)
        with patch.object(netscope,'get_db',concurrent_reader):
            response=self.client.get('/api/targets')
        row=response.json[0]
        self.assertEqual(row['ping_ok'],0)
        self.assertEqual(row['availability']['icmp']['percentage'],0)
        self.assertEqual(row['availability']['icmp']['valid_checks'],1)
        with original() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],2)

    def test_sqlite_rollback_closes_connection_and_preserves_existing_targets(self):
        with self.assertRaises(RuntimeError):
            with netscope.get_db() as connection:
                connection.execute("INSERT INTO targets(name,ip,port,created_at) VALUES('Rollback','127.0.0.2',80,'2026-10-14')")
                raise RuntimeError('rollback this transaction')
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
        self.assertEqual(len(self.client.get('/api/targets').json),1)

    def test_negative_network_results_are_completed_checks_not_execution_failures(self):
        measurements=netscope.parse_ping_batch(windows_batch([]))
        with patch.object(netscope,'ping',return_value=measurements),patch.object(netscope,'tcp_check',return_value=(False,'Connection refused (port is not accepting connections)')):
            response=self.client.post('/api/check')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['completed'],1)
        self.assertEqual(response.json['failed'],0)
        result=self.client.get('/api/targets').json[0]
        self.assertEqual(result['availability']['icmp']['percentage'],0)
        self.assertEqual(result['availability']['tcp']['percentage'],0)


class AuditProbeTests(unittest.TestCase):
    def test_validator_requires_literal_text_not_integer_or_packed_bytes(self):
        for value in (2130706433,b'\x7f\x00\x00\x01',ipaddress.IPv4Address('127.0.0.1'),True):
            with self.subTest(value=value),self.assertRaises(ValueError): netscope.validate_ip(value)

    def test_unexpected_ping_exit_is_not_valid_loss_measurement(self):
        with patch.object(netscope.platform,'system',return_value='Windows'), patch.object(netscope.subprocess,'run') as run:
            run.return_value.returncode=2
            run.return_value.stdout=windows_batch(['=1']*5)
            run.return_value.stderr='command failure'
            result=netscope.ping('127.0.0.1')
        self.assertIsNone(result['packet_loss_pct'])
        self.assertIn('command failed',result['ping_error'].lower())

    def test_nonfinite_or_impossible_rtt_is_not_numeric(self):
        for number in ('9'*400, '-1', '<0'):
            output=f'Reply from 127.0.0.1: bytes=32 time{number if number.startswith("<") else "="+number}ms TTL=128'
            ok,latency,bound,error=netscope.parse_ping_output(output)
            self.assertTrue(ok)
            self.assertIsNone(latency);self.assertIsNone(bound)
            self.assertIsNotNone(error)

    def test_local_socket_resource_errors_do_not_count_as_network_outage(self):
        with patch.object(netscope.socket,'create_connection',side_effect=OSError(24,'too many open files')):
            ok,error=netscope.tcp_check('127.0.0.1',80)
        self.assertFalse(ok)
        self.assertTrue(error.startswith('TCP check unavailable'))
        row=dict(latency_source='icmp_rtt',checked_at=netscope.utc_now(),tcp_ok=0,tcp_error=error)
        self.assertEqual(netscope.check_states(row)['tcp'],'unknown')

    def test_old_local_socket_errors_are_excluded_without_rewriting_history(self):
        for error in ('Network error: [Errno 24] Too many open files', 'Network error: [WinError 10013] Permission denied', 'Network error: unspecified'):
            row=dict(latency_source='icmp_rtt',checked_at=netscope.utc_now(),tcp_ok=0,tcp_error=error)
            self.assertEqual(netscope.check_states(row)['tcp'],'unknown')

    def test_monitor_loop_retries_after_outer_database_error(self):
        with patch.object(netscope,'check_all',side_effect=sqlite3.OperationalError('db busy')),patch.object(netscope.coordinator.stop_event,'wait',side_effect=InterruptedError('stop')),patch.object(netscope.app.logger,'exception') as log:
            with self.assertRaises(InterruptedError): netscope.monitoring_loop()
            log.assert_called_once()

    def test_traceroute_malformed_giant_hop_number_cannot_raise(self):
        result=trace.parse_tracert(' '+('9'*5000)+' 1 ms 2 ms 3 ms 127.0.0.1','127.0.0.1')
        self.assertEqual(result['status'],'command_failure')

    def test_launcher_is_loopback_without_debug_or_reloader(self):
        self.assertFalse(netscope.app.debug)
        tree=ast.parse(Path(netscope.__file__).read_text(encoding='utf-8'))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='run' and isinstance(n.func.value,ast.Name) and n.func.value.id=='app']
        self.assertEqual(len(calls),1)
        values={k.arg:ast.literal_eval(k.value) for k in calls[0].keywords}
        self.assertEqual(values['host'],'127.0.0.1')
        self.assertIs(values['debug'],False)
        self.assertIs(values.get('use_reloader'),False)
