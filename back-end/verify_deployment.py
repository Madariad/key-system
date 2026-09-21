"""Run inside the deployed container. Uses real recognition and removes only its test row."""
import http.cookiejar
import io
import json
import os
from pathlib import Path
import sqlite3
import urllib.request
from PIL import Image
from recognition import Recognizer

base = 'http://127.0.0.1:' + os.getenv('PORT', '8080')
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def request(path, body=None, content_type='application/json'):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=raw, headers={'Content-Type': content_type})
    with client.open(req, timeout=30) as response:
        return json.load(response)

request('/api/login', {'password': os.environ['ADMIN_PASSWORD']})
status = request('/api/status')
assert status['state'] == 'ready', status
print(json.dumps({'check': 'ready', **status}), flush=True)

# A QR generated locally by the diagnostic runner is passed through stdin as a file.
qr_path = Path('/tmp/key-system-test-qr.png')
qr = Image.open(qr_path).convert('RGB').resize((200, 200), Image.Resampling.NEAREST)
frame = Image.new('RGB', (800, 600), 'white')
cache = json.loads((Path(os.environ['DATA_DIR']) / 'face-cache.json').read_text())
import hashlib
selected = None
for path in sorted(Path(os.environ['FACES_DIR']).iterdir()):
    if path.suffix.lower() not in ('.jpg', '.png', '.jpeg'):
        continue
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if 'v1-800-hog-' + digest in cache:
        selected = path
        break
assert selected is not None
photo = Image.fromarray(Recognizer.image(selected))
photo.thumbnail((580, 590))
frame.paste(photo, (0, 0))
frame.paste(qr, (590, 390))
buffer = io.BytesIO()
frame.save(buffer, format='JPEG', quality=95)
scan = request('/api/scan', buffer.getvalue(), 'image/jpeg')
assert scan.get('person') == selected.stem and scan.get('cabinet') == '__DEPLOYMENT_TEST__', scan
print(json.dumps({'check': 'face_and_qr', 'matched': True, 'scan_id_received': bool(scan.get('scan_id'))}), flush=True)
log_id = None
try:
    body = {'scan_id': scan['scan_id'], 'action': 'issue'}
    first = request('/api/confirm-action', body)
    log_id = first['log_id']
    repeated = request('/api/confirm-action', body)
    assert repeated['duplicate'] and repeated['log_id'] == log_id
    logs = request('/api/logs?search=__DEPLOYMENT_TEST__')
    assert any(row[0] == log_id for row in logs['logs'])
    print(json.dumps({'check': 'confirm_and_journal', 'passed': True, 'idempotent': True}), flush=True)
finally:
    if log_id is not None:
        db = sqlite3.connect(Path(os.environ['DATA_DIR']) / 'key_management.db')
        try:
            with db:
                db.execute('DELETE FROM key_logs WHERE id=? AND cabinet_info=?', (log_id, '__DEPLOYMENT_TEST__'))
                db.execute('DELETE FROM scans WHERE id=?', (scan['scan_id'],))
        finally:
            db.close()
    request('/api/logout', {})
