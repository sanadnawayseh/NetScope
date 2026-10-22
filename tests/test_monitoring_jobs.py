"""Async monitoring uses the existing engine; tests use gates, not real probes."""
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import app
import test_app
from monitoring_coordinator import Measurement, MonitoringCoordinator, MonitoringStopped


class MonitoringJobTests(unittest.TestCase):
    def setUp(self):
        self.pool = MonitoringCoordinator(workers=2, logger=Mock())
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.pool.shutdown()

    def done(self, job):
        self.assertTrue(self.pool._batch_done.wait(4), 'Job did not terminate')
        return self.pool.job_status(job['job_id'])

    def test_progress_waits_for_persistence_and_concurrent_results_settle(self):
        entered, writing = threading.Barrier(3), threading.Event()
        rows = []
        def collect(target):
            entered.wait(3)
            return Measurement(target)
        def persist(row):
            writing.set()
            if not self.release.wait(3):
                raise RuntimeError('Write gate not released')
            rows.append(row)
        job = self.pool.submit_job(lambda: [{'id': 1}, {'id': 2}], collect, persist)
        try:
            entered.wait(3)
            self.assertTrue(writing.wait(3))
            status = self.pool.job_status(job['job_id'])
            self.assertEqual((status['total'], status['completed'], status['progress_percentage']), (2, 0, 0))
            self.assertIs(self.pool.run_batch(lambda: [], Mock(), Mock()), False)
            self.assertIs(self.pool.submit_job(lambda: [], Mock(), Mock()), False)
        finally:
            self.release.set()
        status = self.done(job)
        self.assertEqual((status['state'], status['completed'], status['failed'], status['progress_percentage']), ('completed', 2, 0, 100))
        self.assertEqual({r['id'] for r in rows}, {1, 2})
        self.assertIsNotNone(status['started_at'])
        self.assertIsNotNone(status['ended_at'])

    def test_intermediate_progress_counts_only_settled_tasks(self):
        second = threading.Event()
        calls = []
        def persist(row):
            calls.append(row)
            if len(calls) == 2:
                second.set()
                self.release.wait(3)
        job = self.pool.submit_job(lambda: [{'id': 1}, {'id': 2}], lambda target: Measurement(target), persist)
        try:
            self.assertTrue(second.wait(3))
            status = self.pool.job_status(job['job_id'])
            self.assertEqual((status['completed'], status['failed'], status['progress_percentage']), (1, 0, 50))
        finally:
            self.release.set()
        self.assertEqual(self.done(job)['completed'], 2)

    def test_automatic_batch_blocks_job_submission(self):
        started = threading.Event()
        def collect(target):
            started.set()
            self.release.wait(3)
            return Measurement(target)
        worker = threading.Thread(target=self.pool.run_batch, args=(lambda: [{'id': 1}], collect, Mock()))
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            self.assertIs(self.pool.submit_job(lambda: [], Mock(), Mock()), False)
        finally:
            self.release.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())

    def test_partial_and_persistence_failures_keep_correct_counts(self):
        def collect(target):
            return Measurement(target, execution_failed=target['id'] == 2)
        saved = []
        def persist(row):
            if row['id'] == 3:
                raise sqlite3.OperationalError('private database path')
            saved.append(row['id'])
        job = self.pool.submit_job(lambda: [{'id': n} for n in (1, 2, 3)], collect, persist)
        status = self.done(job)
        self.assertEqual((status['state'], status['completed'], status['failed'], status['progress_percentage']), ('failed', 1, 2, 100))
        self.assertEqual(sorted(saved), [1, 2])
        self.assertNotIn('private', status['error'])

    def test_empty_job_and_bounded_retention(self):
        self.pool.job_limit = 2
        jobs = []
        for _ in range(4):
            job = self.pool.submit_job(lambda: [], Mock(), Mock())
            status = self.done(job)
            self.assertEqual((status['state'], status['total'], status['progress_percentage']), ('completed', 0, 100))
            jobs.append(job)
        self.assertIsNone(self.pool.job_status(jobs[0]['job_id']))
        self.assertIsNotNone(self.pool.job_status(jobs[-1]['job_id']))
        status['state'] = 'changed'
        self.assertEqual(self.pool.job_status(jobs[-1]['job_id'])['state'], 'completed')
        self.assertEqual(len(self.pool._jobs), 2)

    def test_shutdown_cancels_queued_tasks_and_finishes_running_write(self):
        self.pool.shutdown()
        self.pool = MonitoringCoordinator(workers=1, logger=Mock())
        started = threading.Event()
        def collect(target):
            started.set()
            self.release.wait(3)
            return Measurement(target)
        saved = []
        job = self.pool.submit_job(lambda: [{'id': n} for n in range(3)], collect, saved.append)
        try:
            self.assertTrue(started.wait(3))
            self.pool.request_stop()
        finally:
            self.release.set()
        self.pool.shutdown()
        status = self.pool.job_status(job['job_id'])
        self.assertEqual((status['state'], status['completed'], status['failed']), ('cancelled', 1, 2))
        self.assertEqual(len(saved), 1)
        self.assertIsNotNone(status['ended_at'])
        with self.assertRaises(MonitoringStopped):
            self.pool.submit_job(lambda: [], Mock(), Mock())

    def test_snapshot_and_thread_start_failures_become_terminal(self):
        job = self.pool.submit_job(Mock(side_effect=sqlite3.OperationalError('secret')), Mock(), Mock())
        self.assertEqual(self.done(job)['state'], 'failed')
        with patch('monitoring_coordinator.threading.Thread.start', side_effect=RuntimeError('secret')):
            job = self.pool.submit_job(lambda: [], Mock(), Mock())
        self.assertEqual((job['state'], job['total']), ('failed', 0))
        self.assertTrue(self.pool._batch_done.is_set())
        self.assertFalse(self.pool.batch_lock.locked())

    def test_stop_after_reservation_before_runner_cancels_job(self):
        thread = Mock()
        with patch('monitoring_coordinator.threading.Thread', return_value=thread):
            job = self.pool.submit_job(lambda: [], Mock(), Mock())
        self.pool.request_stop()
        self.pool._run_job(job['job_id'], lambda: [], Mock(), Mock())
        self.assertEqual(self.pool.job_status(job['job_id'])['state'], 'cancelled')
        self.assertTrue(self.pool._batch_done.is_set())


