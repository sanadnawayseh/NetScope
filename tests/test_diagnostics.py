import errno
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as netscope
import test_app


class ProbeTests(unittest.TestCase):
    def windows_ping(self, output, code=0):
        # Single-reply parser regressions; five-probe execution is covered
        # separately by the packet-loss tests.
        return netscope.parse_ping_output(output)

    def test_windows_reply_rtt_not_process_time_or_summary(self):
        self.assertEqual(self.windows_ping('Reply from 127.0.0.1: bytes=32 time=12ms TTL=128\nMinimum = 0ms, Maximum = 99ms, Average = 40ms'),
                         (True, 12.0, None, None))

    def test_windows_submillisecond_is_bound(self):
        self.assertEqual(self.windows_ping('Reply from 127.0.0.1: bytes=32 time<1ms TTL=128'),
                         (True, None, 1.0, None))

    def test_decimal_and_localized_reply(self):
        self.assertEqual(self.windows_ping('Antwort von 127.0.0.1: Bytes=32 Zeit=1,25ms TTL=128'),
                         (True, 1.25, None, None))

    def test_success_without_parseable_rtt_is_unavailable(self):
        ok, latency, upper, error = self.windows_ping('Reply from 127.0.0.1: bytes=32 TTL=128')
        self.assertTrue(ok)
        self.assertIsNone(latency)
        self.assertIsNone(upper)
        self.assertIn('RTT unavailable', error)

    def test_windows_unreachable_zero_exit_is_not_success(self):
        ok, latency, upper, error = self.windows_ping('Reply from 192.168.1.1: Destination host unreachable.')
        self.assertFalse(ok)
        self.assertIsNone(latency)
        self.assertIsNone(upper)
        self.assertEqual(error, 'Destination unreachable')

    def test_ping_timeout_and_command_failures(self):
        self.assertIn('timed out', self.windows_ping('Request timed out.', 1)[3])
        for exception, message in ((subprocess.TimeoutExpired('ping', 3), 'timed out'),
                                   (FileNotFoundError(), 'unavailable')):
            with self.subTest(exception=exception), patch.object(netscope.subprocess, 'run', side_effect=exception):
                batch = netscope.ping('127.0.0.1')
                ok, latency, upper, error = (batch['ping_ok'], batch['latency_ms'], batch['latency_upper_bound_ms'], batch['ping_error'])
                self.assertFalse(ok)
                self.assertIsNone(latency)
                self.assertIsNone(upper)
                self.assertIn(message, error)

    def test_unix_reply_uses_rtt(self):
        self.assertEqual(netscope.parse_ping_output('64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.034 ms'),
                         (True, 0.034, None, None))

    def test_tcp_failure_categories(self):
        cases = [(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'), 'Connection refused'),
                 (TimeoutError('timed out'), 'Connection timed out'),
                 (OSError(errno.ETIMEDOUT, 'timeout'), 'Connection timed out'),
                 (OSError(errno.ENETUNREACH, 'unreachable'), 'Network error'),
                 (OSError(10061, 'Windows refused'), 'Connection refused'),
                 (OSError(10060, 'Windows timeout'), 'Connection timed out')]
        for exception, message in cases:
            with self.subTest(exception=exception), patch.object(netscope.socket, 'create_connection', side_effect=exception):
                ok, error = netscope.tcp_check('127.0.0.1', 80)
                self.assertFalse(ok)
                self.assertTrue(error.startswith(message), error)

    def test_public_targets_never_reach_probes(self):
        with patch.object(netscope.subprocess, 'run') as ping, patch.object(netscope.socket, 'create_connection') as tcp:
            with self.assertRaises(ValueError):
                netscope.ping('8.8.8.8')
            with self.assertRaises(ValueError):
                netscope.tcp_check('8.8.8.8', 80)
            ping.assert_not_called()
            tcp.assert_not_called()


def batch_fixture(values):
    ok, latency, upper, error = values
    batch = netscope.empty_icmp(error)
    batch.update(ping_ok=ok, latency_ms=latency, latency_upper_bound_ms=upper)
    return batch


class DiagnosticStorageTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def test_check_specific_errors_persist_and_appear_in_both_apis(self):
        for ping_result, tcp_result in [((True, 2.0, None, None), (False, 'Connection timed out')),
                                        ((False, None, None, 'Destination unreachable'), (True, None)),
                                        ((False, None, None, 'Echo request timed out'), (False, 'Connection refused'))]:
            with self.subTest(ping=ping_result, tcp=tcp_result), patch.object(netscope, 'ping', return_value=batch_fixture(ping_result)), patch.object(netscope, 'tcp_check', return_value=tcp_result):
                netscope.run_check({'id': 1, 'ip': '127.0.0.1', 'port': 80})
                latest = self.client.get('/api/targets').json[0]
                recorded = self.client.get('/api/targets/1/history').json[-1]
                for row in (latest, recorded):
                    self.assertEqual(row['ping_error'], ping_result[3])
                    self.assertEqual(row['tcp_error'], tcp_result[1])
                    self.assertEqual(row['latency_ms'], ping_result[1])
                    self.assertEqual(row['latency_source'], 'icmp_rtt')
                    if tcp_result[1]:
                        self.assertIn('TCP: ', row['error'])

    def test_bound_survives_storage_and_api(self):
        with patch.object(netscope, 'ping', return_value=batch_fixture((True, None, 1.0, None))), patch.object(netscope, 'tcp_check', return_value=(True, None)):
            netscope.run_check({'id': 1, 'ip': '127.0.0.1', 'port': 80})
        row = self.client.get('/api/targets').json[0]
        self.assertIsNone(row['latency_ms'])
        self.assertEqual(row['latency_upper_bound_ms'], 1.0)


class MigrationTests(unittest.TestCase):
    def test_legacy_database_preserved_and_migration_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE targets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, port INTEGER NOT NULL, created_at TEXT NOT NULL)')
                db.execute('CREATE TABLE results (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE, checked_at TEXT NOT NULL, ping_ok INTEGER NOT NULL, latency_ms REAL, tcp_ok INTEGER NOT NULL, error TEXT)')
                db.execute("INSERT INTO targets VALUES(42, 'Existing device', '127.0.0.1', 80, '2026-10-14')")
                db.execute("INSERT INTO results VALUES(1, 42, '2026-10-14', 1, 23.5, 0, 'timed out')")
                db.execute("INSERT INTO results VALUES(2, 42, '2026-10-14', 0, NULL, 0, 'combined old errors')")
            db.close()
            with patch.object(netscope, 'DB_PATH', path):
                netscope.init_db()
                netscope.init_db()
                client = netscope.app.test_client()
                rows = client.get('/api/targets/42/history').json
                self.assertEqual(len(rows), 2)
                self.assertIsNone(rows[0]['latency_ms'])
                self.assertEqual(rows[0]['tcp_error'], 'timed out')
                self.assertIsNone(rows[0]['ping_error'])
                self.assertEqual(rows[1]['legacy_error'], 'combined old errors')
                self.assertIsNone(rows[1]['tcp_error'])
                self.assertEqual(client.get('/api/targets').json[0]['id'], 42)
                with netscope.get_db() as db:
                    self.assertEqual(db.execute('SELECT latency_ms FROM results WHERE id=1').fetchone()[0], 23.5)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM targets').fetchone()[0], 1)
                db.close()
