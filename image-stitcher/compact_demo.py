"""Reduce the standalone research demo for file preview without HTTP hosting."""
import base64
from io import BytesIO
from pathlib import Path
import re
from PIL import Image


def compact(source, destination):
    html = source.read_text()
    converted = {}

    def replace(match):
        raw = match.group(1)
        if raw not in converted:
            with Image.open(BytesIO(base64.b64decode(raw))) as image:
                image.thumbnail((800, 800), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format='WEBP', quality=72, method=6)
            converted[raw] = 'data:image/webp;base64,' + base64.b64encode(output.getvalue()).decode()
        return converted[raw]

    html = re.sub(r'data:image/png;base64,([A-Za-z0-9+/=]+)', replace, html)
    # Preview images use WebP, including alpha on the warped overlay.
    html = html.replace("{type:'image/png'}", "{type:'image/webp'}")
    html = html.replace('`${sample.id}-${phase}.png`', '`${sample.id}-${phase}.webp`')
    html = html.replace('`${result.method}-${key}.png`', '`${result.method}-${key}.webp`')
    destination.write_text(html)
    print(f'{destination}: {destination.stat().st_size / 1024:.0f} KiB')


if __name__ == '__main__':
    root = Path(__file__).parent
    compact(root / 'outputs/overlap-demo.html', root / 'outputs/照片配對展示.html')
