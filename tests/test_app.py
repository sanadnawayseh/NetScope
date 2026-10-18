import socket
import tempfile
import threading
import unittest
from pathlib import Path
import app as netscope


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = netscope.DB_PATH
        netscope.DB_PATH = Path(self.directory.name) / 'test.db'
        netscope.init_db()
        self.client = netscope.app.test_client()

    def tearDown(self):
        netscope.DB_PATH = self.previous
        self.directory.cleanup()

    def test_dashboard_and_target_lifecycle(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        response = self.client.post('/api/targets', json={'name': '<script>test</script>', 'ip': '10.0.0.1', 'port': 443})
        self.assertEqual(response.status_code, 201)
        target_id = response.json['id']
        self.assertEqual(self.client.get(f'/api/targets/{target_id}/history').json, [])
        target = self.client.get('/api/targets').json[-1]
        self.assertIsNone(target['ping_ok'])
        self.assertIsNone(target['tcp_ok'])
        self.assertEqual(self.client.delete(f'/api/targets/{target_id}').status_code, 204)
        self.assertEqual(self.client.delete(f'/api/targets/{target_id}').status_code, 404)

    def test_invalid_requests(self):
        for body in ([1], {'name': 'Public', 'ip': '8.8.8.8'}, {'name': 'Invalid', 'ip': '127.0.0.1', 'port': 65536}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/targets', json=body).status_code, 400)

    def test_real_loopback_checks_and_persistence(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen()
            port = server.getsockname()[1]
            response = self.client.post('/api/targets', json={'name': 'Local test service', 'ip': '127.0.0.1', 'port': port})
            target_id = response.json['id']
            def accept():
                connection, _ = server.accept()
                connection.close()
            worker = threading.Thread(target=accept, daemon=True)
            worker.start()
            result = netscope.run_check({'id': target_id, 'ip': '127.0.0.1', 'port': port})
            worker.join(timeout=3)
            self.assertTrue(result['tcp_ok'])
            self.assertIsInstance(result['ping_ok'], bool)
            history = self.client.get(f'/api/targets/{target_id}/history').json
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]['tcp_ok'], 1)
            self.assertEqual(bool(history[0]['ping_ok']), result['ping_ok'])
            if result['ping_ok']:
                if history[0]['latency_ms'] is not None:
                    self.assertGreaterEqual(history[0]['latency_ms'], 0)
                elif history[0]['latency_upper_bound_ms'] is not None:
                    self.assertGreater(history[0]['latency_upper_bound_ms'], 0)
                else:
                    self.assertIsNotNone(history[0]['ping_error'])
            else:
                self.assertIsNotNone(history[0]['error'])
            if result['icmp_sent'] is not None:
                self.assertEqual(result['icmp_sent'], 5)
                self.assertEqual(result['icmp_received'] + result['icmp_unanswered'], 5)
            print(f"\nReal packet batch: sent={result['icmp_sent']}, received={result['icmp_received']}, unanswered={result['icmp_unanswered']}, loss={result['packet_loss_pct']}%")
            print(f"\nReal loopback probe: ICMP={result['ping_ok']}, TCP={result['tcp_ok']}, RTT={result['latency_ms']} ms, upper bound={result['latency_upper_bound_ms']} ms")
            self.client.delete(f'/api/targets/{target_id}')
            self.assertEqual(self.client.get(f'/api/targets/{target_id}/history').json, [])
