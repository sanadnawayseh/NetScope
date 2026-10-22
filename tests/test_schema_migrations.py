"""Migration-only checks. Every database is temporary; no network probes."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from schema_migrations import ADDITIONS


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'migration.db'
        override = patch.object(app, 'DB_PATH', self.path)
        override.start()
        self.addCleanup(override.stop)

    def legacy(self, additions=()):
        with app.get_db() as db:
            db.execute('CREATE TABLE targets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, port INTEGER NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE results (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE, checked_at TEXT NOT NULL, ping_ok INTEGER NOT NULL, latency_ms REAL, tcp_ok INTEGER NOT NULL, error TEXT)')
            db.execute("INSERT INTO targets VALUES(42,'Existing','127.0.0.1',80,'old')")
            db.execute("INSERT INTO results VALUES(7,42,'old',1,2.5,0,'refused')")
            for name in additions:
                db.execute(f'ALTER TABLE results ADD COLUMN {name} {ADDITIONS[name]}')
            if 'ping_error' in additions:
                db.execute("UPDATE results SET ping_error='preserved'")

    def snapshot(self):
        with app.get_db() as db:
            return (db.execute('PRAGMA user_version').fetchone()[0],
                    [tuple(r) for r in db.execute('SELECT * FROM targets')],
                    [tuple(r) for r in db.execute('SELECT * FROM results')],
                    [tuple(r) for r in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')])

    def test_fresh_initialization_wal_foreign_keys_and_cascade(self):
        app.init_db()
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT name FROM targets').fetchone()[0], 'Local computer')
            db.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(1,'now',1,1)")
            db.execute('DELETE FROM targets WHERE id=1')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(999,'now',1,1)")

    def test_upgrade_base_partial_and_fully_additive_v0_preserves_rows(self):
        for additions in ((), ('ping_error', 'icmp_sent'), tuple(ADDITIONS)):
            with self.subTest(additions=additions):
                # Each case gets an independent disposable file.
                self.path = self.path.with_name(f'legacy-{len(additions)}.db')
                with patch.object(app, 'DB_PATH', self.path):
                    self.legacy(additions)
                    before = self.snapshot()
                    app.init_db()
                    after = self.snapshot()
                    self.assertEqual(after[0], 1)
                    self.assertEqual(after[1], before[1])
                    self.assertEqual(after[2][0][:len(before[2][0])], before[2][0])
                    with app.get_db() as db:
                        self.assertTrue(ADDITIONS.keys() <= {r[1] for r in db.execute('PRAGMA table_info(results)')})
                    app.init_db()
                    self.assertEqual(self.snapshot(), after)

    def test_repeat_initialization_keeps_empty_inventory_empty(self):
        app.init_db()
        with app.get_db() as db:
            db.execute('DELETE FROM targets')
        before = self.snapshot()
        app.init_db()
        self.assertEqual(self.snapshot(), before)

    def test_newer_version_is_rejected_without_changes(self):
        self.legacy()
        with app.get_db() as db:
            db.execute('PRAGMA user_version=2')
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'Unsupported'):
            app.init_db()
        self.assertEqual(self.snapshot(), before)

    def test_unexpected_schema_is_rejected_without_changes(self):
        self.legacy()
        with app.get_db() as db:
            db.execute('ALTER TABLE results ADD COLUMN ping_error INTEGER')
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'Unexpected'):
            app.init_db()
        self.assertEqual(self.snapshot(), before)

    def test_ddl_failure_rolls_back_columns_and_version(self):
        self.legacy()
        with app.get_db() as db:
            # Index creation fails after all ALTER TABLE statements have run.
            db.execute('CREATE TABLE idx_results_target_time (value TEXT)')
        before = self.snapshot()
        with self.assertRaises(sqlite3.DatabaseError):
            app.init_db()
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_version_one_is_rejected(self):
        self.legacy()
        with app.get_db() as db:
            db.execute('PRAGMA user_version=1')
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'Incomplete'):
            app.init_db()
        self.assertEqual(self.snapshot(), before)

    def test_orphaned_results_are_rejected_without_changes(self):
        self.legacy()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE results SET target_id=999')
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'foreign key violation'):
            app.init_db()
        self.assertEqual(self.snapshot(), before)
