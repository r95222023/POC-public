"""Export a self-contained local research demo. Inputs/results are embedded.

This is not a deployment: uploaded custom images still require the Flask app.
LEVIR-CD research-use restrictions also apply to this generated, ignored file.
"""
import base64
from io import BytesIO
import json
from pathlib import Path
import cv2
from flask import render_template
from app import app, sample_catalog
from samples import SAMPLES, image_for


def main():
    root = Path(__file__).parent
    payloads = {}
    with app.test_request_context('/'):
        catalog = sample_catalog()
    client = app.test_client()
    for sample in catalog:
        if not sample['available']:
            raise RuntimeError('Run download_samples.py first.')
        uploads = {}
        for phase in ('a', 'b'):
            image = image_for(SAMPLES[sample['id']], phase)
            ok, data = cv2.imencode('.png', image)
            if not ok:
                raise RuntimeError('Cannot encode sample')
            raw = data.tobytes()
            sample[phase] = 'data:image/png;base64,' + base64.b64encode(raw).decode()
            uploads[phase] = (BytesIO(raw), f'{phase}.png')
        response = client.post('/api/align', data={
            **uploads, 'method': 'compare',
            'rotation_search': str(sample['rotation_search']).lower(),
        })
        if response.status_code != 200 or any(result['status'] == 'error' for result in response.json['results']):
            raise RuntimeError(f'Failed to calculate {sample["id"]}')
        payloads[sample['id']] = response.json
        print('Computed', sample['id'], flush=True)
    with app.test_request_context('/'):
        html = render_template('index.html', samples=catalog,
                               stylesheet='INLINE_CSS', javascript='INLINE_JS')
    css = (root / 'static/style.css').read_text()
    js = (root / 'static/app.js').read_text()
    config = json.dumps(payloads, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = html.replace('<link rel="stylesheet" href="INLINE_CSS">', f'<style>{css}</style>')
    html = html.replace('<script src="INLINE_JS"></script>',
                        f'<script>window.OVERLAP_DEMO={config};</script><script>{js}</script>')
    notice = ('<aside style="border:1px solid #52685d;border-radius:10px;padding:14px;margin-top:20px">'
              '<strong>離線範例展示</strong> · 照片與已計算的 SIFT／CNN 結果已包含在此檔案。'
              '選照片組，再按「尋找對應區域」即可比較。自行上傳新圖片需啟動 Python 網站版。'
              '範例影像限學術研究、禁止商用。</aside>')
    html = html.replace('</header>', '</header>' + notice, 1)
    destination = root / 'outputs/overlap-demo.html'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(html)
    print(f'Saved {destination} ({destination.stat().st_size / 1024 / 1024:.1f} MiB)')


if __name__ == '__main__':
    main()
