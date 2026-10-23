"""Durable incident-transition events; local read state, no delivery service."""
import re
import sqlite3
import uuid

DDL = [
    """CREATE TABLE notification_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL CHECK(event_type IN ('incident_opened','incident_resolved')),
        event_at TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT,
        UNIQUE(incident_id,event_type))""",
    'CREATE INDEX idx_notifications_unread ON notification_events(read_at,id)',
    """CREATE TABLE notification_meta (
        id INTEGER PRIMARY KEY CHECK(id=1), stream_id TEXT NOT NULL)""",
    """CREATE TRIGGER notify_incident_opened AFTER INSERT ON incidents
        WHEN NEW.state='OPEN'
        BEGIN
            INSERT OR IGNORE INTO notification_events(incident_id,event_type,event_at,created_at)
            VALUES(NEW.id,'incident_opened',NEW.latest_observed,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
        END""",
    """CREATE TRIGGER notify_incident_resolved AFTER UPDATE OF state ON incidents
        WHEN NEW.state='RESOLVED' AND OLD.state!='RESOLVED'
        BEGIN
            INSERT OR IGNORE INTO notification_events(incident_id,event_type,event_at,created_at)
            VALUES(NEW.id,'incident_resolved',NEW.resolved_at,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
        END""",
]


def migrate_notifications(conn, install):
    if install:
        for sql in DDL:
            conn.execute(sql)
        conn.execute('INSERT INTO notification_meta VALUES(1,?)',(uuid.uuid4().hex,))
    canonical=lambda value:re.sub(r'\s+',' ',value).strip().rstrip(';')
    for sql in DDL:
        name=sql.split()[2]
        row=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
        if row is None or canonical(row[0])!=canonical(sql):
            raise sqlite3.DatabaseError('Unexpected notification schema; initialization stopped')
    rows=conn.execute('SELECT stream_id FROM notification_meta').fetchall()
    if len(rows)!=1 or not re.fullmatch(r'[0-9a-f]{32}',rows[0][0]):
        raise sqlite3.DatabaseError('Invalid notification stream metadata')


def unread_count(conn):
    return conn.execute('SELECT COUNT(*) FROM notification_events WHERE read_at IS NULL').fetchone()[0]


def recent_events(conn, limit, after=None, before=None):
    conn.execute('BEGIN')
    clauses,values=[],[]
    if after is not None:
        clauses.append('n.id>?');values.append(after)
    if before is not None:
        clauses.append('n.id<?');values.append(before)
    query='''SELECT n.*,i.condition,i.target_id,i.original_target_id FROM notification_events n
             JOIN incidents i ON i.id=n.incident_id'''
    if clauses:query+=' WHERE '+' AND '.join(clauses)
    query+=' ORDER BY n.id '+('ASC' if after is not None else 'DESC')+' LIMIT ?'
    rows=conn.execute(query,(*values,limit+1)).fetchall()
    more=len(rows)>limit
    events=[dict(row) for row in rows[:limit]]
    if after is None:events.reverse()
    latest=conn.execute('SELECT COALESCE(MAX(id),0) FROM notification_events').fetchone()[0]
    return dict(events=events,unread_count=unread_count(conn),latest_id=latest,
                stream_id=conn.execute('SELECT stream_id FROM notification_meta').fetchone()[0],
                next_after_id=events[-1]['id'] if events else (after if after is not None else latest),
                next_before_id=events[0]['id'] if more and after is None else None,
                has_more=more if after is not None else False)


def mark_read(conn, event_id=None, up_to_id=None):
    conn.execute('BEGIN IMMEDIATE')
    stamp=conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00','now')").fetchone()[0]
    if event_id is not None:
        row=conn.execute('SELECT id FROM notification_events WHERE id=?',(event_id,)).fetchone()
        if row is None:return None
        conn.execute('UPDATE notification_events SET read_at=? WHERE id=? AND read_at IS NULL',(stamp,event_id))
        result=dict(conn.execute('SELECT id,read_at FROM notification_events WHERE id=?',(event_id,)).fetchone())
    else:
        conn.execute('UPDATE notification_events SET read_at=? WHERE id<=? AND read_at IS NULL',(stamp,up_to_id))
        result=dict(up_to_id=up_to_id,read_at=stamp)
    result['unread_count']=unread_count(conn)
    return result
