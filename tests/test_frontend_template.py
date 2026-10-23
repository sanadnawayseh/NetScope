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
        self.assertIn('href="#incidents"',response.get_data(as_text=True))
        for key in ('name', 'ip', 'icmp', 'tcp', 'rtt', 'loss', 'checked'):
            self.assertEqual(template.tags['sort-' + key], 'button')
            self.assertEqual(template.tags['sort-heading-' + key], 'th')

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
