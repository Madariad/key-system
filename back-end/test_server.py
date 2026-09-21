from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from fastapi.testclient import TestClient
from server import Settings, create_app

class FakeRecognizer:
    def start(self):
        pass
    def status(self):
        return {'state': 'ready', 'loaded': 1, 'total': 1, 'skipped': 0}
    def scan(self, data):
        if data == b'invalid':
            raise ValueError('Invalid image')
        if data == b'busy':
            raise RuntimeError('Busy')
        return {'person': 'Test Person', 'cabinet': '101'}

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.directory.name),
            static_dir=Path(self.directory.name) / 'absent',
            app_password='scanner-test-password', admin_password='admin-test-password', cookie_secure=False)
        self.app = create_app(self.settings, FakeRecognizer())
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def login(self, admin=False, client=None):
        client = client or self.client
        response = client.post('/api/login', json={'password': self.settings.admin_password if admin else self.settings.app_password})
        self.assertEqual(response.status_code, 200)
        return response

    def scan(self):
        response = self.client.post('/api/scan', content=b'fixture', headers={'Content-Type': 'image/jpeg'})
        self.assertEqual(response.status_code, 200)
        return response.json()['scan_id']

    def test_health_while_loading(self):
        self.assertEqual(self.client.get('/healthz').status_code, 200)

    def test_requires_authentication(self):
        for path in ('/api/status', '/api/logs', '/api/session'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.delete('/api/clear-logs').status_code, 401)
        self.assertEqual(self.client.post('/api/scan', content=b'frame').status_code, 401)

    def test_roles_and_logout(self):
        self.login()
        self.assertEqual(self.client.get('/api/session').json()['role'], 'scanner')
        self.assertEqual(self.client.get('/api/logs').status_code, 403)
        self.assertEqual(self.client.delete('/api/clear-logs').status_code, 403)
        self.assertEqual(self.client.post('/api/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/status').status_code, 401)

    def test_wrong_password_and_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/api/login', json={'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'password': 'wrong'}).status_code, 429)

    def test_cookie_and_cross_origin(self):
        response = self.login()
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=strict', cookie)
        self.assertEqual(self.client.post('/api/logout', headers={'Origin': 'https://foreign.example'}).status_code, 403)

    def test_confirm_is_idempotent_and_uses_server_identity(self):
        self.login()
        scan_id = self.scan()
        body = {'scan_id': scan_id, 'action': 'issue'}
        first = self.client.post('/api/confirm-action', json=body)
        again = self.client.post('/api/confirm-action', json=body)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(again.json()['duplicate'])
        self.assertEqual(first.json()['log_id'], again.json()['log_id'])
        self.assertEqual(self.client.post('/api/confirm-action', json={**body, 'action': 'return'}).status_code, 409)
        self.login(admin=True)
        logs = self.client.get('/api/logs').json()
        self.assertEqual(logs['total'], 1)
        self.assertEqual(logs['logs'][0][1:3], ['Test Person', '101'])

    def test_session_isolation(self):
        self.login()
        scan_id = self.scan()
        other = TestClient(self.app)
        self.login(client=other)
        response = other.post('/api/confirm-action', json={'scan_id': scan_id, 'action': 'issue'})
        self.assertEqual(response.status_code, 404)
        other.close()

    def test_expired_scan(self):
        self.login()
        scan_id = self.scan()
        with closing(sqlite3.connect(self.settings.data_dir / 'key_management.db')) as db:
            db.execute('UPDATE scans SET expires=? WHERE id=?', (time.time() - 1, scan_id))
            db.commit()
        self.assertEqual(self.client.post('/api/confirm-action', json={'scan_id': scan_id, 'action': 'issue'}).status_code, 409)

    def test_new_scan_invalidates_previous_unconfirmed_scan(self):
        self.login()
        old = self.scan()
        self.scan()
        self.assertEqual(self.client.post('/api/confirm-action', json={'scan_id': old, 'action': 'issue'}).status_code, 404)

    def test_invalid_payloads_are_4xx(self):
        self.login()
        for body in ([], {}, {'person': {'name': 'fake'}, 'cabinet': '101', 'action': 'anything'},
                     {'scan_id': 'x' * 30, 'action': 'issue', 'person': 'fake'}):
            self.assertEqual(self.client.post('/api/confirm-action', json=body).status_code, 422)
        self.assertEqual(self.client.post('/api/scan', content=b'frame').status_code, 415)
        headers = {'Content-Type': 'image/jpeg'}
        self.assertEqual(self.client.post('/api/scan', content=b'', headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/api/scan', content=b'invalid', headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/api/scan', content=b'busy', headers=headers).status_code, 503)
        self.assertEqual(self.client.post('/api/scan', content=b'x' * 1_000_001, headers=headers).status_code, 413)

    def test_journal_search_pagination_and_clear(self):
        self.login(admin=True)
        for action in ('issue', 'return'):
            self.client.post('/api/confirm-action', json={'scan_id': self.scan(), 'action': action})
        page = self.client.get('/api/logs?limit=1&offset=1&search=101').json()
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['logs']), 1)
        self.assertEqual(self.client.get('/api/logs?search=missing').json()['total'], 0)
        self.assertEqual(self.client.get('/api/logs?limit=10000').status_code, 422)
        self.assertEqual(self.client.delete('/api/clear-logs').status_code, 200)
        self.assertEqual(self.client.get('/api/logs').json()['total'], 0)

    def test_weak_configuration_rejected(self):
        self.settings.app_password = 'short'
        with self.assertRaises(RuntimeError):
            with TestClient(create_app(self.settings, FakeRecognizer())):
                pass

if __name__ == '__main__':
    unittest.main()
