"""Bounded chart payloads and streaming summaries over indexed time windows."""
from collections import deque
from datetime import datetime, timedelta, timezone
import math
import time

RANGES = {'1h': 1, '6h': 6, '24h': 24, '7d': 168, '30d': 720}
POINT_LIMIT = 2000
# Avoid SQLite's non-deterministic date aliases (e.g. legacy text 'now').
# This trusted expression is shared verbatim by the index and range queries.
TIME_SQL = "CASE WHEN checked_at GLOB '[0-9][0-9][0-9][0-9]-*' THEN julianday(checked_at) END"


class AnalyticsLimit(Exception):
    pass


def time_window(value, now=None):
    if value not in RANGES:
        raise ValueError('Range must be 1h, 6h, 24h, 7d or 30d')
    end = now or datetime.now(timezone.utc)
    return (end - timedelta(hours=RANGES[value])).isoformat(), end.isoformat()


def query_history(conn, target_id, start, end, classify, normalize_time, deadline):
    """Summaries cover the full window; only the newest POINT_LIMIT dots return."""
    points = deque(maxlen=POINT_LIMIT)
    count = valid = invalid_times = exact_count = loss_count = 0
    latency_sum = loss_sum = 0.0
    minimum = maximum = None
    protocols = {p: dict(successful_checks=0, valid_checks=0, excluded_checks=0) for p in ('icmp', 'tcp')}
    cursor = conn.execute(f'''SELECT * FROM results WHERE target_id=?
        AND ({TIME_SQL})>=julianday(?) AND ({TIME_SQL})<=julianday(?)
        ORDER BY ({TIME_SQL}), id''', (target_id, start, end))
    for raw in cursor:
        if count % 256 == 0 and time.monotonic() > deadline:
            raise AnalyticsLimit('History query exceeded the local analysis budget; select fewer devices or a shorter range.')
        row = dict(raw)
        count += 1
        instant = normalize_time(row['checked_at'])
        states = classify(row)
        if instant is None:
            invalid_times += 1
        if any(state != 'unknown' for state in states.values()):
            valid += 1
        for protocol, state in states.items():
            metric = protocols[protocol]
            metric['excluded_checks' if state == 'unknown' else 'valid_checks'] += 1
            metric['successful_checks'] += state == 'success'
        latency = row.get('latency_ms')
        if not (states['icmp'] == 'success' and row.get('latency_upper_bound_ms') is None
                and type(latency) in (int, float) and math.isfinite(latency) and latency >= 0):
            latency = None
        if latency is not None:
            exact_count += 1
            latency_sum += latency
            minimum = latency if minimum is None else min(minimum, latency)
            maximum = latency if maximum is None else max(maximum, latency)
        loss = row.get('packet_loss_pct') if states['icmp'] != 'unknown' else None
        if loss is not None:
            loss_count += 1
            loss_sum += loss
        points.append(dict(id=row['id'], checked_at=instant, latency_ms=latency,
                           packet_loss_pct=loss, ping_ok=row['ping_ok'], tcp_ok=row['tcp_ok'],
                           icmp_check_status=states['icmp'], tcp_check_status=states['tcp']))
    for metric in protocols.values():
        metric['percentage'] = metric['successful_checks'] * 100 / metric['valid_checks'] if metric['valid_checks'] else None
    summary = dict(record_count=count, valid_measurements=valid, unknown_measurements=count-valid,
                   exact_rtt_count=exact_count, missing_rtt_count=count-exact_count,
                   average_latency_ms=latency_sum/exact_count if exact_count else None,
                   minimum_latency_ms=minimum, maximum_latency_ms=maximum,
                   valid_loss_count=loss_count, missing_loss_count=count-loss_count,
                   average_packet_loss_pct=loss_sum/loss_count if loss_count else None,
                   invalid_timestamp_count=invalid_times, **protocols)
    return dict(points=list(points), summary=summary, truncated=count > POINT_LIMIT,
                returned_points=len(points))
