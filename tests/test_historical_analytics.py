from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import app
import historical_analytics as analytics
import test_app

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


class HistoricalAnalyticsTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def request(self, query='targets=1&range=1h'):
        with patch.object(app, 'time_window', side_effect=lambda value: analytics.time_window(value, NOW)):
            return self.client.get('/api/history?' + query)

    def insert(self, **changes):
        row = dict(target_id=1, checked_at='2026-10-22T11:30:00+00:00', ping_ok=1,
                   tcp_ok=1, latency_source='icmp_rtt', latency_ms=2.0,
                   icmp_sent=5, icmp_received=5, icmp_unanswered=0, packet_loss_pct=0.0)
        row.update(changes)
        with app.get_db() as db:
            db.execute(f"INSERT INTO results ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",tuple(row.values()))

    def test_range_and_device_validation(self):
        for value in analytics.RANGES:
            self.assertEqual(self.request('targets=1&range='+value).status_code, 200)
        for query in ('targets=1&range=year','targets=1&range=1h&range=6h','targets=1,1&range=1h',
                      'targets=1,2,3,4,5&range=1h','targets=1%20OR%201=1&range=1h',
                      'targets=0&range=1h','range=1h','targets=1&range=1h&start=bad'):
            with self.subTest(query=query):
                self.assertEqual(self.request(query).status_code,400)
        self.assertEqual(self.request('targets=999&range=1h').status_code,404)

    def test_timestamp_order_offsets_boundaries_and_legacy_contract(self):
        self.insert(checked_at='2026-10-22T11:45:00Z')
        self.insert(checked_at='2026-10-22T14:00:00+03:00')  # 11:00 UTC; inserted later.
        self.insert(checked_at='2026-10-22T12:01:00Z')
        self.insert(checked_at='2026-10-22T10:59:59Z')
        result=self.request().json
        points=result['series'][0]['points']
        self.assertEqual([p['id'] for p in points],[2,1])
        self.assertEqual(result['series'][0]['summary']['record_count'],2)
        self.assertIsInstance(self.client.get('/api/targets/1/history').json,list)
        with patch.object(app,'time_window',return_value=analytics.time_window('1h',NOW)):
            self.assertEqual([p['id'] for p in self.client.get('/api/targets/1/history?range=1h').json],[2,1])
        self.assertEqual(self.client.get('/api/targets/1/history?range=bad').status_code,400)

    def test_full_window_summary_excludes_bound_and_unknown_rtt(self):
        self.insert()
        self.insert(latency_ms=None,latency_upper_bound_ms=1)
        self.insert(ping_ok=0,latency_ms=None,icmp_received=0,icmp_unanswered=5,packet_loss_pct=100,
                    tcp_ok=0,tcp_error='Connection refused (port is not accepting connections)')
        self.insert(latency_source=None,latency_ms=999,icmp_sent=None,icmp_received=None,
                    icmp_unanswered=None,packet_loss_pct=None)
        series=self.request().json['series'][0]
        s=series['summary']
        self.assertEqual((s['record_count'],s['valid_measurements'],s['unknown_measurements']),(4,3,1))
        self.assertEqual((s['exact_rtt_count'],s['average_latency_ms'],s['minimum_latency_ms'],s['maximum_latency_ms']),(1,2,2,2))
        self.assertAlmostEqual(s['average_packet_loss_pct'],100/3)
        self.assertAlmostEqual(s['icmp']['percentage'],200/3)
        self.assertEqual(s['tcp']['valid_checks'],3)
        self.assertEqual(s['icmp']['excluded_checks'],1)
        self.assertIsNone(series['points'][1]['latency_ms'])

    def test_limits_apply_to_points_not_full_window_summaries(self):
        for number in range(6):
            self.insert(latency_ms=number)
        with patch.object(analytics,'POINT_LIMIT',3):
            series=self.request().json['series'][0]
        self.assertEqual(series['returned_points'],3)
        self.assertTrue(series['truncated'])
        self.assertEqual(series['summary']['record_count'],6)
        self.assertEqual(series['summary']['average_latency_ms'],2.5)
        self.assertEqual([p['id'] for p in series['points']],[4,5,6])

    def test_comparison_empty_and_missing_samples_stay_separate(self):
        self.client.post('/api/targets',json={'name':'Second','ip':'127.0.0.2'})
        self.insert()
        series=self.request('targets=1,2&range=1h').json['series']
        self.assertEqual([s['target_id'] for s in series],[1,2])
        self.assertEqual(series[1]['points'],[])
        self.assertIsNone(series[1]['summary']['icmp']['percentage'])
        self.assertIsNone(series[1]['summary']['average_latency_ms'])

    def test_index_query_plan_and_no_database_mutation(self):
        self.insert()
        with app.get_db() as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM results')]
            plan=db.execute(f'EXPLAIN QUERY PLAN SELECT * FROM results WHERE target_id=? AND ({analytics.TIME_SQL})>=julianday(?) AND ({analytics.TIME_SQL})<=julianday(?) ORDER BY ({analytics.TIME_SQL}),id',(1,*analytics.time_window('1h',NOW))).fetchall()
            self.assertIn('idx_results_observed',' '.join(str(tuple(row)) for row in plan))
        self.request()
        with app.get_db() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM results')],before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)

    def test_invalid_values_and_query_budget(self):
        self.insert(latency_ms=-1)
        self.insert(checked_at='2026-10-22 11:30:00')
        self.insert(checked_at='not-a-time')
        summary=self.request().json['series'][0]['summary']
        self.assertEqual(summary['exact_rtt_count'],0)
        self.assertEqual(summary['invalid_timestamp_count'],1)
        with patch.object(analytics.time,'monotonic',side_effect=[0,20]):
            response=self.request()
        self.assertEqual(response.status_code,503)

    def test_actual_response_cap_and_latest_samples(self):
        with app.get_db() as db:
            db.executemany("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(1,?,0,0)",
                           [('2026-10-22T11:30:00Z',)]*2005)
        series=self.request().json['series'][0]
        self.assertEqual((series['returned_points'],series['summary']['record_count']),(2000,2005))
        self.assertEqual(series['points'][0]['id'],6)
        self.assertTrue(series['truncated'])

    def test_legacy_date_aliases_survive_index_creation_and_future_writes(self):
        with app.get_db() as db:
            db.execute('DROP INDEX idx_results_observed')
            db.execute("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok) VALUES(1,'now',1,1)")
        app.init_db()
        self.insert(checked_at='now')
        self.assertEqual(self.request().json['series'][0]['summary']['record_count'],0)
        self.assertEqual(len(self.client.get('/api/targets/1/history').json),2)