class MonitoringJobAPITests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def test_202_before_probe_finishes_status_and_legacy_compatibility(self):
        entered, release = threading.Event(), threading.Event()
        def collect(target):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Probe was not released')
            return Measurement(dict(target_id=target['id'], checked_at=app.utc_now(), ping_ok=0, tcp_ok=1))
        with patch.object(app, 'collect_measurement', side_effect=collect):
            response = self.client.post('/api/check-jobs')
            try:
                self.assertEqual(response.status_code, 202)
                self.assertTrue(entered.wait(3))
                location = response.headers['Location']
                status = self.client.get(location)
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json['completed'], 0)
                self.assertEqual(status.headers['Cache-Control'], 'no-store')
                self.assertEqual(self.client.post('/api/check-jobs').status_code, 409)
                self.assertEqual(self.client.post('/api/check').status_code, 409)
                self.assertEqual(self.client.delete('/api/targets/1').status_code, 409)
            finally:
                release.set()
            self.assertTrue(app.coordinator._batch_done.wait(4))
            self.assertEqual(self.client.get(location).json['state'], 'completed')
            self.assertEqual(len(self.client.get('/api/targets/1/history').json), 1)
            legacy = self.client.post('/api/check')
            self.assertEqual(legacy.status_code, 200)
            self.assertEqual((legacy.json['total'], legacy.json['completed'], legacy.json['failed']), (1, 1, 0))

    def test_unknown_stopped_and_security_boundary(self):
        self.assertEqual(self.client.get('/api/check-jobs/missing').status_code, 404)
        self.assertEqual(self.client.post('/api/check-jobs', headers={'Origin': 'https://foreign.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/check-jobs/missing', environ_overrides={'REMOTE_ADDR': '10.0.0.1'}).status_code, 403)
        app.coordinator.shutdown()
        self.assertEqual(self.client.post('/api/check-jobs').status_code, 503)
