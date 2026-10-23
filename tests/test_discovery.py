"""Mocked discovery only. Never invokes ping or scans a real network."""
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import app
import network_discovery as discovery
import test_app


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        test_app.ApplicationTests.setUp(self)
        self.old_service = app.discovery_service
        self.probe = Mock(return_value='responding')
        self.service = discovery.Discovery(app.get_db, app.validate_ip, self.probe, app.lock,
                                           app.trace_service.slot, app.app.logger)
        app.discovery_service = self.service

    def tearDown(self):
        self.service.shutdown()
        app.discovery_service = self.old_service
        test_app.ApplicationTests.tearDown(self)

    def start(self, cidr='192.168.1.0/30'):
        return self.client.post('/api/discovery/jobs', json=dict(cidr=cidr, authorized=True))

    def finish(self, response):
        self.assertEqual(response.status_code, 202, response.json)
        self.service.thread.join(3)
        self.assertFalse(self.service.thread.is_alive(), 'Mocked discovery did not settle')
        return self.client.get(response.headers['Location']).json

    def inventory(self):
        return self.client.get('/api/discovery/inventory').json['observations']

    def test_cidr_boundaries_and_exact_authorization(self):
        for cidr, expected in [('127.0.0.1/32', 1), ('10.0.0.0/31', 2),
                               ('172.16.0.0/27', 30), ('192.168.1.0/30', 2)]:
            hosts = discovery.scope(cidr, True, app.validate_ip)
            self.assertEqual(len(hosts), expected)
        for cidr in ('0.0.0.0/0', '8.8.8.8/32', '169.254.0.0/30', '224.0.0.0/30',
                     '10.0.0.0/26', '192.168.1.1/30', '192.168.1.0/255.255.255.252',
                     '::1/128', '10.0.0.1', '10.0.0.0/7', '100.64.0.0/30', None):
            with self.subTest(cidr=cidr), self.assertRaises(discovery.DiscoveryError):
                discovery.scope(cidr, True, app.validate_ip)
        for authorized in (False, 1, 'true', None):
            with self.assertRaises(discovery.DiscoveryError):
                discovery.scope('127.0.0.1/32', authorized, app.validate_ip)
        self.probe.assert_not_called()

    def test_no_automatic_scan_and_api_security(self):
        self.client.get('/'); self.client.get('/api/discovery/inventory');app.init_db()
        self.probe.assert_not_called()
        body=dict(cidr='127.0.0.1/32',authorized=True)
        for kwargs in (dict(environ_base={'REMOTE_ADDR':'10.0.0.1'}),
                       dict(headers={'Host':'evil.test'}),dict(headers={'Origin':'https://evil.test'}),
                       dict(headers={'Sec-Fetch-Site':'cross-site'})):
            self.assertEqual(self.client.post('/api/discovery/jobs',json=body,**kwargs).status_code,403)
        for data in ({},dict(cidr='127.0.0.1/32',authorized=False),dict(body,extra=1),[]):
            self.assertEqual(self.client.post('/api/discovery/jobs',json=data).status_code,400)
        self.assertEqual(self.client.post('/api/discovery/jobs',data='{}').status_code,415)
        self.probe.assert_not_called()

    def test_nonblocking_progress_concurrency_and_persisted_results(self):
        entered=threading.Event();release=threading.Event();mutex=threading.Lock()
        calls=0;active=0;maximum=0
        def probe(address, timeout):
            nonlocal calls, active, maximum
            with mutex:
                calls+=1;active+=1;maximum=max(maximum,active)
                if calls==2:entered.set()
            self.assertTrue(release.wait(3))
            with mutex:active-=1
            return 'responding'
        self.probe.side_effect=probe
        response=self.start('192.168.1.0/29')
        try:
            self.assertEqual(response.status_code,202)
            self.assertTrue(entered.wait(2))
            status=self.client.get(response.headers['Location']).json
            self.assertEqual(status['completed'],0)
            self.assertEqual(status['progress_percentage'],0)
            self.assertEqual(self.inventory(),[])
            self.assertEqual(self.start().status_code,409)
            self.assertEqual(self.client.post('/api/check-jobs').status_code,409)
            self.assertEqual(self.client.post('/api/targets',json={'name':'x','ip':'10.0.0.1'}).status_code,409)
        finally:release.set()
        job=self.finish(response)
        self.assertEqual((job['state'],job['completed'],job['failed'],job['progress_percentage']),('completed',6,0,100))
        self.assertEqual(maximum,2)
        self.assertEqual(len(self.inventory()),6)
        self.assertEqual([r['method'] for r in self.inventory()],['icmp_echo']*6)
        self.assertEqual(len(self.client.get('/api/targets').json),1)

    def test_admission_rejects_monitoring_and_traceroute_without_quota_charge(self):
        for slot in (app.lock,app.trace_service.slot):
            slot.acquire()
            try:self.assertEqual(self.start().status_code,409)
            finally:slot.release()
        self.assertEqual(len(self.service.requests),0)
        self.assertEqual(self.finish(self.start())['state'],'completed')

    def test_progress_waits_for_committed_persistence(self):
        entered=threading.Event();release=threading.Event()
        persist=self.service._persist
        def blocked(job,outcome):
            entered.set()
            if not release.wait(3):raise RuntimeError('Test persistence gate timed out')
            persist(job,outcome)
        with patch.object(self.service,'_persist',side_effect=blocked):
            response=self.start('127.0.0.1/32')
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.get(response.headers['Location']).json['completed'],0)
                self.assertEqual(self.inventory(),[])
            finally:release.set()
            self.assertEqual(self.finish(response)['completed'],1)

    def test_shutdown_cancels_active_job_and_releases_admission(self):
        entered=threading.Event();release=threading.Event()
        def probe(*args):entered.set();release.wait(3);return 'responding'
        self.probe.side_effect=probe
        response=self.start('10.0.0.0/27')
        self.assertTrue(entered.wait(2))
        shutdown=threading.Thread(target=self.service.shutdown)
        shutdown.start()
        try:
            self.assertTrue(self.service.jobs[response.json['job_id']]['cancel'].wait(2))
        finally:release.set();shutdown.join(3)
        self.assertFalse(shutdown.is_alive())
        self.assertEqual(self.finish(response)['state'],'cancelled')
        self.assertTrue(app.lock.acquire(False));app.lock.release()

    def test_rate_limit_and_clock_expiration(self):
        stamp=[100.0];self.service.clock=lambda:stamp[0]
        self.finish(self.start());self.finish(self.start())
        self.assertEqual(self.start().status_code,429)
        stamp[0]+=60
        self.assertEqual(self.finish(self.start())['state'],'completed')

    def test_cancellation_and_shutdown_settle_without_synthetic_rows(self):
        entered=threading.Event();release=threading.Event()
        def probe(*args):entered.set();release.wait(3);return 'unresponsive'
        self.probe.side_effect=probe
        response=self.start('10.0.0.0/27')
        try:
            self.assertTrue(entered.wait(2))
            path=response.headers['Location']+'/cancel'
            self.assertEqual(self.client.post(path,json={}).status_code,200)
            self.assertEqual(self.client.post(path,json={}).status_code,200)
        finally:release.set()
        job=self.finish(response)
        self.assertEqual(job['state'],'cancelled')
        self.assertLess(job['completed'],job['total'])
        self.assertEqual(len(self.inventory()),job['completed'])
        self.service.shutdown()
        self.assertEqual(self.start().status_code,503)
        self.assertTrue(app.lock.acquire(False));app.lock.release()

    def test_unknown_and_probe_exception_are_not_verified_devices(self):
        self.probe.side_effect=['unknown',RuntimeError('private network metadata')]
        with patch.object(app.app.logger,'exception'):
            job=self.finish(self.start())
        self.assertEqual((job['state'],job['completed'],job['failed']),('failed',2,2))
        self.assertNotIn('private',job['error'])
        rows=self.inventory();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['classification'],'unknown')
        self.assertEqual(self.client.post(f"/api/discovery/observations/{rows[0]['id']}/enroll",json={'name':'x','port':80}).status_code,409)

    def test_database_failure_does_not_advance_success_or_change_history(self):
        with app.get_db() as db:
            db.execute("CREATE TRIGGER reject_discovery BEFORE INSERT ON discovery_observations BEGIN SELECT RAISE(ABORT,'private'); END")
        with patch.object(app.app.logger,'exception'):
            job=self.finish(self.start())
        self.assertEqual((job['state'],job['completed'],job['failed']),('failed',2,2))
        self.assertEqual(self.inventory(),[])
        with app.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM targets').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],0)

    def test_explicit_enrollment_is_validated_and_duplicate_safe(self):
        self.finish(self.start())
        row=self.inventory()[0];path=f"/api/discovery/observations/{row['id']}/enroll"
        for body in ({'name':'','port':80},{'name':'x','port':0},{'name':'x','port':True},{'name':'x','port':80,'ip':'8.8.8.8'}):
            self.assertEqual(self.client.post(path,json=body).status_code,400)
        first=self.client.post(path,json={'name':'Authorized device','port':443})
        self.assertEqual(first.status_code,201)
        duplicate=self.client.post(path,json={'name':'Again','port':80})
        self.assertEqual(duplicate.status_code,409)
        self.assertEqual(duplicate.json['target_id'],first.json['id'])
        target=self.client.get('/api/targets').json[-1]
        self.assertEqual((target['ip'],target['port']),(row['address'],443))
        self.assertEqual(self.client.post('/api/discovery/observations/999/enroll',json={'name':'x','port':80}).status_code,404)

    def test_restart_preserves_inventory_but_not_process_local_jobs(self):
        response=self.start();self.finish(response);rows=self.inventory()
        app.init_db()
        restarted=discovery.Discovery(app.get_db,app.validate_ip,self.probe,app.lock,app.trace_service.slot,app.app.logger)
        self.assertIsNone(restarted.status(response.json['job_id']))
        self.assertEqual(self.inventory(),rows)
        restarted.shutdown()

    def test_pagination_latest_inventory_and_retention(self):
        with app.get_db() as db:
            db.executemany("INSERT INTO discovery_observations(scan_id,cidr,address,method,observed_at,classification) VALUES(?,'10.0.0.0/30',?,'icmp_echo','2026-10-23T00:00:00Z','responding')",
                           [(str(i),'10.0.0.'+str(i%2+1)) for i in range(2050)])
        self.service._persist({'job_id':'next','cidr':'10.0.0.0/30'},('10.0.0.1','unresponsive',discovery.now()))
        with app.get_db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM discovery_observations').fetchone()[0],2048)
        self.assertEqual(len(self.inventory()),2)
        first=self.client.get('/api/discovery/observations?limit=2').json
        second=self.client.get('/api/discovery/observations?limit=2&before_id='+str(first['next_before_id'])).json
        self.assertGreater(first['observations'][-1]['id'],second['observations'][0]['id'])
        for query in ('limit=201','limit=0','before_id=-1','limit=1&limit=2','unknown=1'):
            self.assertEqual(self.client.get('/api/discovery/inventory?'+query).status_code,400)

    def test_v4_upgrade_and_repeat_preserve_existing_data(self):
        with app.get_db() as db:
            db.execute('DROP TABLE discovery_observations');db.execute('PRAGMA user_version=4')
            targets=[tuple(r) for r in db.execute('SELECT * FROM targets')]
        app.init_db();app.init_db()
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM targets')],targets)
        self.probe.assert_not_called()

    def test_migration_failure_rolls_back_version_and_schema(self):
        with app.get_db() as db:
            db.execute('DROP TABLE discovery_observations');db.execute('PRAGMA user_version=4')
        with patch.object(discovery,'DDL',(discovery.DDL[0],'INVALID SQL')):
            with self.assertRaises(sqlite3.Error):app.init_db()
        with app.get_db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='discovery_observations'").fetchone())

    def test_single_echo_command_is_bounded_and_shell_free(self):
        with patch.object(discovery.platform,'system',return_value='Windows'), patch.object(discovery.subprocess,'run') as run:
            run.return_value=Mock(returncode=0,stdout='reply')
            self.assertEqual(discovery.echo('10.0.0.1',500,lambda text:(True,)),'responding')
            args,kwargs=run.call_args
            self.assertEqual(args[0][-5:],['-n','1','-w','500','10.0.0.1'])
            self.assertEqual(kwargs['timeout'],1.5);self.assertFalse(kwargs['shell'])
            run.side_effect=FileNotFoundError()
            self.assertEqual(discovery.echo('10.0.0.1',500,lambda text:(True,)),'unknown')

    def test_job_retention_and_missing_job_errors(self):
        self.service.clock=lambda:0
        for _ in range(34):
            self.service.requests.clear();self.finish(self.start('127.0.0.1/32'))
        self.assertEqual(len(self.service.jobs),32)
        self.assertEqual(self.client.get('/api/discovery/jobs/missing').status_code,404)
        self.assertEqual(self.client.post('/api/discovery/jobs/missing/cancel',json={}).status_code,404)
