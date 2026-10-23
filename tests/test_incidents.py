"""Deterministic incident tests: disposable databases, no network probes."""
from datetime import datetime, timedelta, timezone
import json
import io
import logging
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import app
import incident_management as incidents
import test_app
from application_logging import JSONFormatter


class IncidentTests(unittest.TestCase):
    setUp = test_app.ApplicationTests.setUp
    tearDown = test_app.ApplicationTests.tearDown

    def row(self, second, ping=True, tcp=True, loss=0, latency=20, **changes):
        if not ping:
            loss,latency = 100,None
        received=5-round(loss/20)
        row=dict(target_id=1,checked_at=(datetime(2026,10,22,12,tzinfo=timezone.utc)+timedelta(seconds=second)).isoformat(),
                 ping_ok=int(ping),tcp_ok=int(tcp),latency_source='icmp_rtt',latency_ms=latency,
                 icmp_sent=5,icmp_received=received,icmp_unanswered=5-received,packet_loss_pct=loss,
                 tcp_error=None if tcp else 'Connection refused (port is not accepting connections)')
        row.update(changes)
        return row

    def save(self, second, **changes):
        app.persist_measurement(self.row(second,**changes))

    def records(self, condition=None):
        with app.get_db() as db:
            rows=db.execute('SELECT * FROM incidents'+(' WHERE condition=?' if condition else '')+' ORDER BY id',
                            (condition,) if condition else ()).fetchall()
            return [dict(r) for r in rows]

    def evaluate(self):
        incidents.evaluate_target(app.get_db,1,app.check_states,app.observation_time,app.app.logger)

    def config(self, **values):
        with app.get_db() as db:
            incidents.configure(db,values)

    def test_consecutive_outage_and_independent_tcp_recovery(self):
        for second in (0,1):
            self.save(second,ping=False,tcp=False)
        self.assertEqual(self.records(),[])
        self.save(2,ping=False,tcp=False)
        self.assertEqual(len(self.records('icmp_unavailable')),1)
        self.assertEqual(len(self.records('tcp_unavailable')),1)
        self.save(3,tcp=False)
        self.assertEqual(self.records('icmp_unavailable')[0]['state'],'OPEN')
        self.save(4,tcp=False)
        icmp=self.records('icmp_unavailable')[0]
        self.assertEqual((icmp['state'],icmp['duration_seconds']),('RESOLVED',4))
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'OPEN')
        self.save(5);self.save(6)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'RESOLVED')
        self.assertIsNotNone(icmp['resolving_result_id'])

    def test_flapping_resets_open_and_recovery_streaks(self):
        self.save(0,tcp=False);self.save(1,tcp=False);self.save(2)
        self.save(3,tcp=False);self.save(4,tcp=False)
        self.assertEqual(self.records('tcp_unavailable'),[])
        self.save(5,tcp=False);self.save(6);self.save(7,tcp=False);self.save(8)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'OPEN')
        self.save(9)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'RESOLVED')

    def test_loss_and_latency_strict_threshold_crossings(self):
        self.save(0,loss=40,latency=200)
        for second in (1,2,3):self.save(second,loss=60,latency=201)
        self.assertEqual({r['condition'] for r in self.records()},{'packet_loss','high_latency'})
        for second in (4,5):self.save(second,loss=40,latency=200)
        self.assertTrue(all(r['state']=='RESOLVED' for r in self.records()))
        thresholds=json.loads(self.records('high_latency')[0]['thresholds'])
        self.assertEqual((thresholds['threshold'],thresholds['open_checks'],thresholds['recovery_checks']),(200,3,2))

    def test_unknown_protocol_resets_pending_streak_without_transition(self):
        self.save(0,tcp=False);self.save(1,tcp=False)
        self.save(2,tcp_ok=0,tcp_error='TCP check unavailable (unexpected execution error)')
        self.save(3,tcp=False);self.save(4,tcp=False)
        self.assertEqual(self.records('tcp_unavailable'),[])
        self.save(5,tcp=False);self.save(6)
        self.save(7,tcp_ok=0,tcp_error='TCP check unavailable (local socket error)')
        self.save(8)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'OPEN')
        self.save(9)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'RESOLVED')

    def test_missing_icmp_statistics_never_count_as_outage_or_recovery(self):
        for second in (0,1,2):self.save(second,ping=False)
        for second in (3,4):
            self.save(second,ping_ok=0,icmp_sent=None,icmp_received=None,icmp_unanswered=None,
                      packet_loss_pct=None,latency_ms=None,ping_error='Ping command failed unexpectedly; check unavailable')
        self.assertEqual(self.records('icmp_unavailable')[0]['state'],'OPEN')

    def test_bounded_rtt_cannot_open_or_resolve_latency_incident(self):
        for second in (0,1,2):self.save(second,latency=201)
        for second in (3,4):self.save(second,latency=None,latency_upper_bound_ms=1)
        self.assertEqual(self.records('high_latency')[0]['state'],'OPEN')
        self.save(5);self.save(6)
        self.assertEqual(self.records('high_latency')[0]['state'],'RESOLVED')

    def test_long_gap_requires_new_consecutive_observations(self):
        self.save(0,tcp=False);self.save(1,tcp=False);self.save(1000,tcp=False)
        self.assertEqual(self.records('tcp_unavailable'),[])
        self.save(1001,tcp=False);self.save(1002,tcp=False)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'OPEN')
        app.init_db()
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'OPEN')

    def test_duplicate_timestamps_and_reprocessing_do_not_count_twice(self):
        for _ in range(4):self.save(0,tcp=False)
        self.assertEqual(self.records(),[])
        self.save(1,tcp=False);self.save(2,tcp=False)
        before=self.records()
        self.evaluate();self.evaluate()
        self.assertEqual(self.records(),before)

    def test_out_of_order_pending_rows_sorted_and_late_rows_ignored(self):
        with patch.object(app,'process_pending'):
            for second in (2,0,1):self.save(second,tcp=False)
        self.evaluate()
        incident=self.records('tcp_unavailable')[0]
        self.assertTrue(incident['first_observed'].startswith('2026-10-22T12:00:00'))
        self.assertTrue(incident['latest_observed'].startswith('2026-10-22T12:00:02'))
        self.save(-2);self.save(-1)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'OPEN')

    def test_restart_preserves_pending_streak_and_active_incident(self):
        self.save(0,tcp=False);self.save(1,tcp=False)
        app.init_db();self.save(2,tcp=False)
        before=self.records()
        app.init_db()
        self.assertEqual(self.records(),before)
        self.save(3);app.init_db();self.save(4)
        self.assertEqual(self.records('tcp_unavailable')[0]['state'],'RESOLVED')

    def test_configuration_change_resets_streak_and_preserves_trigger_snapshot(self):
        for second in (0,1,2):self.save(second,latency=250)
        original=self.records('high_latency')[0]['thresholds']
        self.save(3,latency=100)
        self.config(NETSCOPE_INCIDENT_LATENCY_MS='300')
        self.save(4,latency=250)
        self.assertEqual(self.records('high_latency')[0]['state'],'OPEN')
        self.save(5,latency=250)
        record=self.records('high_latency')[0]
        self.assertEqual(record['state'],'RESOLVED')
        self.assertEqual(record['thresholds'],original)
        self.assertEqual(json.loads(record['recovery'])['thresholds']['threshold'],300)

    def test_configuration_validation_is_atomic_and_persistent(self):
        for value in ('nan','inf','-1','101'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.config(NETSCOPE_INCIDENT_LOSS_PCT=value)
        self.config(NETSCOPE_INCIDENT_OPEN_CHECKS='4')
        with app.get_db() as db:
            incidents.configure(db,{})
            self.assertTrue(all(r[0]==4 for r in db.execute('SELECT open_checks FROM alert_config')))

    def test_database_constraint_prevents_duplicate_active_incidents(self):
        for second in (0,1,2):self.save(second,tcp=False)
        with self.assertRaises(sqlite3.IntegrityError):
            with app.get_db() as db:
                db.execute('''INSERT INTO incidents(target_id,original_target_id,condition,state,first_observed,
                    latest_observed,triggering_result_id,latest_result_id,triggering_measurements,thresholds)
                    SELECT target_id,original_target_id,condition,state,first_observed,latest_observed,
                    triggering_result_id,latest_result_id,triggering_measurements,thresholds FROM incidents''')
        self.assertEqual(len(self.records()),1)

    def test_concurrent_evaluators_and_distinct_targets(self):
        self.client.post('/api/targets',json={'name':'Other','ip':'127.0.0.2'})
        with patch.object(app,'process_pending'):
            for second in (0,1,2):
                self.save(second,tcp=False)
                self.save(second,tcp=False,target_id=2)
        errors=[]
        def evaluate(target):
            try:incidents.evaluate_target(app.get_db,target,app.check_states,app.observation_time,app.app.logger)
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=evaluate,args=(target,)) for target in (1,1,2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors,[])
        self.assertEqual(len(self.records()),2)

    def test_failed_write_does_not_evaluate_and_evaluation_failure_keeps_measurement(self):
        with patch.object(app,'process_pending') as evaluate:
            with self.assertRaises(sqlite3.IntegrityError):self.save(0,target_id=999)
            evaluate.assert_not_called()
        with patch.object(incidents,'evaluate_target',side_effect=sqlite3.OperationalError('secret')),patch.object(app.app.logger,'exception') as log:
            self.save(1,tcp=False)
            self.assertEqual(log.call_args.kwargs['extra']['event'],'incident_evaluation_failure')
        with app.get_db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],1)
        app.init_db();self.save(2,tcp=False);self.save(3,tcp=False)
        self.assertEqual(len(self.records()),1)

    def test_evaluation_transaction_rollback_and_no_phantom_logs(self):
        with patch.object(app,'process_pending'):
            for second in (0,1,2):self.save(second,tcp=False)
        with app.get_db() as db:
            db.execute("CREATE TRIGGER stop_incident BEFORE INSERT ON incidents BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with patch.object(app.app.logger,'info') as log:
            with self.assertRaises(sqlite3.IntegrityError):self.evaluate()
            log.assert_not_called()
        with app.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM incident_streaks').fetchone()[0],0)
            db.execute('DROP TRIGGER stop_incident')
        self.evaluate()
        self.assertEqual(len(self.records()),1)

    def test_delete_preserves_incidents_without_binding_reused_target_id(self):
        for second in (0,1,2):self.save(second,tcp=False)
        self.client.delete('/api/targets/1')
        record=self.records()[0]
        self.assertIsNone(record['target_id'])
        self.assertEqual((record['original_target_id'],record['state']),(1,'OPEN'))
        target=self.client.post('/api/targets',json={'name':'Replacement','ip':'127.0.0.1'}).json['id']
        self.save(3,target_id=target);self.save(4,target_id=target)
        self.assertEqual(self.records()[0]['state'],'OPEN')

    def test_structured_logs_only_after_committed_transitions(self):
        with patch.object(app.app.logger,'info') as log:
            for second in (0,1,2):self.save(second,tcp=False)
            self.save(3);self.save(4)
        extras=[call.kwargs['extra'] for call in log.call_args_list]
        self.assertEqual([e['event'] for e in extras],['incident_opened','incident_resolved'])
        self.assertTrue(all(set(e)=={'event','incident_id','condition'} for e in extras))

    def test_api_filters_pagination_safety_and_security(self):
        for second in (0,1,2):self.save(second,ping=False,tcp=False)
        self.save(3);self.save(4)
        url='/api/incidents?target_id=1&condition=tcp_unavailable&state=RESOLVED&range=30d'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json['incidents']),1)
        record=response.json['incidents'][0]
        self.assertEqual(self.client.get('/api/incidents/'+str(record['id'])).json,record)
        self.assertNotIn('127.0.0.1',response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        page=self.client.get('/api/incidents?limit=1').json
        self.assertIsNotNone(page['next_before_id'])
        next_page=self.client.get('/api/incidents?limit=1&before_id='+str(page['next_before_id'])).json
        self.assertNotEqual(page['incidents'][0]['id'],next_page['incidents'][0]['id'])
        self.assertEqual(self.client.get('/api/incidents/999').status_code,404)
        self.assertEqual(self.client.post('/api/incidents').status_code,405)
        self.assertEqual(self.client.get('/api/incidents',headers={'Origin':'https://foreign.example'}).status_code,403)

    def test_api_invalid_filters_and_safe_database_errors(self):
        for query in ('limit=201','target_id=0','condition=oops','state=INVALID',
                      'range=year','range=1h&range=6h','start=bad','start=2026-01-01&end=2026-02-01',
                      'target_id=1%20OR%201=1','start=2026-01-01T00:00:00Z&end=2026-03-01T00:00:00Z'):
            with self.subTest(query=query):self.assertEqual(self.client.get('/api/incidents?'+query).status_code,400)
        with patch.object(app,'get_db',side_effect=sqlite3.OperationalError('secret')),patch.object(app.app.logger,'exception'):
            response=self.client.get('/api/incidents')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('secret',response.get_data(as_text=True))

    def test_version_one_upgrade_preserves_history_without_retroactive_alerts(self):
        with patch.object(app,'process_pending'):
            for second in (0,1,2):self.save(second,tcp=False)
        with app.get_db() as db:
            before=[tuple(row) for row in db.execute('SELECT * FROM results')]
            for table in ('discovery_observations','notification_events','notification_meta','incident_streaks','incident_cursors','incidents','alert_config'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=1')
        app.init_db()
        self.assertEqual(self.records(),[])
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM results')],before)
        for second in (3,4,5):self.save(second,tcp=False)
        self.assertEqual(len(self.records()),1)

    def test_incident_migration_failure_rolls_back_version_and_ddl(self):
        self.save(0)
        with app.get_db() as db:
            for table in ('discovery_observations','notification_events','notification_meta','incident_streaks','incident_cursors','incidents','alert_config'):
                db.execute('DROP TABLE '+table)
            db.execute('CREATE TABLE incidents (unexpected TEXT)')
            db.execute('PRAGMA user_version=1')
        with self.assertRaises(sqlite3.DatabaseError):app.init_db()
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],1)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='alert_config'").fetchone())

    def test_damaged_version_two_index_stops_initialization(self):
        with app.get_db() as db:db.execute('DROP INDEX idx_incidents_active')
        with self.assertRaisesRegex(sqlite3.DatabaseError,'incident schema'):app.init_db()

    def test_transition_json_has_only_allowlisted_metadata(self):
        stream=io.StringIO()
        logger=logging.Logger('incident-test',logging.INFO)
        handler=logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        with patch.object(app,'process_pending'):
            for second in (0,1,2):self.save(second,tcp=False)
        incidents.evaluate_target(app.get_db,1,app.check_states,app.observation_time,logger)
        record=json.loads(stream.getvalue())
        self.assertEqual(record['event'],'incident_opened')
        self.assertEqual(record['condition'],'tcp_unavailable')
        self.assertEqual(record['severity'],'INFO')
        self.assertEqual(set(record),{'timestamp','severity','event','condition','incident_id'})
        handler.close()

    def test_integration_coordinator_evaluates_real_persistence(self):
        targets=app.load_targets()
        for second in (0,1,2):
            report=app.coordinator.run_batch(lambda:targets,
                lambda target:app.Measurement(self.row(second,tcp=False)),app.persist_measurement)
            self.assertEqual(report,{'total':1,'completed':1,'failed':0})
        self.assertEqual(len(self.records('tcp_unavailable')),1)

    def test_custom_incident_time_window_and_individual_detail_security(self):
        for second in (0,1,2):self.save(second,tcp=False)
        self.save(3);self.save(4)
        query='/api/incidents?start=2026-10-22T12:00:01Z&end=2026-10-22T12:00:03Z'
        response=self.client.get(query)
        self.assertEqual((response.status_code,len(response.json['incidents'])),(200,1))
        self.assertEqual(self.client.get('/api/incidents/1',environ_overrides={'REMOTE_ADDR':'192.168.1.1'}).status_code,403)
        self.assertEqual(self.client.get('/api/incidents?start=2026-10-22T12:00:05Z&end=2026-10-22T12:00:06Z').json['incidents'],[])
