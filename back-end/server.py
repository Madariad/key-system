"""Single-origin web app: cameras belong to browsers, never the server."""
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import datetime
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Literal
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
ACTIONS = {'issue': 'Кілтті алу (Выдан)', 'return': 'Кілтті өткізу (Сдан)'}

@dataclass
class Settings:
    data_dir: Path = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
    faces_dir: Path = Path(os.getenv('FACES_DIR', str(ROOT / 'faces')))
    static_dir: Path = Path(os.getenv('STATIC_DIR', str(ROOT / 'static')))
    app_password: str = os.getenv('APP_PASSWORD', '')
    admin_password: str = os.getenv('ADMIN_PASSWORD', '')
    cookie_secure: bool = os.getenv('COOKIE_SECURE', 'true').lower() == 'true'

class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)

class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scan_id: str = Field(min_length=20, max_length=100)
    action: Literal['issue', 'return']

def create_app(settings=None, recognizer=None):
    settings = settings or Settings()
    if recognizer is None:
        from recognition import Recognizer
        recognizer = Recognizer(settings.faces_dir, settings.data_dir / 'face-cache.json')

    @contextmanager
    def database():
        conn = sqlite3.connect(settings.data_dir / 'key_management.db', timeout=10)
        conn.row_factory = sqlite3.Row
        conn.create_function('CASEFOLD', 1, lambda value: (value or '').casefold(), deterministic=True)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @asynccontextmanager
    async def lifespan(app):
        if min(len(settings.app_password), len(settings.admin_password)) < 12:
            raise RuntimeError('Set APP_PASSWORD and ADMIN_PASSWORD (12+ characters). Run python configure.py.')
        if settings.app_password == settings.admin_password:
            raise RuntimeError('APP_PASSWORD and ADMIN_PASSWORD must be different.')
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with database() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS key_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT,
                    cabinet_info TEXT, action TEXT, timestamp DATETIME);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, role TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, person TEXT NOT NULL,
                    cabinet TEXT NOT NULL, expires REAL NOT NULL, action TEXT, log_id INTEGER);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    address TEXT PRIMARY KEY, attempts INTEGER NOT NULL, resets REAL NOT NULL);
            ''')
        recognizer.start()
        yield

    app = FastAPI(title='Key System', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.recognizer = recognizer

    @app.middleware('http')
    async def headers(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
            if origin and origin != expected:
                return Response('Cross-origin request rejected', status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(self), microphone=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def session(request: Request):
        digest = hashlib.sha256(request.cookies.get('key_session', '').encode()).hexdigest()
        with database() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE token=? AND expires>?', (digest, time.time())).fetchone()
        if row is None:
            raise HTTPException(401, 'Войдите в систему.')
        return dict(row)

    def admin(current=Depends(session)):
        if current['role'] != 'admin':
            raise HTTPException(403, 'Требуется доступ администратора.')
        return current

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.post('/api/login')
    def login(data: Login, request: Request, response: Response):
        address = request.client.host if request.client else 'unknown'
        now = time.time()
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM login_attempts WHERE resets<?', (now,))
            attempt = conn.execute('SELECT * FROM login_attempts WHERE address=?', (address,)).fetchone()
            if attempt and attempt['attempts'] >= 10:
                raise HTTPException(429, 'Слишком много попыток. Повторите через 5 минут.')
            role = ('admin' if hmac.compare_digest(data.password.encode(), settings.admin_password.encode()) else
                    'scanner' if hmac.compare_digest(data.password.encode(), settings.app_password.encode()) else None)
            if not role:
                conn.execute('INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(address) DO UPDATE SET attempts=attempts+1', (address, now + 300))
            else:
                conn.execute('DELETE FROM login_attempts WHERE address=?', (address,))
                conn.execute('DELETE FROM sessions WHERE expires<?', (now,))
                token = secrets.token_urlsafe(32)
                conn.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), role, now + 28800))
        if not role:
            raise HTTPException(401, 'Неверный пароль.')
        response.set_cookie('key_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', max_age=28800)
        return {'role': role}

    @app.get('/api/session')
    def whoami(current=Depends(session)):
        return {'role': current['role']}

    @app.post('/api/logout')
    def logout(response: Response, current=Depends(session)):
        with database() as conn:
            conn.execute('DELETE FROM sessions WHERE token=?', (current['token'],))
            conn.execute('DELETE FROM scans WHERE session=?', (current['token'],))
        response.delete_cookie('key_session', secure=settings.cookie_secure, httponly=True, samesite='strict')
        return {'status': 'ok'}

    @app.get('/api/status')
    def status(current=Depends(session)):
        return recognizer.status()

    @app.post('/api/scan')
    async def scan(request: Request, current=Depends(session)):
        if request.headers.get('content-type', '').split(';')[0] != 'image/jpeg':
            raise HTTPException(415, 'Отправьте JPEG-кадр.')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1_000_000:
                raise HTTPException(413, 'Кадр слишком большой (максимум 1 МБ).')
        if not body:
            raise HTTPException(400, 'Пустой кадр.')
        try:
            result = await run_in_threadpool(recognizer.scan, bytes(body))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error
        if result.get('person') and result.get('cabinet'):
            scan_id = secrets.token_urlsafe(32)
            with database() as conn:
                conn.execute('DELETE FROM scans WHERE expires<?', (time.time() - 86400,))
                conn.execute('DELETE FROM scans WHERE session=? AND action IS NULL', (current['token'],))
                conn.execute('INSERT INTO scans (id,session,person,cabinet,expires) VALUES (?,?,?,?,?)',
                             (scan_id, current['token'], result['person'], result['cabinet'], time.time() + 90))
            result['scan_id'] = scan_id
        return result

    @app.post('/api/confirm-action')
    def confirm(data: Confirmation, current=Depends(session)):
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM scans WHERE id=? AND session=?', (data.scan_id, current['token'])).fetchone()
            if row is None:
                raise HTTPException(404, 'Сканирование не найдено.')
            if row['action']:
                if row['action'] != data.action:
                    raise HTTPException(409, 'Это сканирование уже подтверждено.')
                return {'status': 'success', 'log_id': row['log_id'], 'duplicate': True}
            if row['expires'] < time.time():
                raise HTTPException(409, 'Сканирование устарело. Повторите его.')
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
            cursor = conn.execute('INSERT INTO key_logs (full_name,cabinet_info,action,timestamp) VALUES (?,?,?,?)',
                                  (row['person'], row['cabinet'], ACTIONS[data.action], timestamp))
            log_id = cursor.lastrowid
            conn.execute('UPDATE scans SET action=?, log_id=? WHERE id=?', (data.action, log_id, data.scan_id))
        return {'status': 'success', 'log_id': log_id, 'duplicate': False}

    @app.get('/api/logs')
    def logs(limit: int = 200, offset: int = 0, search: str = '', current=Depends(admin)):
        if not 1 <= limit <= 200 or offset < 0 or len(search) > 200:
            raise HTTPException(422, 'Недопустимые параметры журнала.')
        pattern = '%' + search.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where = "WHERE CASEFOLD(full_name) LIKE ? ESCAPE '\\' OR CASEFOLD(cabinet_info) LIKE ? ESCAPE '\\'"
        with database() as conn:
            rows = conn.execute('SELECT id,full_name,cabinet_info,action,timestamp FROM key_logs ' + where + ' ORDER BY id DESC LIMIT ? OFFSET ?', (pattern, pattern, limit, offset)).fetchall()
            total = conn.execute('SELECT count(*) FROM key_logs ' + where, (pattern, pattern)).fetchone()[0]
        return {'logs': [list(row) for row in rows], 'total': total}

    @app.delete('/api/clear-logs')
    def clear(current=Depends(admin)):
        with database() as conn:
            conn.execute('DELETE FROM key_logs')
        return {'status': 'cleared'}

    if settings.static_dir.is_dir():
        app.mount('/static', StaticFiles(directory=settings.static_dir / 'static'), name='static')

        @app.get('/')
        def index():
            return FileResponse(settings.static_dir / 'index.html', headers={'Cache-Control': 'no-cache'})

        @app.get('/{filename}')
        def public_file(filename: str):
            if filename not in ('favicon.ico', 'manifest.json', 'logo192.png', 'logo512.png', 'robots.txt'):
                raise HTTPException(404)
            return FileResponse(settings.static_dir / filename)
    return app

app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')),
                proxy_headers=True, forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS', '127.0.0.1'))
