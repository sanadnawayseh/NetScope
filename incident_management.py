"""Persistent, observation-driven incident state. No probes or notifications."""
from datetime import datetime, timezone
import json
import math
import os
import re
import sqlite3

CONDITIONS = ('icmp_unavailable', 'tcp_unavailable', 'packet_loss', 'high_latency')
DEFAULTS = dict(icmp_unavailable=0, tcp_unavailable=0, packet_loss=40, high_latency=200)
DDL = [
    """CREATE TABLE alert_config (
        condition TEXT PRIMARY KEY CHECK(condition IN ('icmp_unavailable','tcp_unavailable','packet_loss','high_latency')),
        threshold REAL NOT NULL CHECK(threshold>=0 AND threshold<=60000),
        open_checks INTEGER NOT NULL CHECK(open_checks BETWEEN 1 AND 100),
        recovery_checks INTEGER NOT NULL CHECK(recovery_checks BETWEEN 1 AND 100),
        max_gap_seconds INTEGER NOT NULL CHECK(max_gap_seconds BETWEEN 30 AND 86400),
        revision INTEGER NOT NULL CHECK(revision>=1),
        CHECK(condition!='packet_loss' OR threshold<=100))""",
    """CREATE TABLE incidents (
        id INTEGER PRIMARY KEY,
        target_id INTEGER REFERENCES targets(id) ON DELETE SET NULL,
        original_target_id INTEGER NOT NULL,
        condition TEXT NOT NULL REFERENCES alert_config(condition),
        state TEXT NOT NULL CHECK(state IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
        first_observed TEXT NOT NULL, latest_observed TEXT NOT NULL,
        resolved_at TEXT, duration_seconds REAL,
        triggering_result_id INTEGER NOT NULL, latest_result_id INTEGER NOT NULL,
        resolving_result_id INTEGER,
        triggering_measurements TEXT NOT NULL, thresholds TEXT NOT NULL, recovery TEXT,
        CHECK((state IN ('OPEN','ACKNOWLEDGED') AND resolved_at IS NULL AND duration_seconds IS NULL)
           OR (state='RESOLVED' AND resolved_at IS NOT NULL AND duration_seconds IS NOT NULL AND duration_seconds>=0)))""",
    """CREATE UNIQUE INDEX idx_incidents_active ON incidents(target_id,condition)
        WHERE state IN ('OPEN','ACKNOWLEDGED')""",
    'CREATE INDEX idx_incidents_time ON incidents(first_observed,id)',
    'CREATE INDEX idx_incidents_target ON incidents(target_id,first_observed,id)',
    """CREATE TABLE incident_cursors (
        target_id INTEGER PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
        last_result_id INTEGER NOT NULL DEFAULT 0, latest_observed TEXT)""",
    """CREATE TABLE incident_streaks (
        target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
        condition TEXT NOT NULL REFERENCES alert_config(condition),
        bad_count INTEGER NOT NULL DEFAULT 0 CHECK(bad_count>=0),
        good_count INTEGER NOT NULL DEFAULT 0 CHECK(good_count>=0),
        first_bad TEXT, first_evidence TEXT, revision INTEGER NOT NULL,
        PRIMARY KEY(target_id,condition))""",
]
ACK_COLUMNS = [('acknowledged_at','TEXT'),
               ('operator_note',"TEXT CHECK(operator_note IS NULL OR (typeof(operator_note)='text' AND length(operator_note)<=500))")]


def migrate_incidents(conn, install, acknowledgements=False):
    if install:
        for sql in DDL:
            conn.execute(sql)
        conn.executemany('INSERT INTO alert_config VALUES(?,?,3,2,300,1)', DEFAULTS.items())
        # Begin prospectively; never infer incidents from pre-upgrade history.
        conn.execute('''INSERT INTO incident_cursors(target_id,last_result_id)
            SELECT t.id,COALESCE(MAX(r.id),0) FROM targets t LEFT JOIN results r
            ON r.target_id=t.id GROUP BY t.id''')
    # Check constraints/index definitions as well as column names on v2 restart.
    canonical = lambda value: re.sub(r'\s+', ' ', value).strip()
    for sql in DDL:
        name = sql.split()[3] if sql.startswith('CREATE UNIQUE') else sql.split()[2]
        if name=='incidents' and acknowledgements:
            sql=sql.replace('recovery TEXT,','recovery TEXT, '+', '.join(column+' '+kind for column,kind in ACK_COLUMNS)+',')
        row = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if row is None or canonical(row[0]) != canonical(sql):
            raise sqlite3.DatabaseError('Unexpected incident schema; initialization stopped')
    if {r[0] for r in conn.execute('SELECT condition FROM alert_config')} != set(CONDITIONS):
        raise sqlite3.DatabaseError('Incomplete alert configuration')


