"""Generate local credentials once; never overwrite an existing configuration."""
from pathlib import Path
import secrets

target = Path(__file__).resolve().parent / '.env'
if target.exists():
    print('.env already exists; left unchanged.')
else:
    with target.open('x', encoding='utf-8') as output:
        output.write(f'APP_PASSWORD={secrets.token_urlsafe(24)}\nADMIN_PASSWORD={secrets.token_urlsafe(24)}\nLOCAL_PORT=3000\nCOOKIE_SECURE=false\nDOMAIN=\n')
    print('Created .env with unique scanner and administrator passwords. Open it locally to read them. Do not share this file.')
