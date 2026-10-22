"""Versioned, additive SQLite migrations; the caller owns the transaction."""
import sqlite3

SCHEMA_VERSION = 1
TARGETS = {'id': 'INTEGER', 'name': 'TEXT', 'ip': 'TEXT',
           'port': 'INTEGER', 'created_at': 'TEXT'}
RESULTS = {'id': 'INTEGER', 'target_id': 'INTEGER', 'checked_at': 'TEXT',
           'ping_ok': 'INTEGER', 'latency_ms': 'REAL', 'tcp_ok': 'INTEGER', 'error': 'TEXT'}
ADDITIONS = dict(ping_error='TEXT', tcp_error='TEXT', latency_source='TEXT',
                 latency_upper_bound_ms='REAL', icmp_sent='INTEGER',
                 icmp_received='INTEGER', icmp_unanswered='INTEGER',
                 packet_loss_pct='REAL', rtt_min_ms='REAL', rtt_max_ms='REAL',
                 rtt_sample_count='INTEGER')


def validate_table(conn, table, required, optional=None):
    columns = {row[1]: row for row in conn.execute(f'PRAGMA table_info({table})')}
    allowed = dict(required, **(optional or {}))
    if (not required.keys() <= columns.keys() or columns.keys() - allowed.keys()
            or any(row[2].upper() != allowed[name] for name, row in columns.items())
            or columns['id'][5] != 1
            or any(row[5] for name, row in columns.items() if name != 'id')):
        raise sqlite3.DatabaseError(f'Unexpected NetScope {table} schema; migration stopped')
    return columns


def migrate_schema(conn):
    """Upgrade v0 to v1, or validate v1. Return whether to seed a fresh DB.

    Never commit here: schema, version and default seed form one transaction.
    Existing v0 columns may be any subset of the historical additions.
    """
    if not conn.in_transaction:
        raise sqlite3.DatabaseError('Schema migration requires an explicit transaction')
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise sqlite3.DatabaseError('Schema migration requires foreign key enforcement')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise sqlite3.DatabaseError(f'Unsupported NetScope schema version {version}')
    objects = dict(conn.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    fresh = not objects
    if fresh and version == 0:
        conn.execute('CREATE TABLE targets (id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL, port INTEGER NOT NULL, created_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE results (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE, checked_at TEXT NOT NULL, ping_ok INTEGER NOT NULL, latency_ms REAL, tcp_ok INTEGER NOT NULL, error TEXT)')
    elif objects.get('targets') != 'table' or objects.get('results') != 'table':
        raise sqlite3.DatabaseError('Unexpected NetScope tables; migration stopped')
    validate_table(conn, 'targets', TARGETS)
    columns = validate_table(conn, 'results', RESULTS, ADDITIONS)
    foreign_keys = list(conn.execute('PRAGMA foreign_key_list(results)'))
    if (len(foreign_keys) != 1 or foreign_keys[0][2:5] != ('targets', 'target_id', 'id')
            or foreign_keys[0][6].upper() != 'CASCADE'):
        raise sqlite3.DatabaseError('Unexpected results foreign key; migration stopped')
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise sqlite3.DatabaseError('Existing foreign key violation; migration stopped')
    if version == 0:
        for name, kind in ADDITIONS.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE results ADD COLUMN {name} {kind}')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_results_target_time ON results(target_id, id DESC)')
    elif not ADDITIONS.keys() <= columns.keys():
        raise sqlite3.DatabaseError('Incomplete NetScope version 1 schema')
    index = list(conn.execute('PRAGMA index_xinfo(idx_results_target_time)'))
    keys = [(row[2], row[3], row[4]) for row in index if row[5]]
    indexes = list(conn.execute('PRAGMA index_list(results)'))
    if (keys != [('target_id', 0, 'BINARY'), ('id', 1, 'BINARY')]
            or not any(row[1] == 'idx_results_target_time' and not row[2] and not row[4]
                       for row in indexes)):
        raise sqlite3.DatabaseError('Unexpected results history index; migration stopped')
    if version == 0:
        conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    return fresh