def migrate_acknowledgements(conn):
    for name,kind in ACK_COLUMNS:
        conn.execute(f'ALTER TABLE incidents ADD COLUMN {name} {kind}')


class IncidentNotFound(Exception):
    pass


class IncidentConflict(Exception):
    pass


def acknowledge(get_db, incident_id, note, logger):
    changed=False
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM incidents WHERE id=?',(incident_id,)).fetchone()
        if row is None:
            raise IncidentNotFound()
        if row['acknowledged_at'] is None:
            if row['state']=='RESOLVED':
                raise IncidentConflict()
            stamp=datetime.now(timezone.utc).isoformat(timespec='microseconds')
            conn.execute("UPDATE incidents SET state='ACKNOWLEDGED',acknowledged_at=?,operator_note=? WHERE id=?",
                         (stamp,note,incident_id))
            changed=True
        result=dict(conn.execute('SELECT * FROM incidents WHERE id=?',(incident_id,)).fetchone())
    if changed:
        logger.info('Incident acknowledged',extra={'event':'incident_acknowledged','incident_id':incident_id,'condition':result['condition']})
    return result


def configure(conn, environ=None):
    """Explicit environment overrides persist; absent variables keep DB settings."""
    environ = os.environ if environ is None else environ
    specs = {'NETSCOPE_INCIDENT_OPEN_CHECKS': ('open_checks',1,100),
             'NETSCOPE_INCIDENT_RECOVERY_CHECKS': ('recovery_checks',1,100),
             'NETSCOPE_INCIDENT_MAX_GAP_SECONDS': ('max_gap_seconds',30,86400),
             'NETSCOPE_INCIDENT_LOSS_PCT': ('threshold',0,100),
             'NETSCOPE_INCIDENT_LATENCY_MS': ('threshold',0,60000)}
    updates = {}
    for variable, (field,low,high) in specs.items():
        if variable not in environ:
            continue
        try:
            value = float(environ[variable])
            if not math.isfinite(value) or not low<=value<=high or (field!='threshold' and not value.is_integer()):
                raise ValueError()
        except (ValueError,TypeError,OverflowError):
            raise ValueError(f'{variable} must be between {low} and {high}') from None
        updates[variable] = int(value) if field!='threshold' else value
    for row in conn.execute('SELECT * FROM alert_config').fetchall():
        data = dict(row)
        for variable,value in updates.items():
            if variable=='NETSCOPE_INCIDENT_LOSS_PCT' and data['condition']!='packet_loss':
                continue
            if variable=='NETSCOPE_INCIDENT_LATENCY_MS' and data['condition']!='high_latency':
                continue
            data[specs[variable][0]] = value
        fields = ('threshold','open_checks','recovery_checks','max_gap_seconds')
        if any(data[field]!=row[field] for field in fields):
            conn.execute('''UPDATE alert_config SET threshold=?,open_checks=?,recovery_checks=?,
                max_gap_seconds=?,revision=revision+1 WHERE condition=?''',
                (*[data[field] for field in fields],data['condition']))


def observation(row, condition, states):
    if condition in ('icmp_unavailable','tcp_unavailable'):
        state = states['icmp' if condition=='icmp_unavailable' else 'tcp']
        return None if state=='unknown' else int(state=='failure')
    if states['icmp']=='unknown':
        return None
    if condition=='packet_loss':
        return row['packet_loss_pct']
    value = row['latency_ms']
    return value if (states['icmp']=='success' and row['latency_upper_bound_ms'] is None
                     and type(value) in (float,int) and math.isfinite(value) and value>=0) else None


