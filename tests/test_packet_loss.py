import subprocess
import threading
import unittest
from unittest.mock import patch, Mock

import app as netscope
import test_app


def windows_batch(times, sent=5, received=None):
    replies = '\n'.join(f'Reply from 127.0.0.1: bytes=32 time{value}ms TTL=128' for value in times)
    received = len(times) if received is None else received
    return f'{replies}\nPackets: Sent = {sent}, Received = {received}, Lost = {sent-received} ({(sent-received)*100//sent}% loss),'


class PacketParserTests(unittest.TestCase):
    def test_zero_loss_and_rtt_statistics_from_replies(self):
        batch = netscope.parse_ping_batch(windows_batch(['=1', '=2', '=3', '=4', '=5']) + '\nMinimum = 90ms, Maximum = 99ms, Average = 95ms')
        self.assertEqual([batch[k] for k in ('icmp_sent', 'icmp_received', 'icmp_unanswered', 'packet_loss_pct')], [5, 5, 0, 0])
        self.assertEqual([batch[k] for k in ('latency_ms', 'rtt_min_ms', 'rtt_max_ms', 'rtt_sample_count')], [3, 1, 5, 5])
        self.assertTrue(batch['ping_ok'])

    def test_partial_loss_excludes_unanswered_from_rtt(self):
        batch = netscope.parse_ping_batch(windows_batch(['=2', '=4', '=9']) + '\nRequest timed out.\nRequest timed out.')
        self.assertEqual([batch[k] for k in ('icmp_sent', 'icmp_received', 'icmp_unanswered', 'packet_loss_pct')], [5, 3, 2, 40])
        self.assertEqual([batch[k] for k in ('latency_ms', 'rtt_min_ms', 'rtt_max_ms')], [5, 2, 9])
        self.assertTrue(batch['ping_ok'])

    def test_total_loss_has_no_latency(self):
        batch = netscope.parse_ping_batch('Request timed out.\n' * 5 + windows_batch([]))
        self.assertEqual(batch['packet_loss_pct'], 100)
        self.assertEqual(batch['icmp_received'], 0)
        self.assertEqual(batch['icmp_unanswered'], 5)
        self.assertFalse(batch['ping_ok'])
        for field in ('latency_ms', 'rtt_min_ms', 'rtt_max_ms', 'latency_upper_bound_ms'):
            self.assertIsNone(batch[field])

    def test_windows_icmp_errors_are_not_echo_replies(self):
        output = 'Reply from 192.168.1.1: Destination host unreachable.\n' * 5 + windows_batch([], received=5)
        batch = netscope.parse_ping_batch(output)
        self.assertEqual(batch['packet_loss_pct'], 100)
        self.assertEqual(batch['icmp_received'], 0)
        self.assertFalse(batch['ping_ok'])

    def test_malformed_or_truncated_output_never_fabricates_statistics(self):
        outputs = ['', 'garbage', 'Request timed out.',
                   'Packets: Sent = 5, Received = 5, Lost = 0',
                   windows_batch(['=2'], sent=4), windows_batch(['=2']*6, sent=6),
                   windows_batch(['=2']).replace('Lost = 4', 'Lost = 1'),
                   'Reply from 127.0.0.1: bytes=32 time=2ms TTL=128']
        for output in outputs:
            with self.subTest(output=output):
                batch = netscope.parse_ping_batch(output)
                for field in ('icmp_sent', 'icmp_received', 'icmp_unanswered', 'packet_loss_pct', 'latency_ms', 'rtt_min_ms', 'rtt_max_ms'):
                    self.assertIsNone(batch[field])
                self.assertIn('malformed', batch['ping_error'])

    def test_bounds_are_not_fabricated_exact_averages(self):
        batch = netscope.parse_ping_batch(windows_batch(['<1', '=2', '=3']))
        self.assertEqual(batch['packet_loss_pct'], 40)
        self.assertEqual(batch['rtt_sample_count'], 2)
        self.assertEqual(batch['latency_upper_bound_ms'], 2)
        for field in ('latency_ms', 'rtt_min_ms', 'rtt_max_ms'):
            self.assertIsNone(batch[field])

    def test_missing_rtt_does_not_invalidate_real_loss(self):
        output = windows_batch(['=1']*5).replace(' time=1ms', '')
        batch = netscope.parse_ping_batch(output)
        self.assertEqual(batch['packet_loss_pct'], 0)
        self.assertIsNone(batch['latency_ms'])
        self.assertIsNone(batch['rtt_min_ms'])
        self.assertIn('RTT', batch['ping_error'])

    def test_linux_summary_and_actual_reply_times(self):
        batch = netscope.parse_ping_batch('\n'.join(f'64 bytes from 127.0.0.1: icmp_seq={i} ttl=64 time=0.2 ms' for i in range(3)) + '\n5 packets transmitted, 3 received, 40% packet loss')
        self.assertEqual(batch['packet_loss_pct'], 40)
        self.assertAlmostEqual(batch['latency_ms'], .2)

    def test_execution_five_probes_configured_timeout_partial_nonzero_exit(self):
        with patch.object(netscope.platform, 'system', return_value='Windows'), patch.object(netscope, 'ICMP_TIMEOUT_MS', 2500), patch.object(netscope.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, windows_batch(['=2', '=4']), '')) as run:
            batch = netscope.ping('127.0.0.1')
        self.assertTrue(run.call_args.args[0][0].lower().endswith('system32\\ping.exe'))
        self.assertEqual(run.call_args.args[0][1:], ['-n', '5', '-w', '2500', '127.0.0.1'])
        self.assertEqual(run.call_args.kwargs['timeout'], 22)
        self.assertFalse(run.call_args.kwargs.get('shell', False))
        self.assertEqual(batch['packet_loss_pct'], 60)
        self.assertTrue(batch['ping_ok'])

    def test_process_deadline_does_not_invent_five_sent(self):
        with patch.object(netscope.platform, 'system', return_value='Windows'), patch.object(netscope.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ping', 12, output='partial output')):
            batch = netscope.ping('127.0.0.1')
        self.assertIsNone(batch['icmp_sent'])
        self.assertIsNone(batch['packet_loss_pct'])
        self.assertIsNone(batch['latency_ms'])

    def test_config_bounds(self):
        for value in ('nan', 'inf', '-1', '10001', 'bad'):
            with self.subTest(value=value), patch.dict(netscope.os.environ, {'NETSCOPE_ICMP_TIMEOUT_MS': value}):
                with self.assertRaises(ValueError):
                    netscope.setting('NETSCOPE_ICMP_TIMEOUT_MS', 1000, 100, 10000)


class PacketStorageAndSchedulingTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def test_all_loss_levels_survive_database_and_history(self):
        for times, expected in [(['=1']*5, 0), (['=2', '=4', '=9'], 40), ([], 100)]:
            output = windows_batch(times)
            with patch.object(netscope.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')), patch.object(netscope, 'tcp_check', return_value=(True, None)) as tcp:
                netscope.run_check({'id':1, 'ip':'127.0.0.1', 'port':80})
                tcp.assert_called_once_with('127.0.0.1', 80)
            for row in (self.client.get('/api/targets').json[0], self.client.get('/api/targets/1/history').json[-1]):
                self.assertEqual(row['packet_loss_pct'], expected)
                self.assertEqual(row['icmp_sent'], 5)
                self.assertEqual(row['icmp_unanswered'], 5-len(times))
                self.assertEqual(row['tcp_ok'], 1)
                if not times:
                    self.assertIsNone(row['latency_ms'])
                    self.assertIsNone(row['rtt_min_ms'])
                    self.assertIsNone(row['rtt_max_ms'])
        self.assertEqual(len(self.client.get('/api/targets/1/history').json), 3)

    def test_prior_diagnostic_schema_migration_is_additive(self):
        with netscope.get_db() as db:
            # Recreate precisely the previous diagnostic schema in this disposable DB.
            db.execute('PRAGMA user_version=0')
            db.execute('DROP TABLE results')
            db.execute('CREATE TABLE results (id INTEGER PRIMARY KEY, target_id INTEGER REFERENCES targets(id) ON DELETE CASCADE, checked_at TEXT, ping_ok INTEGER, latency_ms REAL, tcp_ok INTEGER, error TEXT, ping_error TEXT, tcp_error TEXT, latency_source TEXT, latency_upper_bound_ms REAL)')
            db.execute("INSERT INTO results VALUES(7,1,'2026-10-14',1,2.5,0,'TCP: timed out',NULL,'timed out','icmp_rtt',NULL)")
        db.close()
        netscope.init_db()
        netscope.init_db()
        rows = self.client.get('/api/targets/1/history').json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['latency_ms'], 2.5)
        self.assertEqual(rows[0]['tcp_error'], 'timed out')
        for field in netscope.PACKET_FIELDS:
            self.assertIsNone(rows[0][field])
        self.assertEqual(self.client.get('/api/targets').json[0]['id'], 1)

    def test_overlapping_requests_are_skipped_not_queued(self):
        entered, release = threading.Event(), threading.Event()
        def slow_check(target):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test cycle was not released')
            return netscope.Measurement({'target_id': target['id']})
        with patch.object(netscope, 'collect_measurement', side_effect=slow_check) as run, patch.object(netscope, 'persist_measurement'):
            worker = threading.Thread(target=netscope.check_all)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                response = self.client.post('/api/check')
                self.assertEqual(response.status_code, 409)
                self.assertIn('no duplicate', response.json['error'])
                self.assertFalse(netscope.check_all())
                self.assertEqual(run.call_count, 1)
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(netscope.lock.locked())
            self.assertEqual(run.call_count, 1)

    def test_background_interval_is_after_finished_checks(self):
        timeline = []
        def completed():
            timeline.append('finished')
        def sleep(seconds):
            timeline.append(seconds)
            raise InterruptedError('Stop test loop')
        with patch.object(netscope, 'check_all', side_effect=completed), patch.object(netscope.coordinator.stop_event, 'wait', side_effect=sleep):
            with self.assertRaises(InterruptedError):
                netscope.monitoring_loop()
        self.assertEqual(timeline, ['finished', netscope.CHECK_INTERVAL])

    def test_failed_target_does_not_leave_batch_lock_held(self):
        with patch.object(netscope, 'collect_measurement', side_effect=RuntimeError('probe failed')), patch.object(netscope.app.logger, 'exception') as log:
            self.assertTrue(netscope.check_all())
            log.assert_called_once()
        self.assertFalse(netscope.lock.locked())
