"""Run inside an isolated backend container with a disposable SQLite database."""
import json
import os
import urllib.request
import urllib.error

BASE = 'http://127.0.0.1:8000'

if os.environ.get('KEY_SYSTEM_TEST_DATABASE') != '1':
    raise SystemExit('Refusing destructive checks without KEY_SYSTEM_TEST_DATABASE=1')

def request(path, data=None, method=None, raw=False):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read()
            return response.status, len(payload) if raw else json.loads(payload)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()

if request('/api/logs') != (200, {'logs': []}):
    raise SystemExit('Refusing to test a non-empty or unavailable database')

checks = [
    ('status', '/api/status', None, None, False),
    ('logs_initial', '/api/logs', None, None, False),
    ('video', '/video_feed', None, None, True),
    ('missing_fields', '/api/confirm-action', {}, 'POST', False),
    ('forged_identity_action', '/api/confirm-action', {'person': 'DOCKER_TEST', 'cabinet': 'TEST-101', 'action': 'arbitrary-action'}, 'POST', False),
    ('issue', '/api/confirm-action', {'person': 'DOCKER_TEST', 'cabinet': 'TEST-101', 'action': 'Кілтті алу (Выдан)'}, 'POST', False),
    ('return', '/api/confirm-action', {'person': 'DOCKER_TEST', 'cabinet': 'TEST-101', 'action': 'Кілтті өткізу (Сдан)'}, 'POST', False),
    ('logs_after_write', '/api/logs', None, None, False),
    ('wrong_body_type', '/api/confirm-action', ['unexpected'], 'POST', False),
    ('wrong_field_type', '/api/confirm-action', {'person': {'name': 'test'}, 'cabinet': '101', 'action': 'test'}, 'POST', False),
    ('delete_without_auth', '/api/clear-logs', None, 'DELETE', False),
    ('logs_after_delete', '/api/logs', None, None, False),
]
for name, path, data, method, raw in checks:
    try:
        status, result = request(path, data, method, raw)
        print(json.dumps({'check': name, 'http': status, 'result': result}, ensure_ascii=False), flush=True)
    except Exception as error:
        print(json.dumps({'check': name, 'error': str(error)}, ensure_ascii=False), flush=True)
