import unittest
from datetime import datetime, timedelta, timezone

import app as netscope
import test_app


def record(received=5, tcp_ok=1, **overrides):
    data = dict(checked_at='2026-10-14T10:00:00+00:00', latency_source='icmp_rtt',
                ping_ok=int(received > 0), icmp_sent=5, icmp_received=received,
                icmp_unanswered=5-received, packet_loss_pct=(5-received)*20,
                latency_ms=None, latency_upper_bound_ms=None, ping_error=None,
                tcp_ok=tcp_ok, tcp_error=None if tcp_ok else 'Connection refused (port is not accepting connections)')
    data.update(overrides)
    return data


class AvailabilityCalculationTests(unittest.TestCase):
    def test_zero_partial_and_full_availability(self):
        for rows, expected in [([record(0,0)]*3, 0),
                               ([record(5,1),record(0,0),record(3,1),record(0,0)],50),
                               ([record(5,1)]*3,100)]:
            with self.subTest(expected=expected):
                summary=netscope.availability_summary(rows)
                for protocol in ('icmp','tcp'):
                    self.assertEqual(summary[protocol]['percentage'],expected)
                    self.assertEqual(summary[protocol]['valid_checks'],len(rows))
                    self.assertEqual(summary[protocol]['excluded_checks'],0)
                    self.assertIsNotNone(summary[protocol]['window_start'])
                    self.assertIsNotNone(summary[protocol]['window_end'])

    def test_packet_loss_is_not_check_availability(self):
        summary=netscope.availability_summary([record(1),record(3)])
        self.assertEqual(summary['icmp']['percentage'],100)
        self.assertEqual(summary['icmp']['successful_checks'],2)
        self.assertEqual(summary['icmp']['valid_checks'],2)

    def test_icmp_and_tcp_have_independent_denominators(self):
        rows=[record(5,0), record(0,1),
              record(5,1,icmp_sent=None,icmp_received=None,icmp_unanswered=None,packet_loss_pct=None),
              record(5,0,tcp_error='Unclassifiable old message')]
        summary=netscope.availability_summary(rows)
        self.assertAlmostEqual(summary['icmp']['percentage'],200/3)
        self.assertAlmostEqual(summary['tcp']['percentage'],200/3)
        self.assertEqual(netscope.check_states(rows[2]),{'icmp':'unknown','tcp':'success'})
        self.assertEqual(netscope.check_states(rows[3]),{'icmp':'success','tcp':'unknown'})
        # Opposite outcomes remain separate, rather than reporting device uptime.
        opposite=netscope.availability_summary([record(5,0)]*2)
        self.assertEqual(opposite['icmp']['percentage'],100)
        self.assertEqual(opposite['tcp']['percentage'],0)

    def test_empty_missing_and_legacy_are_not_failures(self):
        for rows in ([],[{}],[record(latency_source=None)],
                     [record(latency_source='legacy_process_duration')],
                     [record(latency_source='unrecognized')],
                     [record(checked_at=None)],[record(checked_at='bad')],
                     [record(checked_at='2026-10-14')]):
            with self.subTest(rows=rows):
                summary=netscope.availability_summary(rows)
                for protocol in ('icmp','tcp'):
                    self.assertIsNone(summary[protocol]['percentage'])
                    self.assertEqual(summary[protocol]['valid_checks'],0)
                    self.assertEqual(summary[protocol]['excluded_checks'],len(rows))
                    self.assertIsNone(summary[protocol]['window_start'])

    def test_incomplete_and_inconsistent_icmp_is_excluded_only_from_icmp(self):
        variations=[dict(icmp_sent=None),dict(icmp_sent=4),dict(icmp_received=6),
                    dict(icmp_unanswered=3),dict(packet_loss_pct=None),
                    dict(packet_loss_pct=float('nan')),dict(packet_loss_pct=20),
                    dict(ping_ok=0),dict(icmp_received='5'),
                    dict(ping_error='Ping command unavailable'),
                    dict(ping_error='Incomplete or malformed ping statistics')]
        for variation in variations:
            with self.subTest(variation=variation):
                states=netscope.check_states(record(**variation))
                self.assertEqual(states,{'icmp':'unknown','tcp':'success'})

    def test_rtt_unavailable_does_not_exclude_completed_icmp(self):
        for data in (record(latency_upper_bound_ms=1),record(ping_error='Echo responses received; complete RTT statistics unavailable')):
            self.assertEqual(netscope.check_states(data)['icmp'],'success')

    def test_unclassifiable_tcp_and_contradictory_success_excluded(self):
        for overrides in (dict(tcp_ok=0,tcp_error=None),dict(tcp_ok=1,tcp_error='Connection timed out'),
                          dict(tcp_ok=2),dict(tcp_ok=0,tcp_error='Network error: '),
                          dict(tcp_ok=0,tcp_error='unexpected diagnostic')):
            self.assertEqual(netscope.check_states(record(**overrides)),{'icmp':'success','tcp':'unknown'})

    def test_completed_tcp_failure_categories(self):
        for error in ('Connection refused (port is not accepting connections)',
                      'Connection timed out (service did not respond)',
                      'Network error: [WinError 10051] Network unreachable'):
            self.assertEqual(netscope.check_states(record(0,0,tcp_error=error))['tcp'],'failure')

    def test_each_protocol_uses_its_own_valid_observation_window(self):
        rows=[record(5,0,checked_at='2026-10-14T10:00:00+00:00',tcp_error=None),
              record(0,1,checked_at='2026-10-14T10:05:00+00:00',icmp_sent=None),
              record(5,1,checked_at='2026-10-14T14:10:00+04:00')]
        result=netscope.availability_summary(rows)
        self.assertEqual(result['icmp']['window_start'],'2026-10-14T10:00:00+00:00')
        self.assertEqual(result['tcp']['window_start'],'2026-10-14T10:05:00+00:00')
        for protocol in ('icmp','tcp'):
            self.assertEqual(result[protocol]['window_end'],'2026-10-14T10:10:00+00:00')
            self.assertEqual(result[protocol]['valid_checks'],2)
            self.assertEqual(result[protocol]['excluded_checks'],1)


