"""Deterministic statistics over disposable measurements; no probes."""
from datetime import datetime,timezone
import json
import unittest
from unittest.mock import patch
import app
import advanced_analytics as analytics
import test_app
import test_historical_analytics

WINDOW='start=2026-10-22T11:00:00Z&end=2026-10-22T12:00:00Z'


class AdvancedAnalyticsTests(unittest.TestCase):
    setUp=test_app.ApplicationTests.setUp
    tearDown=test_app.ApplicationTests.tearDown
    insert=test_historical_analytics.HistoricalAnalyticsTests.insert

    def request(self,query=None,incidents=False):
        return self.client.get('/api/analytics'+('/incidents' if incidents else '')+'?'+(query if query is not None else 'targets=1&'+WINDOW))

    def incident(self,**changes):
        row=dict(target_id=1,original_target_id=1,condition='tcp_unavailable',state='RESOLVED',
                 first_observed='2026-10-22T10:59:00Z',latest_observed='2026-10-22T11:30:00Z',
                 resolved_at='2026-10-22T11:30:00Z',duration_seconds=1860,triggering_result_id=1,latest_result_id=1,
                 triggering_measurements=json.dumps({'trigger':{'observed_at':'2026-10-22T11:00:00Z'}}),thresholds='{}')
        row.update(changes)
        with app.get_db() as db:
            cursor=db.execute('INSERT INTO incidents('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
            return cursor.lastrowid

    def test_percentile_method_and_minimum_counts(self):
        s=analytics.statistics(list(range(1,101)))
        self.assertEqual(s['median'],50.5);self.assertAlmostEqual(s['p95'],95.05);self.assertAlmostEqual(s['p99'],99.01)
        self.assertEqual(analytics.statistics([7])['median'],7)
        self.assertIsNone(analytics.statistics([7])['p95'])
        self.assertAlmostEqual(analytics.statistics(list(range(20)))['p95'],18.05)
        self.assertIsNone(analytics.statistics([])['mean'])

    def test_exact_statistics_exclude_bounds_unknown_and_negative(self):
        for value in range(1,101):self.insert(latency_ms=value)
        self.insert(latency_ms=None,latency_upper_bound_ms=1)
        self.insert(latency_source=None,latency_ms=999)
        self.insert(latency_ms=-2)
        s=self.request().json['series'][0]['current']
        self.assertEqual(s['rtt_ms']['count'],100)
        self.assertAlmostEqual(s['rtt_ms']['p99'],99.01)
        self.assertEqual(s['summary']['missing_rtt_count'],3)
        self.assertEqual(sum(row['count'] for row in s['rtt_distribution']),100)
        self.assertEqual(s['summary']['unknown_measurements'],1)

    def test_loss_distribution_and_independent_sampled_availability(self):
        for received in range(6):
            self.insert(ping_ok=int(received>0),icmp_received=received,icmp_unanswered=5-received,
                        packet_loss_pct=(5-received)*20,latency_ms=2 if received else None)
        self.insert(latency_source=None,icmp_sent=None,icmp_received=None,icmp_unanswered=None,packet_loss_pct=None)
        s=self.request().json['series'][0]['current']
        self.assertEqual(s['packet_loss_pct']['mean'],50)
        self.assertEqual([b['count'] for b in s['loss_distribution']],[1,1,1,2,1])
        self.assertAlmostEqual(s['summary']['icmp']['percentage'],500/6)
        self.assertEqual(s['summary']['tcp']['percentage'],100)
        self.assertEqual(s['summary']['icmp']['excluded_checks'],1)

    def test_equal_preceding_window_trends_with_unequal_counts_and_zero_baseline(self):
        for _ in range(5):self.insert(checked_at='2026-10-22T10:30:00Z',latency_ms=10)
        for _ in range(8):self.insert(latency_ms=20)
        series=self.request().json['series'][0]
        trend=series['trends']['mean_rtt_ms']
        self.assertEqual((trend['delta'],trend['relative_change_pct'],trend['current_count'],trend['previous_count']),(10,100,8,5))
        self.assertEqual(series['trends']['mean_loss_pct']['delta'],0)
        self.assertIsNone(series['trends']['mean_loss_pct']['relative_change_pct'])
        with app.get_db() as db:db.execute("UPDATE results SET latency_ms=0 WHERE checked_at='2026-10-22T10:30:00Z'")
        self.assertIsNone(self.request().json['series'][0]['trends']['mean_rtt_ms']['relative_change_pct'])

    def test_half_open_windows_offsets_and_no_double_counted_boundary(self):
        for stamp in ('2026-10-22T10:00:00Z','2026-10-22T14:00:00+03:00','2026-10-22T12:00:00Z'):
            self.insert(checked_at=stamp)
        series=self.request().json['series'][0]
        self.assertEqual(series['current']['summary']['record_count'],1)
        self.assertEqual(series['previous']['summary']['record_count'],1)
        self.assertFalse(series['trends']['mean_rtt_ms']['sufficient'])
        self.assertIsNone(series['trends']['mean_rtt_ms']['delta'])

    def test_empty_sparse_invalid_times_and_missing_counts(self):
        empty=self.request().json['series'][0]['current']
        self.assertEqual(empty['rtt_ms']['count'],0);self.assertIsNone(empty['summary']['icmp']['percentage'])
        self.insert(checked_at='2026-10-22 11:30:00')
        self.insert(checked_at='invalid')
        s=self.request().json['series'][0]['current']['summary']
        self.assertEqual((s['record_count'],s['invalid_timestamp_count'],s['missing_rtt_count']),(1,1,1))

    def test_incident_opening_confirmation_resolution_and_current_unresolved(self):
        self.incident()
        self.incident(first_observed='2026-10-22T11:10:00Z',duration_seconds=120,resolved_at='2026-10-22T11:12:00Z',condition='high_latency')
        self.incident(state='ACKNOWLEDGED',resolved_at=None,duration_seconds=None,condition='icmp_unavailable',first_observed='2026-10-22T09:00:00Z',triggering_measurements=json.dumps({'trigger':{'observed_at':'2026-10-22T09:01:00Z'}}))
        stats=self.request().json['incidents']
        self.assertEqual((stats['opened'],stats['resolved'],stats['unresolved']),(2,2,1))
        self.assertEqual(stats['resolved_sampled_span_seconds']['median'],990)
        self.assertEqual(stats['mean_sampled_time_to_recovery_seconds'],990)
        self.assertEqual(stats['resolved_sampled_span_seconds']['maximum'],1860)
        self.assertEqual(stats['by_condition']['icmp_unavailable']['unresolved'],1)
        self.assertGreater(stats['unresolved_age_seconds']['maximum'],0)

    def test_deleted_device_incidents_remain_global_and_do_not_attach_to_reused_ids(self):
        self.incident()
        self.client.delete('/api/targets/1')
        stats=self.request(WINDOW,incidents=True).json['incidents']
        self.assertEqual(stats['resolved'],1)
        self.assertIsNone(stats['by_device'][0]['target_id'])
        self.assertEqual(stats['by_device'][0]['original_target_id'],1)
        self.assertEqual(self.request().status_code,404)
        self.client.post('/api/targets',json={'name':'New','ip':'127.0.0.1'})
        self.assertEqual(self.request().json['incidents']['resolved'],0)

    def test_incident_end_boundaries_and_invalid_evidence(self):
        self.incident(resolved_at='2026-10-22T12:00:00Z',triggering_measurements='{}')
        self.incident(resolved_at='invalid',triggering_measurements='[]')
        self.incident(state='OPEN',resolved_at=None,duration_seconds=None,condition='high_latency',first_observed='invalid')
        stats=self.request().json['incidents']
        self.assertEqual(stats['opened'],1);self.assertEqual(stats['resolved'],0)
        self.assertEqual(stats['invalid_open_timestamp_count'],2)
        self.assertEqual(stats['invalid_resolved_timestamp_count'],1)
        self.assertEqual(stats['invalid_age_count'],1)

    def test_nonfinite_legacy_spans_and_future_unresolved_ages_are_excluded(self):
        self.incident(duration_seconds=float('inf'))
        self.incident(state='OPEN',resolved_at=None,duration_seconds=None,condition='high_latency',first_observed='2999-01-01T00:00:00Z')
        stats=self.request().json['incidents']
        self.assertEqual(stats['resolved'],1)
        self.assertEqual(stats['invalid_duration_count'],1)
        self.assertIsNone(stats['mean_sampled_time_to_recovery_seconds'])
        self.assertEqual(stats['unresolved'],1)
        self.assertEqual(stats['invalid_age_count'],1)

    def test_api_validation_security_and_presets(self):
        for value in ('1h','6h','24h','7d','30d'):
            self.assertEqual(self.request('targets=1&range='+value).status_code,200)
        for query in ('range=1h','targets=1,1','targets=0','targets=1,2,3,4,5','targets=1&range=bad',
                      'targets=1&range=1h&range=6h','targets=1&extra=1','targets=1&start=bad',
                      'targets=1&'+WINDOW+'&range=1h','targets=1&start=2026-10-22T11:00:00&end=2026-10-22T12:00:00',
                      'targets=1&start=2026-01-01T00:00:00Z&end=2026-03-01T00:00:00Z','targets=1%20OR%201=1'):
            self.assertEqual(self.request(query).status_code,400,query)
        for route in ('/api/analytics?targets=1','/api/analytics/incidents'):
            self.assertEqual(self.client.get(route,headers={'Origin':'https://evil.test'}).status_code,403)
            self.assertEqual(self.client.get(route,environ_base={'REMOTE_ADDR':'10.0.0.1'}).status_code,403)

    def test_row_incident_time_limits_and_database_failure_are_safe(self):
        for _ in range(4):self.insert()
        with patch.object(analytics,'MAX_ROWS',3):self.assertEqual(self.request().status_code,503)
        self.incident();self.incident(condition='high_latency')
        with patch.object(analytics,'MAX_INCIDENTS',1):self.assertEqual(self.request().status_code,503)
        with patch.object(analytics.Budget,'check',side_effect=analytics.AnalyticsLimit('budget')):
            self.assertEqual(self.request().status_code,503)
        with patch.object(app,'get_db',side_effect=app.sqlite3.OperationalError('private')),patch.object(app.app.logger,'exception'):
            response=self.request();self.assertEqual(response.status_code,503);self.assertNotIn('private',response.get_data(as_text=True))

    def test_large_history_uses_existing_index_without_payload_or_data_changes(self):
        with app.get_db() as db:
            db.executemany("INSERT INTO results(target_id,checked_at,ping_ok,tcp_ok,latency_source,latency_ms,icmp_sent,icmp_received,icmp_unanswered,packet_loss_pct) VALUES(1,'2026-10-22T11:30:00Z',1,1,'icmp_rtt',20,5,5,0,0)",[()]*6000)
            plan=db.execute(f'EXPLAIN QUERY PLAN SELECT * FROM results WHERE target_id=? AND ({app.TIME_SQL})>=julianday(?) AND ({app.TIME_SQL})<julianday(?) ORDER BY ({app.TIME_SQL}),id',(1,'2026-10-22T11:00:00Z','2026-10-22T12:00:00Z')).fetchall()
            self.assertIn('idx_results_observed',' '.join(str(tuple(row)) for row in plan))
        response=self.request();self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['series'][0]['current']['rtt_ms']['p99'],20)
        self.assertNotIn('points',response.json['series'][0]['current'])
        self.assertLess(len(response.data),15000)
        with app.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],6000)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
