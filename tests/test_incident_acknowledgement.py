import sqlite3
import threading
import unittest
from unittest.mock import patch

import app
import incident_management as incidents
import test_incidents


class AcknowledgementTests(unittest.TestCase):
    setUp=test_incidents.IncidentTests.setUp
    tearDown=test_incidents.IncidentTests.tearDown
    row=test_incidents.IncidentTests.row
    save=test_incidents.IncidentTests.save
    records=test_incidents.IncidentTests.records

    def opened(self):
        for second in (0,1,2):self.save(second,tcp=False)
        return self.records('tcp_unavailable')[0]['id']

    def ack(self,incident_id,body=None):
        return self.client.post(f'/api/incidents/{incident_id}/acknowledge',json={} if body is None else body)

    def version_two(self):
        incident_id=self.opened()
        with app.get_db() as db:
            old=dict(db.execute('SELECT * FROM incidents WHERE id=?',(incident_id,)).fetchone())
            del old['acknowledged_at'];del old['operator_note']
            db.execute('DROP TABLE notification_events')
            db.execute('DROP TABLE notification_meta')
            db.execute('DROP TABLE discovery_observations')
            db.execute('DROP TABLE incidents')
            db.execute(incidents.DDL[1])
            for sql in incidents.DDL[2:5]:db.execute(sql)
            db.execute(f"INSERT INTO incidents({','.join(old)}) VALUES({','.join('?' for _ in old)})",tuple(old.values()))
            db.execute('PRAGMA user_version=2')
        return old

    def test_acknowledgement_is_idempotent_and_preserves_evidence(self):
        incident_id=self.opened();before=self.records()[0]
        with patch.object(app.app.logger,'info') as log:
            first=self.ack(incident_id,{'note':'  Investigating locally  '})
            second=self.ack(incident_id,{'note':'replacement must not overwrite'})
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json,second.json)
        self.assertEqual(first.json['state'],'ACKNOWLEDGED')
        self.assertEqual(first.json['operator_note'],'Investigating locally')
        self.assertIsNotNone(first.json['acknowledged_at'])
        self.assertEqual(first.json['first_observed'],before['first_observed'])
        self.assertEqual(first.json['thresholds']['threshold'],0)
        self.assertEqual(log.call_count,1)
        self.assertEqual(set(log.call_args.kwargs['extra']),{'event','condition','incident_id'})

    def test_acknowledged_incident_resolves_only_after_verified_recovery(self):
        incident_id=self.opened();self.ack(incident_id)
        self.save(3)
        self.assertEqual(self.records()[0]['state'],'ACKNOWLEDGED')
        self.save(4)
        row=self.records()[0]
        self.assertEqual((row['state'],row['duration_seconds']),('RESOLVED',4))
        self.assertIsNotNone(row['acknowledged_at'])
        self.assertEqual(self.ack(incident_id).json['state'],'RESOLVED')

    def test_unacknowledged_resolved_incident_returns_conflict(self):
        incident_id=self.opened();self.save(3);self.save(4)
        self.assertEqual(self.ack(incident_id).status_code,409)
        self.assertIsNone(self.records()[0]['acknowledged_at'])

    def test_unknown_measurements_and_more_failures_keep_acknowledged_state(self):
        incident_id=self.opened();self.ack(incident_id)
        self.save(3,tcp_ok=0,tcp_error='TCP check unavailable (unexpected execution error)')
        self.save(4,tcp=False);self.save(5,tcp=False);self.save(6,tcp=False)
        self.assertEqual(len(self.records()),1)
        self.assertEqual(self.records()[0]['state'],'ACKNOWLEDGED')

    def test_input_validation_and_local_request_restrictions(self):
        incident_id=self.opened();url=f'/api/incidents/{incident_id}/acknowledge'
        for body in ([],{'extra':1},{'note':False},{'note':[]},{'note':'x'*501},{'note':'bad\x00note'}):
            with self.subTest(body=type(body).__name__):self.assertEqual(self.client.post(url,json=body).status_code,400)
        self.assertEqual(self.client.post(url,data='{',content_type='application/json').status_code,400)
        self.assertEqual(self.client.post(url,data='text').status_code,415)
        self.assertEqual(self.client.post(url,data='x'*5000,content_type='application/json').status_code,413)
        self.assertEqual(self.client.post(url,json={},headers={'Origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.post(url,json={},headers={'Host':'foreign.example'}).status_code,403)
        self.assertEqual(self.client.post(url,json={},environ_overrides={'REMOTE_ADDR':'10.0.0.1'}).status_code,403)
        self.assertEqual(self.ack(999).status_code,404)
        self.assertEqual(self.ack(incident_id,{'note':'<script>literal text</script>'}).json['operator_note'],'<script>literal text</script>')

    def test_parallel_acknowledgements_commit_once(self):
        incident_id=self.opened();barrier=threading.Barrier(3);responses=[]
        def run(note):
            barrier.wait(3)
            with app.app.test_client() as client:
                responses.append(client.post(f'/api/incidents/{incident_id}/acknowledge',json={'note':note}))
        workers=[threading.Thread(target=run,args=(note,)) for note in ('first','second')]
        for worker in workers:worker.start()
        barrier.wait(3)
        for worker in workers:worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual([response.status_code for response in responses],[200,200])
        self.assertEqual(responses[0].json,responses[1].json)

    def test_summary_and_extended_filters_preserve_list_contract(self):
        incident_id=self.opened()
        summary=self.client.get('/api/incidents/summary').json
        self.assertEqual((summary['active'],summary['open'],summary['acknowledged']),(1,1,0))
        self.ack(incident_id)
        summary=self.client.get('/api/incidents/summary').json
        self.assertEqual((summary['active'],summary['open'],summary['acknowledged']),(1,0,1))
        self.assertEqual(summary['by_condition']['tcp_unavailable']['ACKNOWLEDGED'],1)
        for state in ('ACTIVE','ACKNOWLEDGED'):
            data=self.client.get('/api/incidents?state='+state).json
            self.assertEqual(len(data['incidents']),1)
            self.assertEqual(set(data),{'start','end','incidents','next_before_id'})
        self.assertEqual(self.client.get('/api/incidents?state=OPEN').json['incidents'],[])

    def test_v2_upgrade_preserves_rows_and_cursors_and_repeats(self):
        old=self.version_two()
        with app.get_db() as db:
            cursors=[tuple(r) for r in db.execute('SELECT * FROM incident_cursors')]
        app.init_db();app.init_db()
        new=self.records()[0]
        self.assertEqual({key:new[key] for key in old},old)
        self.assertIsNone(new['acknowledged_at'])
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM incident_cursors')],cursors)

    def test_mid_migration_failure_rolls_back_columns_and_version(self):
        old=self.version_two()
        with patch.object(incidents,'ACK_COLUMNS',[('acknowledged_at','TEXT'),('operator_note','INVALID(')]):
            with self.assertRaises(sqlite3.DatabaseError):app.init_db()
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertNotIn('acknowledged_at',{r[1] for r in db.execute('PRAGMA table_info(incidents)')})
            self.assertEqual(dict(db.execute('SELECT * FROM incidents').fetchone()),old)

    def test_ack_transaction_failure_preserves_state_and_emits_no_transition(self):
        incident_id=self.opened()
        with app.get_db() as db:db.execute("CREATE TRIGGER reject_ack BEFORE UPDATE OF acknowledged_at ON incidents BEGIN SELECT RAISE(ABORT,'private'); END")
        with patch.object(app.app.logger,'info') as log,patch.object(app.app.logger,'exception'):
            response=self.ack(incident_id)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private',response.get_data(as_text=True))
        self.assertEqual(self.records()[0]['state'],'OPEN')
        log.assert_not_called()
