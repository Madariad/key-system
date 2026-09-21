"""Create a shareable source archive, excluding passwords, databases and dependencies."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
target = root / 'dist' / 'key-system-source.zip'
target.parent.mkdir(exist_ok=True)
files = ['README.md', 'DOCKER.md', 'REPORT.md', 'RELEASE_REPORT.md', 'Dockerfile', '.dockerignore',
         '.gitignore', '.env.example', 'compose.yaml', 'compose.production.yaml',
         'Caddyfile', 'configure.py', 'package-source.py',
         'back-end/server.py', 'back-end/recognition.py', 'back-end/test_server.py',
         'back-end/verify_deployment.py',
         'back-end/requirements.txt', 'back-end/requirements-test.txt',
         'front-end/package.json', 'front-end/package-lock.json']
for directory in ('front-end/src', 'front-end/public', 'back-end/faces'):
    files.extend(str(p.relative_to(root)) for p in (root / directory).rglob('*') if p.is_file() and p.suffix.lower() != '.ds_store' and p.name != '.DS_Store')
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for filename in sorted(set(files)):
        source = root / filename
        if not source.is_file():
            raise SystemExit(f'Missing required source: {filename}')
        archive.write(source, 'key-system/' + source.relative_to(root).as_posix())
print(f'Created {target} ({target.stat().st_size:,} bytes). Includes project face photos; share privately.')
