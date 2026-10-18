import unittest
from app import validate_ip

class ValidationTests(unittest.TestCase):
    def test_private_and_loopback(self):
        self.assertEqual(validate_ip('127.0.0.1'), '127.0.0.1')
        self.assertEqual(validate_ip('192.168.1.1'), '192.168.1.1')
    def test_public_rejected(self):
        for ip in ('8.8.8.8', '224.0.0.1', '169.254.1.1', '0.0.0.0', 'example.com',
                   '192.0.2.1', '198.51.100.1', '203.0.113.1', '240.0.0.1',
                   '100.64.0.1', '::1', '127.0.0.1 & whoami'):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                validate_ip(ip)

if __name__ == '__main__':
    unittest.main()
