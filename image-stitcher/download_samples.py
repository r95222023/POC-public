"""Fetch author-provided LEVIR-CD examples. Research use only; not redistributed."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

REVISION = 'adcd7aea6f234586ffffdd4e9959404f96271711'
BASE = f'https://raw.githubusercontent.com/justchenhao/BIT_CD/{REVISION}/samples'
NAMES = ['test_102_0512_0000.png', 'test_113_0256.png', 'test_121_0768_0256.png',
         'test_2_0000_0000.png', 'test_2_0000_0512.png', 'test_55_0256_0000.png',
         'test_77_0512_0256.png', 'test_7_0256_0512.png', 'train_36_0512_0512.png',
         'train_386_0512_0768.png', 'train_412_0512_0768.png', 'val_27_0000_0256.png']
ROOT = Path(__file__).parent / 'data' / 'levir'


def main():
    manifest = {'source': BASE, 'dataset': 'LEVIR-CD', 'restriction': 'Academic use only; no commercial use.',
                'terms': 'https://github.com/justchenhao/LEVIR#usage', 'files': []}
    for name in NAMES:
        for phase in ('A', 'B'):
            url = f'{BASE}/{phase}/{name}'
            with urlopen(url, timeout=60) as response:
                data = response.read()
            destination = ROOT / phase / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            manifest['files'].append({'path': f'{phase}/{name}', 'url': url,
                                      'sha256': hashlib.sha256(data).hexdigest()})
        print('Downloaded', name, flush=True)
    (ROOT / 'sources.json').write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
