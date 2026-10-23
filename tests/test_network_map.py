"""Map integration reads isolated fixtures only, never starts a probe."""
import unittest
from unittest.mock import patch
import app
import test_app


class NetworkMapTests(unittest.TestCase):
    setUp=test_app.ApplicationTests.setUp
    tearDown=test_app.ApplicationTests.tearDown

    def snapshot(self):
        with app.get_db() as db:
            return [tuple(row) for row in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')], [tuple(row) for row in db.execute('SELECT * FROM targets')], [tuple(row) for row in db.execute('SELECT * FROM discovery_observations')]

    def test_map_template_and_sources_do_not_probe_or_write(self):
        with app.get_db() as db:
            db.execute("INSERT INTO discovery_observations(scan_id,cidr,address,method,observed_at,classification) VALUES('mock','10.0.0.0/30','10.0.0.1','icmp_echo','2026-10-23T00:00:00Z','unresponsive')")
        before=self.snapshot()
        with patch.object(app.discovery_service,'submit',side_effect=AssertionError('Unexpected scan')) as scan, patch.object(app,'ping',side_effect=AssertionError('Unexpected echo')) as ping, patch.object(app,'tcp_check',side_effect=AssertionError('Unexpected TCP')) as tcp:
            html=self.client.get('/').get_data(as_text=True)
            self.assertIn('id="network-map"',html)
            self.assertIn('href="#network-map"',html)
            self.assertIn('network-map.js',html)
            self.assertIn('No verified network links',html)
            self.assertEqual(self.client.get('/api/targets').status_code,200)
            inventory=self.client.get('/api/discovery/inventory?limit=200')
            self.assertEqual(inventory.json['observations'][0]['classification'],'unresponsive')
            self.assertEqual(self.client.get('/api/incidents?state=ACTIVE&range=30d&limit=200').status_code,200)
            scan.assert_not_called();ping.assert_not_called();tcp.assert_not_called()
        self.assertEqual(self.snapshot(),before)
        with app.get_db() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)

    def test_map_data_sources_keep_local_boundary_and_safe_failures(self):
        for route in ('/','/api/targets','/api/discovery/inventory?limit=200','/api/incidents?state=ACTIVE&range=30d&limit=200'):
            for kwargs in (dict(environ_base={'REMOTE_ADDR':'10.0.0.1'}),dict(headers={'Host':'evil.test'}),dict(headers={'Origin':'https://evil.test'})):
                self.assertEqual(self.client.get(route,**kwargs).status_code,403)
        with patch.object(app,'get_db',side_effect=app.sqlite3.OperationalError('secret')),patch.object(app.app.logger,'exception'):
            for route in ('/api/targets','/api/discovery/inventory?limit=200','/api/incidents?state=ACTIVE&range=30d&limit=200'):
                response=self.client.get(route)
                self.assertEqual(response.status_code,503)
                self.assertNotIn('secret',response.get_data(as_text=True))

    def test_map_has_keyboard_controls_and_explicit_topology_disclaimer(self):
        from test_frontend_template import Template
        html=self.client.get('/').get_data(as_text=True);template=Template();template.feed(html)
        self.assertEqual(template.tags['network-map-svg'],'svg')
        self.assertEqual(template.attributes['network-map-svg']['tabindex'],'0')
        self.assertEqual(template.attributes['network-map-details']['tabindex'],'-1')
        for key in ('network-map-zoom-in','network-map-zoom-out','network-map-reset','network-map-refresh'):
            self.assertEqual(template.attributes[key]['type'],'button')
        self.assertIn('display only',html)
        self.assertIn('No probes run when opening this map',html)
