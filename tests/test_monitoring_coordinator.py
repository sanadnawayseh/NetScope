"""Deterministic simulated monitoring. No network access or production DB writes."""
from contextlib import contextmanager
from pathlib import Path
import runpy
import sqlite3
import threading
import unittest
from unittest.mock import Mock, call, patch

import app as netscope
from monitoring_coordinator import Measurement, MonitoringCoordinator, MonitoringStopped, parse_worker_limit
import test_app
from test_packet_loss import windows_batch


TARGETS = [{'id': n} for n in range(1, 21)]


def start_thread(function):
    result, done = {}, threading.Event()
    def run():
        try:
            result['value'] = function()
        except BaseException as error:
            result['error'] = error
        finally:
            done.set()
    thread = threading.Thread(target=run)
    thread.start()
    return thread, result, done


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.pool = MonitoringCoordinator(logger=Mock())

    def tearDown(self):
        self.pool.shutdown()

    def finish(self, thread, result):
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'Monitoring thread did not settle')
        if 'error' in result:
            raise result['error']
        return result['value']

    def test_worker_configuration_default_and_strict_bounds(self):
        self.assertEqual(self.pool.workers, 4)
        for value in (1, 4, 16, '1', '4', '16'):
            self.assertEqual(parse_worker_limit(value), int(value))
        for value in (0, 17, True, 1.5, '4.0', 'inf', '-1', '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_worker_limit(value)

    def test_twenty_devices_overlap_with_bound_and_single_writer(self):
        self.verify_worker_limit(self.pool, 4)

    def test_one_worker_is_sequential_fallback(self):
        with_pool = MonitoringCoordinator(1)
        try:
            self.verify_worker_limit(with_pool, 1)
        finally:
            with_pool.shutdown()

    def verify_worker_limit(self, pool, limit):
        guard, release, full = threading.Lock(), threading.Event(), threading.Event()
        active, maximum, worker_ids, rows, writer_ids = 0, 0, set(), [], set()
        def collect(target):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
                worker_ids.add(threading.get_ident())
                if active == limit:
                    full.set()
            try:
                if not release.wait(4):
                    raise RuntimeError('Test gate not released')
                return Measurement({'target_id': target['id']})
            finally:
                with guard:
                    active -= 1
        def persist(row):
            rows.append(row['target_id'])
            writer_ids.add(threading.get_ident())
        thread, result, done = start_thread(lambda: pool.run_batch(lambda: TARGETS, collect, persist))
        try:
            self.assertTrue(full.wait(3))
            self.assertFalse(done.is_set())
            self.assertEqual(maximum, limit)
            with patch.object(pool, 'run_batch', wraps=pool.run_batch):
                self.assertIs(pool.run_batch(Mock(side_effect=AssertionError('busy batch loaded targets')), collect, persist), False)
        finally:
            release.set()
        report = self.finish(thread, result)
        self.assertEqual(report, {'total': 20, 'completed': 20, 'failed': 0})
        self.assertEqual(sorted(rows), list(range(1, 21)))
        self.assertEqual(writer_ids, {thread.ident})
        self.assertTrue(writer_ids.isdisjoint(worker_ids))
        self.assertFalse(pool.batch_lock.locked())

    def test_fast_targets_persist_while_one_slow_target_is_running(self):
        entered, release, fast = threading.Event(), threading.Event(), threading.Event()
        rows = []
        def collect(target):
            if target['id'] == 1:
                entered.set()
                if not release.wait(4):
                    raise RuntimeError('Test gate not released')
            return Measurement({'target_id': target['id']})
        def persist(row):
            rows.append(row['target_id'])
            if len(rows) == 19:
                fast.set()
        thread, result, done = start_thread(lambda: self.pool.run_batch(lambda: TARGETS, collect, persist))
        try:
            self.assertTrue(entered.wait(3))
            self.assertTrue(fast.wait(3))
            self.assertNotIn(1, rows)
            self.assertTrue(self.pool.batch_lock.locked())
        finally:
            release.set()
        self.assertEqual(self.finish(thread, result)['completed'], 20)

    def test_worker_partial_and_persistence_failures_are_isolated(self):
        rows, attempts = [], []
        def collect(target):
            if target['id'] == 3:
                raise RuntimeError('Worker failed')
            return Measurement({'target_id': target['id']}, execution_failed=target['id'] == 5)
        def persist(row):
            attempts.append(row['target_id'])
            if row['target_id'] == 7:
                raise sqlite3.OperationalError('Simulated locked database')
            rows.append(row['target_id'])
        report = self.pool.run_batch(lambda: TARGETS, collect, persist)
        self.assertEqual(report, {'total': 20, 'completed': 17, 'failed': 3})
        self.assertEqual(len(attempts), 19)
        self.assertEqual(len(set(rows)), 18)
        self.assertIn(5, rows)  # Useful partial data is persisted but execution failed.
        self.assertIn(20, rows)
        self.assertNotIn(3, rows)
        self.assertNotIn(7, rows)

    def test_shutdown_cancels_queue_drains_running_results_and_rejects_new_work(self):
        full, release = threading.Event(), threading.Event()
        guard, started, rows = threading.Lock(), [], []
        def collect(target):
            with guard:
                started.append(target['id'])
                if len(started) == 4:
                    full.set()
            if not release.wait(4):
                raise RuntimeError('Test gate not released')
            return Measurement({'target_id': target['id']})
        thread, result, done = start_thread(lambda: self.pool.run_batch(lambda: TARGETS, collect, rows.append))
        closer = None
        try:
            self.assertTrue(full.wait(3))
            self.pool.request_stop()
            closer, close_result, close_done = start_thread(self.pool.shutdown)
            self.assertFalse(close_done.is_set())
            self.assertFalse(done.is_set())
            self.assertTrue(self.pool.batch_lock.locked())
            self.assertIs(self.pool.run_batch(lambda: TARGETS, collect, rows.append), False)
        finally:
            release.set()
        self.assertEqual(self.finish(thread, result), {'total': 20, 'completed': 4, 'failed': 16})
        self.finish(closer, close_result)
        self.assertEqual(len(started), 4)
        self.assertEqual(len(rows), 4)
        self.assertFalse(self.pool.batch_lock.locked())
        with self.assertRaises(MonitoringStopped):
            self.pool.run_batch(lambda: TARGETS, collect, rows.append)

    def test_shutdown_while_loading_snapshot_submits_nothing(self):
        entered, release = threading.Event(), threading.Event()
        collect = Mock()
        def load():
            entered.set()
            if not release.wait(4):
                raise RuntimeError('Test gate not released')
            return TARGETS
        thread, result, done = start_thread(lambda: self.pool.run_batch(load, collect, Mock()))
        try:
            self.assertTrue(entered.wait(3))
            self.pool.request_stop()
            self.assertTrue(self.pool.batch_lock.locked())
        finally:
            release.set()
        self.assertEqual(self.finish(thread, result), {'total': 20, 'completed': 0, 'failed': 20})
        collect.assert_not_called()

    def test_shutdown_waits_for_persistence_without_holding_lifecycle_lock(self):
        writing, release = threading.Event(), threading.Event()
        rows = []
        def persist(row):
            writing.set()
            if not release.wait(4):
                raise RuntimeError('Test writer not released')
            rows.append(row)
        thread, result, done = start_thread(lambda: self.pool.run_batch(lambda: TARGETS, lambda t: Measurement(t), persist))
        try:
            self.assertTrue(writing.wait(3))
            self.pool.request_stop()  # Must return while the writer remains blocked.
            closer, closed, close_done = start_thread(self.pool.shutdown)
            self.assertFalse(close_done.is_set())
            self.assertTrue(self.pool.batch_lock.locked())
        finally:
            release.set()
        report = self.finish(thread, result)
        self.finish(closer, closed)
        self.assertEqual(report['completed'] + report['failed'], 20)
        self.assertEqual(report['completed'], len(rows))

    def test_worker_that_reaches_start_gate_after_stop_does_not_probe(self):
        self.pool.request_stop()
        collect = Mock()
        with self.assertRaises(Exception) as error:
            self.pool._collect(collect, TARGETS[0])
        self.assertEqual(type(error.exception).__name__, 'CancelledError')
        collect.assert_not_called()

    def test_load_failure_releases_lock_and_next_batch_can_run(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.pool.run_batch(Mock(side_effect=sqlite3.OperationalError('failed snapshot')), Mock(), Mock())
        self.assertFalse(self.pool.batch_lock.locked())
        self.assertEqual(self.pool.run_batch(lambda: [], Mock(), Mock())['total'], 0)

    def test_shutdown_from_worker_or_writer_rejects_self_wait_instead_of_deadlocking(self):
        def collect(target):
            with self.assertRaisesRegex(RuntimeError, 'wait on itself'):
                self.pool.shutdown()
            return Measurement(target)
        def persist(row):
            with self.assertRaisesRegex(RuntimeError, 'wait on itself'):
                self.pool.shutdown()
        report = self.pool.run_batch(lambda: TARGETS, collect, persist)
        self.assertEqual(report, {'total': 20, 'completed': 20, 'failed': 0})

    def test_submission_failure_drains_already_submitted_work(self):
        original = self.pool._executor.submit
        calls = 0
        def submit(*args):
            nonlocal calls
            calls += 1
            if calls == 5:
                raise RuntimeError('Cannot create remaining workers')
            return original(*args)
        rows = []
        with patch.object(self.pool._executor, 'submit', side_effect=submit):
            report = self.pool.run_batch(lambda: TARGETS, lambda t: Measurement({'target_id': t['id']}), rows.append)
        self.assertEqual(report, {'total': 20, 'completed': 4, 'failed': 16})
        self.assertEqual(len(rows), 4)

    def test_stop_racing_with_admission_never_deadlocks_or_submits_after_close(self):
        for _ in range(10):
            pool = MonitoringCoordinator(logger=Mock())
            barrier = threading.Barrier(3)
            def batch():
                barrier.wait(3)
                return pool.run_batch(lambda: TARGETS, lambda t: Measurement(t), lambda r: None)
            def stop():
                barrier.wait(3)
                pool.shutdown()
            thread, result, done = start_thread(batch)
            closer, closed, close_done = start_thread(stop)
            barrier.wait(3)
            try:
                thread.join(5)
                self.assertFalse(thread.is_alive())
                if 'error' in result:
                    self.assertIsInstance(result['error'], MonitoringStopped)
                else:
                    report = result['value']
                    self.assertEqual(report['completed'] + report['failed'], 20)
                self.finish(closer, closed)
                self.assertFalse(pool.batch_lock.locked())
            finally:
                pool.shutdown()


class CoordinatorIntegrationTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def add_twenty(self):
        with netscope.get_db() as db:
            db.executemany('INSERT INTO targets(name,ip,port,created_at) VALUES(?,?,?,?)',
                           [(f'Simulated {n}', f'127.0.0.{n}', 80, netscope.utc_now()) for n in range(2, 21)])

    def test_partial_protocol_errors_preserve_independent_results_and_return_503(self):
        self.add_twenty()
        good = netscope.parse_ping_batch(windows_batch(['=2'] * 5))
        def ping(ip):
            if ip.endswith('.3'):
                raise RuntimeError('Unexpected ICMP error')
            return dict(good)
        def tcp(ip, port):
            if ip.endswith('.4'):
                raise RuntimeError('Unexpected TCP error')
            return True, None
        with patch.object(netscope, 'ping', side_effect=ping), patch.object(netscope, 'tcp_check', side_effect=tcp), patch.object(netscope.app.logger, 'exception'):
            response = self.client.post('/api/check')
        self.assertEqual(response.status_code, 503)
        self.assertEqual([response.json[k] for k in ('total', 'completed', 'failed')], [20, 18, 2])
        targets = {r['id']: r for r in self.client.get('/api/targets').json}
        self.assertEqual((targets[3]['icmp_check_status'], targets[3]['tcp_check_status']), ('unknown', 'success'))
        self.assertIsNone(targets[3]['packet_loss_pct'])
        self.assertIsNone(targets[3]['latency_ms'])
        self.assertEqual((targets[4]['icmp_check_status'], targets[4]['tcp_check_status']), ('success', 'unknown'))
        self.assertEqual(targets[4]['latency_ms'], 2)
        with netscope.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0], 20)

    def test_mutations_and_repeated_requests_block_until_persistence_finishes(self):
        self.add_twenty()
        entered, release = threading.Event(), threading.Event()
        original = netscope.persist_measurement
        def persist(row):
            if not entered.is_set():
                entered.set()
                if not release.wait(4):
                    raise RuntimeError('Test gate not released')
            original(row)
        good = netscope.parse_ping_batch(windows_batch(['=2'] * 5))
        with patch.object(netscope, 'ping', return_value=good), patch.object(netscope, 'tcp_check', return_value=(True, None)), patch.object(netscope, 'persist_measurement', side_effect=persist):
            thread, result, done = start_thread(lambda: netscope.check_all(report=True))
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.delete('/api/targets/20').status_code, 409)
                self.assertEqual(self.client.post('/api/targets', json={'name': 'Replacement', 'ip': '127.0.0.20', 'port': 80}).status_code, 409)
                self.assertEqual(self.client.post('/api/check').status_code, 409)
                self.assertFalse(done.is_set())
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', result)
        self.assertEqual(result['value']['completed'], 20)
        self.assertEqual(self.client.delete('/api/targets/20').status_code, 204)

    def test_both_execution_errors_are_unknown_not_offline(self):
        with patch.object(netscope, 'ping', side_effect=RuntimeError('ICMP')), patch.object(netscope, 'tcp_check', side_effect=RuntimeError('TCP')), patch.object(netscope.app.logger, 'exception'):
            response = self.client.post('/api/check')
        self.assertEqual(response.status_code, 503)
        target = self.client.get('/api/targets').json[0]
        self.assertEqual((target['icmp_check_status'], target['tcp_check_status']), ('unknown', 'unknown'))
        for protocol in ('icmp', 'tcp'):
            self.assertEqual(target['availability'][protocol]['valid_checks'], 0)

    def test_stopped_monitor_returns_503_without_new_probes(self):
        netscope.coordinator.shutdown()
        with patch.object(netscope, 'collect_measurement') as collect:
            response = self.client.post('/api/check')
        self.assertEqual(response.status_code, 503)
        collect.assert_not_called()

    def test_launcher_stops_joins_and_drains_on_return_and_keyboard_interrupt(self):
        for interruption in (None, KeyboardInterrupt):
            with self.subTest(interruption=interruption):
                pool, thread, order = Mock(), Mock(), Mock()
                order.attach_mock(pool.request_stop, 'stop')
                order.attach_mock(thread.join, 'join')
                order.attach_mock(pool.shutdown, 'drain')
                with patch.dict('os.environ', {'NETSCOPE_DB': str(Path(self.directory.name) / 'launcher.db')}), patch('application_logging.configure_logging'), patch('monitoring_coordinator.MonitoringCoordinator', return_value=pool), patch.object(netscope.threading, 'Thread', return_value=thread), patch('flask.Flask.run', side_effect=interruption):
                    if interruption:
                        with self.assertRaises(KeyboardInterrupt):
                            runpy.run_path(str(netscope.BASE / 'app.py'), run_name='__main__')
                    else:
                        runpy.run_path(str(netscope.BASE / 'app.py'), run_name='__main__')
                thread.start.assert_called_once()
                self.assertEqual(order.mock_calls, [call.stop(), call.join(), call.drain()])

    def test_sqlite_contention_released_before_deadline_persists_once(self):
        self.add_twenty()
        attempting = threading.Event()
        original = netscope.get_db
        @contextmanager
        def traced_db():
            with original() as db:
                db.set_trace_callback(lambda sql: attempting.set() if sql.startswith('INSERT INTO results') else None)
                yield db
        good = netscope.parse_ping_batch(windows_batch(['=1'] * 5))
        with original() as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            with patch.object(netscope, 'get_db', traced_db), patch.object(netscope, 'ping', return_value=good), patch.object(netscope, 'tcp_check', return_value=(True, None)):
                thread, result, done = start_thread(lambda: netscope.check_all(report=True))
                try:
                    self.assertTrue(attempting.wait(3))
                    self.assertFalse(done.is_set())
                finally:
                    blocker.commit()
                    thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', result)
        self.assertEqual(result['value'], {'total': 20, 'completed': 20, 'failed': 0})
        with original() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0], 20)

    def test_sqlite_busy_failure_does_not_stop_remaining_results(self):
        self.add_twenty()
        failed, released = threading.Event(), threading.Event()
        original = netscope.get_db
        connect = sqlite3.connect
        def short_connect(*args, **kwargs):
            kwargs['timeout'] = 0  # Deterministic immediate busy error in this test only.
            return connect(*args, **kwargs)
        def logged(*args, **kwargs):
            failed.set()
            if not released.wait(4):
                raise RuntimeError('Test writer was not released')
        good = netscope.parse_ping_batch(windows_batch(['=1'] * 5))
        with original() as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            with patch.object(netscope.sqlite3, 'connect', side_effect=short_connect), patch.object(netscope, 'ping', return_value=good), patch.object(netscope, 'tcp_check', return_value=(True, None)), patch.object(netscope.coordinator.logger, 'exception', side_effect=logged):
                thread, result, done = start_thread(lambda: netscope.check_all(report=True))
                try:
                    self.assertTrue(failed.wait(3))
                finally:
                    blocker.commit()
                    released.set()
                    thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', result)
        self.assertEqual(result['value'], {'total': 20, 'completed': 19, 'failed': 1})
        with original() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0], 19)
