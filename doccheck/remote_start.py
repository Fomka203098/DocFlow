import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
data = Path(os.environ.get('DOCCHECK_DATA_DIR', root / 'data'))
data.mkdir(exist_ok=True)
settings = data / 'remote.json'
pidfile = data / 'tunnel.pid'
binary = root / 'tools' / 'cloudflared.exe'
if settings.exists() or pidfile.exists():
    raise SystemExit('Remote session already exists. Stop it with STOP_REMOTE.cmd before starting another.')
if not binary.exists():
    raise SystemExit('tools/cloudflared.exe is missing; see README.md.')
log = data / 'tunnel.log'
with log.open('w', encoding='utf-8') as output:
    process = subprocess.Popen([str(binary), 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', 'http://127.0.0.1:8000'],
        cwd=root, stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    for _ in range(100):
        time.sleep(.5)
        text = log.read_text(encoding='utf-8', errors='replace')
        match = re.search(r'https://([a-z0-9-]+\.trycloudflare\.com)', text)
        if match:
            host = match.group(1)
            password = secrets.token_urlsafe(24)
            tmp = data / 'remote.json.tmp'
            public = os.environ.get('DOCCHECK_PUBLIC') == '1'
            tmp.write_text(json.dumps({'host':host, 'password':password, 'public':public}), encoding='utf-8')
            os.replace(tmp, settings)
            pidfile.write_text(str(process.pid), encoding='ascii')
            subprocess.Popen([sys.executable, str(root / 'keepawake.py'), str(process.pid)], cwd=root,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            print('URL: https://' + host)
            if not public:
                print('Username: doccheck')
                print('Password: ' + password)
            break
        if process.poll() is not None:
            raise RuntimeError('Cloudflare tunnel exited. See data/tunnel.log.')
    else:
        raise RuntimeError('Cloudflare did not provide a URL. See data/tunnel.log.')
except Exception:
    process.terminate()
    settings.unlink(missing_ok=True)
    pidfile.unlink(missing_ok=True)
    raise
