"""Integrity checks use disposable databases and never run network probes."""
import sqlite3
import unittest
from unittest.mock import patch

import app
from schema_migrations import migrate_schema
import test_app


class DatabaseIntegrityTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def row(self, **changes):
        row = dict(target_id=1, checked_at=app.utc_now(), ping_ok=1, tcp_ok=0,
                   latency_ms=2.0, rtt_min_ms=1.0, rtt_max_ms=3.0,
                   icmp_sent=5, icmp_received=4, icmp_unanswered=1,
                   packet_loss_pct=20.0, rtt_sample_count=4, latency_source='icmp_rtt')
        return dict(row, **changes)

    def count(self):
        with app.get_db() as db:
            return db.execute('SELECT COUNT(*) FROM results').fetchone()[0]

    def test_invalid_measurements_do_not_change_existing_history(self):
        app.persist_measurement(self.row())
        invalid = [dict(target_id=0), dict(target_id=True), dict(checked_at='bad'),
                   dict(checked_at='2026-10-22'), dict(ping_ok=2), dict(tcp_ok=True),
                   dict(latency_ms=float('nan')), dict(latency_ms=float('inf')),
                   dict(latency_ms=-1), dict(packet_loss_pct=101), dict(icmp_sent=4),
                   dict(icmp_received=6), dict(icmp_unanswered=0), dict(ping_ok=0),
                   dict(rtt_sample_count=5), dict(rtt_min_ms=4), dict(rtt_max_ms=1),
                   dict(icmp_received=None), dict(latency_upper_bound_ms=1),
                   dict(ping_error=123), {'untrusted_column': 1}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(sqlite3.IntegrityError):
                app.persist_measurement(self.row(**change))
        self.assertEqual(self.count(), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            app.persist_measurement({'target_id': 1})

    def test_unknown_and_bounded_measurements_remain_supported(self):
        unknown = dict(target_id=1, checked_at=app.utc_now(), tcp_ok=1,
                       **app.empty_icmp('Incomplete statistics'))
        unknown['ping_ok'] = 0
        app.persist_measurement(unknown)
        app.persist_measurement(self.row(latency_ms=None, rtt_min_ms=None,
                                        rtt_max_ms=None, latency_upper_bound_ms=1))
        self.assertEqual(self.count(), 2)

    def test_orphan_write_fails_and_next_write_succeeds(self):
        with self.assertRaises(sqlite3.IntegrityError):
            app.persist_measurement(self.row(target_id=999))
        app.persist_measurement(self.row())
        self.assertEqual(self.count(), 1)

    def test_transaction_rolls_back_earlier_write_on_constraint_failure(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with app.get_db() as db:
                db.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(1,'now',1,1)")
                db.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(999,'now',1,1)")
        self.assertEqual(self.count(), 0)

    def test_migration_requires_transaction_and_foreign_keys(self):
        with sqlite3.connect(':memory:') as db:
            with self.assertRaisesRegex(sqlite3.DatabaseError, 'explicit transaction'):
                migrate_schema(db)
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(sqlite3.DatabaseError, 'foreign key enforcement'):
                migrate_schema(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)

    def test_bad_index_rejected_for_both_versions_preserving_data(self):
        app.persist_measurement(self.row())
        for version in (0, 1):
            with self.subTest(version=version):
                with app.get_db() as db:
                    db.execute('DROP INDEX idx_results_target_time')
                    db.execute('CREATE INDEX idx_results_target_time ON results(target_id COLLATE NOCASE,id DESC)')
                    db.execute(f'PRAGMA user_version={version}')
                with self.assertRaisesRegex(sqlite3.DatabaseError, 'history index'):
                    app.init_db()
                self.assertEqual(self.count(), 1)
                with app.get_db() as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], version)
                    db.execute('DROP INDEX idx_results_target_time')
                    db.execute('CREATE INDEX idx_results_target_time ON results(target_id,id DESC)')

    def test_seed_failure_rolls_back_fresh_schema_and_version(self):
        path = app.DB_PATH.with_name('seed-failure.db')
        with patch.object(app, 'DB_PATH', path), patch.object(app, 'utc_now', side_effect=RuntimeError('seed failure')):
            with self.assertRaisesRegex(RuntimeError, 'seed failure'):
                app.init_db()
            with app.get_db() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0], 0)

    def test_database_failure_returns_safe_json_and_preserves_history(self):
        app.persist_measurement(self.row())
        with patch.object(app, 'get_db', side_effect=sqlite3.OperationalError('private path')), patch.object(app.app.logger, 'exception'):
            response = self.client.get('/api/targets')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private path', response.get_data(as_text=True))
        self.assertEqual(self.count(), 1)