class AvailabilityDatabaseTests(unittest.TestCase):
    setUp=test_app.ApplicationTests.setUp
    tearDown=test_app.ApplicationTests.tearDown

    def insert(self,data,target_id=1):
        row=dict(target_id=target_id,**data)
        with netscope.get_db() as db:
            db.execute(f"INSERT INTO results ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",tuple(row.values()))

    def test_real_database_rows_drive_summaries_and_timeline_states(self):
        self.insert(record(5,0))
        self.insert(record(0,1,checked_at='2026-10-14T10:01:00+00:00'))
        self.insert(record(latency_source=None,checked_at='2026-10-14T10:02:00+00:00'))
        self.insert(record(5,1,icmp_sent=None,checked_at='2026-10-14T10:03:00+00:00'))
        target=self.client.get('/api/targets').json[0]
        summary=target['availability']
        self.assertEqual(summary['icmp']['percentage'],50)
        self.assertEqual(summary['icmp']['valid_checks'],2)
        self.assertEqual(summary['icmp']['excluded_checks'],2)
        self.assertAlmostEqual(summary['tcp']['percentage'],200/3)
        self.assertEqual(summary['tcp']['valid_checks'],3)
        history=self.client.get('/api/targets/1/history').json
        self.assertEqual([r['icmp_check_status'] for r in history],['success','failure','unknown','unknown'])
        self.assertEqual([r['tcp_check_status'] for r in history],['failure','success','unknown','success'])
        with netscope.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],4)

    def test_window_is_latest_sixty_stored_rows_not_sixty_valid_rows(self):
        start=datetime(2026,10,14,10,tzinfo=timezone.utc)
        for i in range(65):
            self.insert(record(0 if i<5 else 5,0 if i<5 else 1,checked_at=(start+timedelta(minutes=i)).isoformat()))
        self.insert(record(latency_source=None,checked_at=(start+timedelta(minutes=65)).isoformat()))
        result=self.client.get('/api/targets').json[0]['availability']
        self.assertEqual(result['record_count'],60)
        for protocol in ('icmp','tcp'):
            self.assertEqual(result[protocol]['percentage'],100)
            self.assertEqual(result[protocol]['valid_checks'],59)
            self.assertEqual(result[protocol]['excluded_checks'],1)
            self.assertEqual(result[protocol]['window_start'],(start+timedelta(minutes=6)).isoformat())
        self.assertEqual(len(self.client.get('/api/targets/1/history').json),60)

    def test_devices_do_not_share_statistics(self):
        response=self.client.post('/api/targets',json={'name':'Other local service','ip':'127.0.0.2','port':80})
        other=response.json['id']
        self.insert(record(5,0),1)
        self.insert(record(0,1),other)
        targets=self.client.get('/api/targets').json
        self.assertEqual(targets[0]['availability']['icmp']['percentage'],100)
        self.assertEqual(targets[0]['availability']['tcp']['percentage'],0)
        self.assertEqual(targets[1]['availability']['icmp']['percentage'],0)
        self.assertEqual(targets[1]['availability']['tcp']['percentage'],100)

    def test_no_records_returns_na_and_zero_counts(self):
        result=self.client.get('/api/targets').json[0]['availability']
        self.assertEqual(result['record_count'],0)
        self.assertIsNone(result['icmp']['percentage'])
        self.assertIsNone(result['tcp']['percentage'])
        self.assertEqual(result['icmp']['valid_checks'],0)
        self.assertEqual(self.client.get('/api/targets/1/history').json,[])
