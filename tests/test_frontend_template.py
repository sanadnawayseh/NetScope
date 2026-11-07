"""Verify template bindings the lightweight JavaScript DOM harness cannot check."""
import re
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import app as netscope

class Template(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tags = {}
        self.attributes = {}
    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if 'id' in attributes:
            self.ids.append(attributes['id'])
            self.tags[attributes['id']] = tag
            self.attributes[attributes['id']] = attributes

class FrontendTemplateTests(unittest.TestCase):
    def test_every_literal_javascript_binding_exists_once_in_rendered_template(self):
        response = netscope.app.test_client().get('/')
        self.assertEqual(response.status_code, 200)
        template = Template()
        template.feed(response.get_data(as_text=True))
        script = (Path(netscope.BASE) / 'static' / 'app.js').read_text(encoding='utf-8')
        script += (Path(netscope.BASE) / 'static' / 'notifications.js').read_text(encoding='utf-8')
        script += (Path(netscope.BASE) / 'static' / 'discovery.js').read_text(encoding='utf-8')
        script += (Path(netscope.BASE) / 'static' / 'network-map.js').read_text(encoding='utf-8')
        script += (Path(netscope.BASE) / 'static' / 'analytics.js').read_text(encoding='utf-8')
        script += (Path(netscope.BASE) / 'static' / 'reports.js').read_text(encoding='utf-8')
        ids = set(re.findall(r"\$\('([^']+)'\)", script))
        counts = Counter(template.ids)
        for identifier in ids:
            with self.subTest(identifier=identifier):
                self.assertEqual(counts[identifier], 1)
        self.assertFalse([key for key, count in counts.items() if count > 1])
        self.assertEqual(template.tags['check'], 'button')
        self.assertEqual(template.tags['targets'], 'tbody')
        self.assertEqual(template.tags['incident-state'], 'select')
        self.assertEqual(template.attributes['incident-detail']['tabindex'], '-1')
        self.assertEqual(template.attributes['incident-status']['role'], 'status')
        self.assertEqual(template.tags['notification-read-all'], 'button')
        self.assertEqual(template.tags['notification-older'], 'button')
        self.assertEqual(template.attributes['notification-panel']['tabindex'], '-1')
        for obsolete in ('desktop-toggle', 'desktop-test', 'desktop-status'):
            self.assertNotIn(obsolete, template.ids)
        self.assertIn('href="#incidents"',response.get_data(as_text=True))
        for key in ('name', 'ip', 'icmp', 'tcp', 'rtt', 'loss', 'checked'):
            self.assertEqual(template.tags['sort-' + key], 'button')
            self.assertEqual(template.tags['sort-heading-' + key], 'th')

    def test_history_record_controls_have_accessible_native_bindings(self):
        template = Template()
        template.feed(netscope.app.test_client().get('/').get_data(as_text=True))
        self.assertEqual(template.tags['timeline-range'], 'select')
        self.assertIn('hidden', template.attributes['timeline-details'])
        self.assertEqual(template.attributes['timeline-details']['aria-labelledby'], 'timeline-detail-heading')
        self.assertEqual(template.attributes['timeline-detail-heading']['tabindex'], '-1')
        self.assertEqual(template.tags['timeline-records'], 'tbody')
        for identifier in ('timeline-close', 'timeline-reset', 'timeline-zoom'):
            self.assertEqual(template.tags[identifier], 'button')
            self.assertEqual(template.attributes[identifier]['type'], 'button')

    def test_history_outcome_legend_sits_directly_before_timeline(self):
        html = netscope.app.test_client().get('/').get_data(as_text=True)
        section = html.split('id="availability-history"', 1)[1].split('id="timeline-details"', 1)[0]
        self.assertLess(section.index('id="timeline-range"'), section.index('class="timeline-legend"'))
        self.assertLess(section.index('class="timeline-legend"'), section.index('id="check-timeline"'))
        self.assertIn('aria-label="Check outcomes"', section)
        for state in ('success', 'failure', 'unknown'):
            self.assertIn(f'class="{state}" aria-hidden="true"></i>', section)

    def test_add_device_disclosure_is_closed_and_accessibly_bound(self):
        html = netscope.app.test_client().get('/').get_data(as_text=True)
        template = Template()
        template.feed(html)
        trigger = template.attributes['add-device-toggle']
        self.assertEqual(template.tags['add-device-toggle'], 'button')
        self.assertEqual(trigger['type'], 'button')
        self.assertEqual(trigger['aria-expanded'], 'false')
        self.assertEqual(trigger['aria-controls'], 'add-device-panel')
        self.assertIn('hidden', template.attributes['add-device-panel'])
        self.assertEqual(template.attributes['add-device-panel']['aria-labelledby'], 'add-device-heading')
        self.assertEqual(template.attributes['add-device-close']['type'], 'button')
        self.assertLess(html.index('id="add-device-toggle"'), html.index('class="device-table"'))
        self.assertRegex(html, r'<th scope="col" class="actions-heading">Actions</th>')

    def test_declutter_preserves_safety_and_measurement_context(self):
        html = netscope.app.test_client().get('/').get_data(as_text=True)
        template = Template()
        template.feed(html)
        for removed in ('Local only', 'Local workspace', 'Flask / SQLite', 'Private network monitoring',
                        'Local-first. Real measurements.', 'Two workers', 'SQL window'):
            self.assertNotIn(removed, html)
        self.assertNotIn('discovery-authorized', template.ids)
        self.assertNotIn('trace-authorized', template.ids)
        for essential in ('Connections are unverified', 'not continuous uptime',
                          'Unknown means', 'Stale means'):
            self.assertIn(essential, html)
        self.assertEqual(template.attributes['network-map-svg']['aria-describedby'], 'network-map-help')
        self.assertNotIn('discovery-summary', template.ids)
        self.assertNotIn('aria-describedby', template.attributes['discovery-cidr'])
        self.assertNotIn('class="topbar"', html)
        self.assertNotIn('class="slash"', html)
        self.assertIn('<h1>Network overview</h1>', html)
        self.assertEqual(template.tags['notification-toggle'], 'button')

    def test_discovery_uses_a_native_per_scan_confirmation_dialog(self):
        html = netscope.app.test_client().get('/').get_data(as_text=True)
        template = Template(); template.feed(html)
        self.assertEqual(template.tags['discovery-confirmation'], 'dialog')
        self.assertNotIn('open', template.attributes['discovery-confirmation'])
        self.assertEqual(template.attributes['discovery-confirmation']['aria-labelledby'], 'discovery-confirm-title')
        self.assertEqual(template.attributes['discovery-confirmation']['aria-describedby'], 'discovery-confirm-warning')
        self.assertIn('Only scan networks you own or are authorized to test.', html)
        self.assertIn('autofocus', template.attributes['discovery-confirm-cancel'])
        for control in ('discovery-confirm-cancel', 'discovery-confirm-scan'):
            self.assertEqual(template.attributes[control]['type'], 'button')
        self.assertNotIn('trace-authorized', template.ids)
        self.assertNotIn('discovery-authorized', template.ids)

    def test_manual_diagnostics_is_compact_with_contextual_help_and_direct_traceroute(self):
        html = netscope.app.test_client().get('/').get_data(as_text=True)
        template = Template()
        template.feed(html)
        manual = html.split('class="panel manual-diagnostics"', 1)[1]
        for removed in ('NAME RESOLUTION', 'ROUTE DIAGNOSTICS', 'DNS diagnostics',
                        'Windows only', 'dns-scope', 'trace-scope', 'means no reply'):
            self.assertNotIn(removed, manual)
        self.assertIn('<h3>DNS Lookup</h3>', manual)
        self.assertIn('<h3>Traceroute</h3>', manual)
        self.assertIn('Look up DNS', manual)
        self.assertIn('Run traceroute', manual)
        self.assertIn('hidden', template.attributes['dns-help'])
        self.assertNotIn('trace-authorized', template.ids)
        self.assertEqual(template.attributes['trace-hops']['min'], '1')
        self.assertEqual(template.attributes['trace-hops']['max'], '20')
        self.assertEqual(template.attributes['dns-status']['aria-live'], 'polite')
        self.assertEqual(template.attributes['trace-status']['aria-live'], 'polite')
