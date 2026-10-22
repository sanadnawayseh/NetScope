"""Repeatable simulated I/O benchmark; uses only disposable SQLite databases.

Run: python -B tests/benchmark_monitoring.py --repeats 5
No real ping, TCP connection, discovery, or production monitoring data is used.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from monitoring_coordinator import MonitoringCoordinator


def run_once(workers, workload, uniform_ms=100, fast_ms=50, slow_ms=300):
    previous = app.DB_PATH
    with tempfile.TemporaryDirectory() as directory:
        app.DB_PATH = Path(directory) / 'benchmark.db'
        try:
            app.init_db()
            with app.get_db() as db:
                db.executemany('INSERT INTO targets(name,ip,port,created_at) VALUES(?,?,?,?)',
                               [(f'Simulated {n}', f'127.0.0.{n}', 80, app.utc_now()) for n in range(2, 21)])
                targets = [dict(row) for row in db.execute('SELECT * FROM targets')]
            active, maximum = 0, 0
            guard, timer = threading.Lock(), threading.Event()
            good = dict(ping_ok=True, latency_ms=2.0, latency_upper_bound_ms=None,
                        ping_error=None, icmp_sent=5, icmp_received=5,
                        icmp_unanswered=0, packet_loss_pct=0.0,
                        rtt_min_ms=2.0, rtt_max_ms=2.0, rtt_sample_count=5)
            def simulated_ping(ip):
                nonlocal active, maximum
                number = int(ip.split('.')[-1])
                delay = (uniform_ms if workload == 'uniform' else slow_ms if number % 5 == 0 else fast_ms) / 1000
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    timer.wait(delay)  # Intentional simulated I/O cost, not a correctness assertion.
                    return dict(good)
                finally:
                    with guard:
                        active -= 1
            pool = MonitoringCoordinator(workers)
            try:
                with patch.object(app, 'ping', side_effect=simulated_ping), patch.object(app, 'tcp_check', return_value=(True, None)):
                    started = time.perf_counter()
                    report = pool.run_batch(lambda: targets, app.collect_measurement, app.persist_measurement)
                    elapsed = time.perf_counter() - started
            finally:
                pool.shutdown()
            with app.get_db() as db:
                count = db.execute('SELECT COUNT(*) FROM results').fetchone()[0]
                unique = db.execute('SELECT COUNT(DISTINCT target_id) FROM results').fetchone()[0]
            if report != {'total': 20, 'completed': 20, 'failed': 0} or count != 20 or unique != 20 or maximum > workers:
                raise AssertionError('Benchmark correctness/worker bound failed')
            return dict(seconds=elapsed, persisted=count, failed=report['failed'], maximum_active=maximum)
        finally:
            app.DB_PATH = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--uniform-ms', type=int, default=100)
    parser.add_argument('--fast-ms', type=int, default=50)
    parser.add_argument('--slow-ms', type=int, default=300)
    args = parser.parse_args()
    if not 3 <= args.repeats <= 20:
        parser.error('--repeats must be between 3 and 20')
    if any(not 1 <= delay <= 1000 for delay in (args.uniform_ms, args.fast_ms, args.slow_ms)):
        parser.error('Simulated delays must be between 1 and 1000 milliseconds')
    output = {}
    for workload in ('uniform', 'mixed'):
        measurements = {1: [], 4: []}
        # Alternate modes to reduce order effects from filesystem/OS warmup.
        for repetition in range(args.repeats):
            for workers in ((1, 4) if repetition % 2 == 0 else (4, 1)):
                measurements[workers].append(run_once(workers, workload, args.uniform_ms, args.fast_ms, args.slow_ms))
        output[workload] = {str(workers): {
            'median_seconds': round(statistics.median(row['seconds'] for row in rows), 6),
            'runs': [{**row, 'seconds': round(row['seconds'], 6)} for row in rows],
        } for workers, rows in measurements.items()}
    print(json.dumps(output, indent=2))
    if any(result['4']['median_seconds'] >= result['1']['median_seconds'] for result in output.values()):
        raise SystemExit('Concurrent median was not lower; investigate rather than assuming speedup')


if __name__ == '__main__':
    main()
