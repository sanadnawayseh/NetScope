import io
import json
import logging
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import app
from application_logging import JSONFormatter, BatchContextFilter, configure_logging
from monitoring_coordinator import Measurement, MonitoringCoordinator
from schema_migrations import migrate_schema


class ApplicationLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.logger = logging.Logger('logging-test', logging.DEBUG)
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(BatchContextFilter())
        self.logger.addHandler(handler)
        self.addCleanup(lambda: [h.close() for h in self.logger.handlers])

    def events(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def test_json_format_severity_and_redaction(self):
        try:
            raise RuntimeError('password=secret 192.168.1.2 private.db')
        except RuntimeError:
            self.logger.exception('secret body %s', {'token': 'secret'}, extra={'event': 'probe_failure', 'protocol': 'icmp', 'request_body': 'secret'})
        event = self.events()[0]
        self.assertEqual(event['event'], 'probe_failure')
        self.assertEqual(event['severity'], 'ERROR')
        self.assertEqual(event['exception_type'], 'RuntimeError')
        self.assertTrue(event['timestamp'].endswith('+00:00'))
        for value in ('secret', '192.168', 'private.db', 'request_body'):
            self.assertNotIn(value, self.stream.getvalue())
        self.logger.info('secret', extra={'event': 'secret\nbody', 'batch_id': 'private', 'phase': 'private'})
        self.assertEqual(self.events()[1]['event'], 'application_message')
        self.assertNotIn('phase', self.events()[1])

    def test_rotation_level_and_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', {'NETSCOPE_LOG_DIR': directory, 'NETSCOPE_LOG_LEVEL': 'WARNING', 'NETSCOPE_LOG_MAX_BYTES': '1024', 'NETSCOPE_LOG_BACKUPS': '2'}):
                configure_logging(self.logger, directory)
                self.assertEqual(self.logger.level, logging.WARNING)
                file_handler = self.logger.handlers[1]
                self.assertEqual((file_handler.maxBytes, file_handler.backupCount), (1024, 2))
                with patch('sys.stderr', io.StringIO()):
                    # Existing stream handler is redirected explicitly to keep test output quiet.
                    self.logger.handlers[0].setStream(io.StringIO())
                    self.logger.info('hidden', extra={'event': 'hidden_event'})
                    for _ in range(45):
                        self.logger.warning('rotation', extra={'event': 'rotation_test'})
                configure_logging(self.logger, directory)
                self.assertEqual(len(self.logger.handlers), 2)
                for handler in self.logger.handlers:
                    handler.close()
                files = list(Path(directory).glob('netscope.log*'))
                self.assertEqual(len(files), 3)
                for path in files:
                    self.assertLessEqual(path.stat().st_size, 1024)
                    for line in path.read_text(encoding='utf-8').splitlines():
                        self.assertEqual(json.loads(line)['event'], 'rotation_test')

    def test_invalid_configuration_fails_without_handlers_or_files(self):
        for variable, value in [('NETSCOPE_LOG_LEVEL', 'VERBOSE'), ('NETSCOPE_LOG_MAX_BYTES', '0'), ('NETSCOPE_LOG_BACKUPS', '0')]:
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as directory:
                with patch.dict('os.environ', {variable: value, 'NETSCOPE_LOG_DIR': str(Path(directory)/'new')}):
                    with self.assertRaises(ValueError):
                        configure_logging(self.logger, directory)
                    self.assertFalse((Path(directory)/'new').exists())

    def test_server_messages_cannot_expose_request_urls(self):
        server = logging.Logger('test-server')
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', {'NETSCOPE_LOG_DIR': directory}), patch('application_logging.logging.getLogger', return_value=server):
                configure_logging(self.logger, directory, include_server=True)
                self.logger.handlers[0].setStream(io.StringIO())
                server.info('GET /?password=secret HTTP/1.1')
                for handler in self.logger.handlers:
                    handler.close()
                data = (Path(directory)/'netscope.log').read_text(encoding='utf-8')
                self.assertNotIn('secret', data)
                self.assertEqual(json.loads(data)['event'], 'application_message')

    def test_batch_counts_and_expected_network_failure_are_info(self):
        pool = MonitoringCoordinator(logger=self.logger)
        self.addCleanup(pool.shutdown)
        def collect(target):
            self.logger.info('worker', extra={'event': 'worker_test'})
            return Measurement({'ping_ok': 0, 'tcp_ok': 0})
        report = pool.run_batch(lambda: [{'id': 1}], collect, lambda row: None)
        self.assertEqual(report, {'total': 1, 'completed': 1, 'failed': 0})
        events = self.events()
        self.assertEqual(len({event['batch_id'] for event in events}), 1)
        self.assertTrue(all(event['severity'] == 'INFO' for event in events))
        self.assertEqual(events[-1]['completed'], 1)
        self.assertGreaterEqual(events[-1]['elapsed_ms'], 0)

    def test_database_and_worker_failures_are_errors_without_sensitive_details(self):
        for failure, event, phase in [(sqlite3.OperationalError('secret'), 'database_failure', 'persist'),
                                      (RuntimeError('secret'), 'monitor_task_failure', 'collect')]:
            with self.subTest(event=event):
                self.stream.seek(0)
                self.stream.truncate()
                pool = MonitoringCoordinator(logger=self.logger)
                try:
                    collect = Mock(return_value=Measurement({}))
                    persist = Mock()
                    (persist if phase == 'persist' else collect).side_effect = failure
                    report = pool.run_batch(lambda: [{'id': 1}], collect, persist)
                    self.assertEqual(report['failed'], 1)
                    error = next(e for e in self.events() if e['event'] == event)
                    self.assertEqual((error['severity'], error['phase']), ('ERROR', phase))
                    self.assertEqual(self.events()[-1]['severity'], 'WARNING')
                    self.assertNotIn('secret', self.stream.getvalue())
                finally:
                    pool.shutdown()

    def test_load_failure_is_logged_and_reraised(self):
        pool = MonitoringCoordinator(logger=self.logger)
        self.addCleanup(pool.shutdown)
        with self.assertRaises(sqlite3.OperationalError):
            pool.run_batch(Mock(side_effect=sqlite3.OperationalError('secret')), Mock(), Mock())
        self.assertEqual(self.events()[-1]['event'], 'database_failure')
        self.assertEqual(self.events()[-1]['phase'], 'load')
        self.assertFalse(pool.batch_lock.locked())

    def test_probe_exception_is_error_but_normal_negative_is_quiet(self):
        target = dict(id=1, ip='127.0.0.1', port=80)
        with patch.object(app, 'ping', return_value=app.empty_icmp('timeout')), patch.object(app, 'tcp_check', return_value=(False, 'Connection timed out')), patch.object(app, 'app', Mock(logger=self.logger)):
            self.assertFalse(app.collect_measurement(target).execution_failed)
            self.assertEqual(self.events(), [])
        with patch.object(app, 'ping', side_effect=RuntimeError('secret')), patch.object(app, 'tcp_check', return_value=(True, None)), patch.object(app, 'app', Mock(logger=self.logger)):
            self.assertTrue(app.collect_measurement(target).execution_failed)
        self.assertEqual(self.events()[-1]['event'], 'probe_failure')

    def test_launcher_startup_shutdown_and_startup_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                self.stream.seek(0)
                self.stream.truncate()
                pool, thread = Mock(), Mock()
                with patch.dict('os.environ', {'NETSCOPE_DB': str(Path(directory)/'test.db')}), patch('application_logging.configure_logging'), patch('flask.Flask.logger', self.logger), patch('flask.Flask.run'), patch('monitoring_coordinator.MonitoringCoordinator', return_value=pool), patch('threading.Thread', return_value=thread), patch('schema_migrations.migrate_schema', side_effect=sqlite3.DatabaseError('secret') if failure else migrate_schema):
                    if failure:
                        with self.assertRaises(sqlite3.DatabaseError):
                            runpy.run_path(str(app.BASE/'app.py'), run_name='__main__')
                    else:
                        runpy.run_path(str(app.BASE/'app.py'), run_name='__main__')
                names = [event['event'] for event in self.events()]
                if failure:
                    self.assertEqual(names, ['application_starting', 'startup_failure'])
                    thread.start.assert_not_called()
                else:
                    self.assertEqual(names, ['application_starting', 'application_started', 'application_stopping', 'application_stopped'])
                pool.shutdown.assert_called_once()
