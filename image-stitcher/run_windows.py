"""Run the local server in the console and open a browser after readiness."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:5000/'
opener = build_opener(ProxyHandler({}))


def healthy():
    try:
        with opener.open(URL + 'api/health', timeout=1) as response:
            data = json.load(response)
        return data.get('status') == 'ok' and data.get('version') == 'gallery-2'
    except (URLError, OSError, TimeoutError, ValueError):
        return False


def main():
    if healthy():
        print('Overlap is already running. Opening ' + URL)
        webbrowser.open(URL)
        return 0
    print('Starting Overlap. Keep this console open; press Ctrl+C to stop.')
    print('Local website: ' + URL)
    process = subprocess.Popen([sys.executable, str(ROOT / 'app.py'),
                                '--host', '127.0.0.1', '--port', '5000'], cwd=ROOT)

    def open_when_ready():
        for _ in range(100):
            if process.poll() is not None:
                return
            if healthy():
                webbrowser.open(URL)
                return
            time.sleep(.2)
        print('Readiness timed out. Check the server output above.')

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
        process.wait()
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