def evaluate_target(get_db, target_id, classify, normalize_time, logger):
    """Consume committed rows atomically. Stale/duplicate instants never qualify."""
    events = []
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM targets WHERE id=?',(target_id,)).fetchone():
            return
        conn.execute('INSERT OR IGNORE INTO incident_cursors(target_id) VALUES(?)',(target_id,))
        cursor = conn.execute('SELECT * FROM incident_cursors WHERE target_id=?',(target_id,)).fetchone()
        last_id, watermark = cursor['last_result_id'], cursor['latest_observed']
        configs = [dict(r) for r in conn.execute('SELECT * FROM alert_config')]
        rows = conn.execute('''SELECT * FROM results WHERE target_id=? AND id>?
            ORDER BY julianday(checked_at),id''',(target_id,last_id))
        for raw in rows:
            row = dict(raw)
            last_id = max(last_id,row['id'])
            instant = normalize_time(row['checked_at'])
            if instant is None:
                # Unplaceable observations interrupt pending streaks, never incidents.
                conn.execute('UPDATE incident_streaks SET bad_count=0,good_count=0,first_bad=NULL,first_evidence=NULL WHERE target_id=?',(target_id,))
                continue
            current = datetime.fromisoformat(instant).astimezone(timezone.utc)
            if watermark and current <= datetime.fromisoformat(watermark):
                continue
            gap = (current-datetime.fromisoformat(watermark)).total_seconds() if watermark else 0
            watermark = current.isoformat(timespec='microseconds')
            states = classify(row)
            for config in configs:
                condition = config['condition']
                conn.execute('INSERT OR IGNORE INTO incident_streaks(target_id,condition,revision) VALUES(?,?,?)',
                             (target_id,condition,config['revision']))
                streak = dict(conn.execute('SELECT * FROM incident_streaks WHERE target_id=? AND condition=?',(target_id,condition)).fetchone())
                if streak['revision']!=config['revision'] or gap>config['max_gap_seconds']:
                    streak.update(bad_count=0,good_count=0,first_bad=None,first_evidence=None)
                value = observation(row,condition,states)
                active = conn.execute("SELECT * FROM incidents WHERE target_id=? AND condition=? AND state IN ('OPEN','ACKNOWLEDGED')",(target_id,condition)).fetchone()
                evidence = dict(result_id=row['id'],observed_at=watermark,value=value)
                if value is None:
                    streak.update(bad_count=0,good_count=0,first_bad=None,first_evidence=None)
                elif value>config['threshold']:
                    if not streak['bad_count']:
                        streak.update(first_bad=watermark,first_evidence=json.dumps(evidence))
                    streak['bad_count'] = min(streak['bad_count']+1,config['open_checks'])
                    streak['good_count'] = 0
                    if active is None and streak['bad_count']>=config['open_checks']:
                        result = conn.execute('''INSERT INTO incidents(target_id,original_target_id,condition,state,
                            first_observed,latest_observed,triggering_result_id,latest_result_id,triggering_measurements,thresholds)
                            VALUES(?,?,?,'OPEN',?,?,?,?,?,?)''',
                            (target_id,target_id,condition,streak['first_bad'],watermark,row['id'],row['id'],
                             json.dumps(dict(first=json.loads(streak['first_evidence']),trigger=evidence)),json.dumps(config)))
                        events.append(('incident_opened',result.lastrowid,condition))
                else:
                    streak.update(bad_count=0,first_bad=None,first_evidence=None)
                    streak['good_count'] = min(streak['good_count']+1,config['recovery_checks'])
                    if active and streak['good_count']>=config['recovery_checks']:
                        duration = (current-datetime.fromisoformat(active['first_observed'])).total_seconds()
                        conn.execute("""UPDATE incidents SET state='RESOLVED',resolved_at=?,duration_seconds=?,
                            resolving_result_id=?,recovery=? WHERE id=?""",
                            (watermark,duration,row['id'],json.dumps(dict(measurement=evidence,thresholds=config)),active['id']))
                        events.append(('incident_resolved',active['id'],condition))
                if active and value is not None:
                    conn.execute('UPDATE incidents SET latest_observed=?,latest_result_id=? WHERE id=?',(watermark,row['id'],active['id']))
                conn.execute('''UPDATE incident_streaks SET bad_count=?,good_count=?,first_bad=?,first_evidence=?,revision=?
                    WHERE target_id=? AND condition=?''', (streak['bad_count'],streak['good_count'],streak['first_bad'],
                    streak['first_evidence'],config['revision'],target_id,condition))
        conn.execute('UPDATE incident_cursors SET last_result_id=?,latest_observed=? WHERE target_id=?',(last_id,watermark,target_id))
    # Emit transitions only after COMMIT, never for rolled-back state.
    for event,incident_id,condition in events:
        logger.info('Incident transition',extra=dict(event=event,incident_id=incident_id,condition=condition))


def process_pending(get_db, classify, normalize_time, logger, target_id=None):
    """Retry retained cursors after restart or a later successful measurement."""
    try:
        if target_id is None:
            with get_db() as conn:
                ids = [r[0] for r in conn.execute('''SELECT t.id FROM targets t
                    LEFT JOIN incident_cursors c ON c.target_id=t.id WHERE EXISTS
                    (SELECT 1 FROM results r WHERE r.target_id=t.id AND r.id>COALESCE(c.last_result_id,0))''')]
        else:
            ids = [target_id]
        for target in ids:
            try:
                evaluate_target(get_db,target,classify,normalize_time,logger)
            except Exception:
                # Secondary evaluation must not reclassify a committed measurement
                # as a failed write. Leave its cursor untouched for a retry.
                logger.exception('Incident evaluation failed',extra={'event':'incident_evaluation_failure'})
    except sqlite3.Error:
        logger.exception('Incident pending lookup failed',extra={'event':'incident_evaluation_failure'})
