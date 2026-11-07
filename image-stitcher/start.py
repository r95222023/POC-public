"""Idempotent dev startup with health checks; processes do not survive sleep."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
HEALTH = 'http://127.0.0.1:5000/api/health'
opener = build_opener(ProxyHandler({}))  # This health request is local only.


def healthy():
    try:
        with opener.open(HEALTH, timeout=1) as response:
            result = json.load(response)
        return result.get('status') == 'ok' and result.get('version') == 'gallery-2'
    except (URLError, TimeoutError, OSError, ValueError):
        return False


def main():
    if healthy():
        print('Overlap is already running on port 5000.')
        return
    output = ROOT / 'outputs'
    output.mkdir(exist_ok=True)
    with (output / 'server.log').open('ab') as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / 'app.py'), '--host', '0.0.0.0', '--port', '5000'],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(50):
        if process.poll() is not None:
            raise SystemExit('Server exited; see outputs/server.log. No existing process was stopped.')
        if healthy():
            (output / 'server.pid').write_text(str(process.pid) + '\n')
            print(f'Overlap ready on port 5000 (PID {process.pid}).')
            print('This is a cloud-local service, not a public URL; use a supported desktop or port preview.')
            return
        time.sleep(.2)
    raise SystemExit('Server did not become ready; see outputs/server.log.')


if __name__ == '__main__':
    main()
