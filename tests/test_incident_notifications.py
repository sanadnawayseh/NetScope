import sqlite3
import unittest
from unittest.mock import patch

import app
import incident_notifications as notifications
import test_incidents


class NotificationTests(unittest.TestCase):
    setUp=test_incidents.IncidentTests.setUp
    tearDown=test_incidents.IncidentTests.tearDown
    row=test_incidents.IncidentTests.row
    save=test_incidents.IncidentTests.save
    records=test_incidents.IncidentTests.records
    evaluate=test_incidents.IncidentTests.evaluate

    def cycle(self,base=0):
        for second in (base,base+1,base+2):self.save(second,tcp=False)
        self.save(base+3);self.save(base+4)

    def feed(self,query=''):
        response=self.client.get('/api/notifications'+('?' + query if query else ''))
        self.assertEqual(response.status_code,200)
        return response.json

    def version_three(self):
        with app.get_db() as db:
            db.execute('DROP TRIGGER notify_incident_opened')
            db.execute('DROP TRIGGER notify_incident_resolved')
            db.execute('DROP TABLE notification_events')
            db.execute('DROP TABLE notification_meta')
            db.execute('DROP TABLE discovery_observations')
            db.execute('PRAGMA user_version=3')

    def test_outage_and_recovery_emit_exactly_two_committed_events(self):
        self.cycle()
        feed=self.feed()
        self.assertEqual([e['event_type'] for e in feed['events']],['incident_opened','incident_resolved'])
        self.assertEqual({e['incident_id'] for e in feed['events']},{self.records()[0]['id']})
        self.assertLess(feed['events'][0]['event_at'],feed['events'][1]['event_at'])
        self.assertEqual(feed['unread_count'],2)
        self.evaluate();app.init_db()
        self.assertEqual(self.feed()['events'],feed['events'])
        self.assertEqual(self.feed()['stream_id'],feed['stream_id'])

    def test_unknown_and_acknowledgement_do_not_emit_alerts(self):
        for second in (0,1,2):self.save(second,tcp_ok=0,tcp_error='TCP check unavailable (unexpected execution error)')
        self.assertEqual(self.feed()['events'],[])
        for second in (3,4,5):self.save(second,tcp=False)
        incident_id=self.records()[0]['id']
        self.client.post(f'/api/incidents/{incident_id}/acknowledge',json={'note':'not an alert'})
        self.assertEqual(len(self.feed()['events']),1)
        self.save(6);self.save(7)
        self.assertEqual(len(self.feed()['events']),2)

    def test_event_failure_rolls_back_incident_and_cursor_but_not_measurement(self):
        self.save(0,tcp=False);self.save(1,tcp=False)
        with app.get_db() as db:
            db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON notification_events BEGIN SELECT RAISE(ABORT,'private'); END")
        with patch.object(app.app.logger,'exception') as log:self.save(2,tcp=False)
        self.assertTrue(log.called)
        self.assertEqual(self.records(),[])
        self.assertEqual(self.feed()['events'],[])
        with app.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT last_result_id FROM incident_cursors').fetchone()[0],2)
            db.execute('DROP TRIGGER reject_event')
        self.evaluate();self.evaluate()
        self.assertEqual(len(self.feed()['events']),1)
        self.save(3);self.save(4)
        self.assertEqual(len(self.feed()['events']),2)

    def test_failed_measurement_cannot_generate_event(self):
        with self.assertRaises(sqlite3.IntegrityError):self.save(0,target_id=999,tcp=False)
        self.assertEqual(self.feed()['events'],[])

    def test_unique_constraint_and_target_deletion_preserve_event_history(self):
        self.cycle()
        with self.assertRaises(sqlite3.IntegrityError):
            with app.get_db() as db:
                db.execute('INSERT INTO notification_events(incident_id,event_type,event_at,created_at) SELECT incident_id,event_type,event_at,created_at FROM notification_events')
        self.client.delete('/api/targets/1')
        feed=self.feed()
        self.assertEqual(len(feed['events']),2)
        self.assertTrue(all(e['target_id'] is None and e['original_target_id']==1 for e in feed['events']))

    def test_read_state_is_idempotent_durable_and_does_not_acknowledge_incident(self):
        for second in (0,1,2):self.save(second,tcp=False)
        first=self.client.post('/api/notifications/1/read',json={})
        again=self.client.post('/api/notifications/1/read',json={})
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json,again.json)
        self.assertEqual(self.records()[0]['state'],'OPEN')
        app.init_db()
        self.assertEqual(self.feed()['events'][0]['read_at'],first.json['read_at'])
        self.assertEqual(self.feed()['unread_count'],0)

    def test_read_all_snapshot_preserves_newer_events(self):
        self.cycle()
        snapshot=self.feed()['latest_id']
        self.cycle(5)
        response=self.client.post('/api/notifications/read',json={'up_to_id':snapshot})
        self.assertEqual(response.json['unread_count'],2)
        before=self.feed()['events'][:2]
        self.client.post('/api/notifications/read',json={'up_to_id':snapshot})
        self.assertEqual(self.feed()['events'][:2],before)

    def test_bounded_pagination_order_and_forward_cursors(self):
        for base in (0,5,10):self.cycle(base)
        first=self.feed('after_id=0&limit=2')
        self.assertEqual([e['id'] for e in first['events']],[1,2])
        self.assertTrue(first['has_more'])
        second=self.feed('after_id=2&limit=2')
        self.assertEqual([e['id'] for e in second['events']],[3,4])
        recent=self.feed('limit=2')
        self.assertEqual([e['id'] for e in recent['events']],[5,6])
        self.assertEqual(recent['next_before_id'],5)
        older=self.feed('before_id=5&limit=2')
        self.assertEqual([e['id'] for e in older['events']],[3,4])

    def test_api_validation_and_request_boundary(self):
        for query in ('limit=201','limit=0','after_id=-1','after_id=1&before_id=2','after_id=1&after_id=2','secret=yes','after_id=1%20OR%201=1'):
            with self.subTest(query=query):self.assertEqual(self.client.get('/api/notifications?'+query).status_code,400)
        self.assertEqual(self.client.get('/api/notifications',headers={'Origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/notifications/1/read',json={},environ_overrides={'REMOTE_ADDR':'10.0.0.1'}).status_code,403)
        self.assertEqual(self.client.post('/api/notifications/1/read',data='text').status_code,415)
        self.assertEqual(self.client.post('/api/notifications/1/read',json={'unexpected':1}).status_code,400)
        self.assertEqual(self.client.post('/api/notifications/999/read',json={}).status_code,404)
        for body in ({},{'up_to_id':True},{'up_to_id':-1},{'up_to_id':'2'},[]):
            self.assertEqual(self.client.post('/api/notifications/read',json=body).status_code,400)

    def test_v3_migration_preserves_incidents_without_backfilled_notifications(self):
        for second in (0,1,2):self.save(second,tcp=False)
        old=self.records()
        self.version_three();app.init_db();app.init_db()
        self.assertEqual(self.records(),old)
        self.assertEqual(self.feed()['events'],[])
        self.save(3);self.save(4)
        self.assertEqual([e['event_type'] for e in self.feed()['events']],['incident_resolved'])
        with app.get_db() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)

    def test_notification_migration_failure_rolls_back_ddl_and_version(self):
        self.version_three()
        with patch.object(notifications,'DDL',notifications.DDL+['CREATE TRIGGER invalid WRONG']):
            with self.assertRaises(sqlite3.DatabaseError):app.init_db()
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='notification_events'").fetchone())

    def test_failed_read_update_is_rolled_back_and_api_error_is_safe(self):
        self.cycle()
        with app.get_db() as db:
            db.execute("CREATE TRIGGER reject_read BEFORE UPDATE OF read_at ON notification_events BEGIN SELECT RAISE(ABORT,'private'); END")
        with patch.object(app.app.logger,'exception'):
            response=self.client.post('/api/notifications/read',json={'up_to_id':2})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private',response.get_data(as_text=True))
        self.assertEqual(self.feed()['unread_count'],2)
