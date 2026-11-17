import base64
import hashlib
from io import BytesIO
from pathlib import Path
import threading
import warnings
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import Flask, jsonify, render_template, request, send_file, url_for
from align import AlignmentError, MAX_SIDE, align, render
from samples import SAMPLES, available, image_for
from scene_routes import scenes

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.register_blueprint(scenes)
Image.MAX_IMAGE_PIXELS = 40_000_000
work_lock = threading.Lock()


def decode(upload):
    if upload is None:
        raise AlignmentError('請上傳 A 與 B 兩張圖片。')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(upload.stream) as image:
            image = ImageOps.exif_transpose(image)
            original = image.size
            if min(original) < 32:
                raise AlignmentError('圖片寬高至少需要 32 像素。')
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            # Flatten alpha on white rather than silently treating transparency as content.
            rgba = image.convert('RGBA')
            background = Image.new('RGBA', rgba.size, 'white')
            rgb = Image.alpha_composite(background, rgba).convert('RGB')
            array = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    return array, original


def encode(image):
    h, w = image.shape[:2]
    if max(h, w) > 1800:
        image = cv2.resize(image, (round(w * 1800 / max(h, w)), round(h * 1800 / max(h, w))))
    ok, buf = cv2.imencode('.png', image)
    if not ok:
        raise AlignmentError('無法產生預覽。')
    return 'data:image/png;base64,' + base64.b64encode(buf).decode()


@app.get('/')
def index():
    def asset(name):
        contents = (Path(app.static_folder) / name).read_bytes()
        return url_for('static', filename=name, v=hashlib.sha256(contents).hexdigest()[:12])
    response = app.make_response(render_template(
        'index.html', samples=sample_catalog(),
        stylesheet=asset('style.css'), javascript=asset('app.js')))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/health')
def health():
    return jsonify(status='ok', max_side=MAX_SIDE, version='gallery-2')


def sample_catalog():
    return [{
        'id': key, 'title': spec['title'], 'description': spec['description'],
        'available': available(spec), 'rotation_search': spec['rotation_search'],
        'a': url_for('sample_image', sample_id=key, phase='a'),
        'b': url_for('sample_image', sample_id=key, phase='b'),
    } for key, spec in SAMPLES.items()]


@app.get('/api/samples')
def sample_list():
    return jsonify(samples=sample_catalog())


@app.get('/api/samples/<sample_id>/<phase>.png')
def sample_image(sample_id, phase):
    spec = SAMPLES.get(sample_id)
    if spec is None or phase not in ('a', 'b'):
        return jsonify(error='找不到此範例。'), 404
    if not available(spec):
        return jsonify(error='範例尚未下載，請執行 .venv/bin/python download_samples.py。'), 404
    image = image_for(spec, phase)
    if image is None:
        return jsonify(error='範例圖片無法讀取，請重新下載。'), 404
    ok, buffer = cv2.imencode('.png', image)
    if not ok:
        return jsonify(error='無法產生範例圖片。'), 500
    return send_file(BytesIO(buffer.tobytes()), mimetype='image/png', max_age=3600)


@app.post('/api/align')
def register():
    mode = request.form.get('method', 'compare')
    if mode not in ('sift', 'lightglue', 'compare'):
        return jsonify(error='未知的特徵方法。'), 400
    if not work_lock.acquire(blocking=False):
        return jsonify(error='目前正在處理另一組圖片，請稍後再試。'), 429
    try:
        a, original_a = decode(request.files.get('a'))
        b, original_b = decode(request.files.get('b'))
        methods = ['sift', 'lightglue'] if mode == 'compare' else [mode]
        results = []
        sa = np.diag([a.shape[1] / original_a[0], a.shape[0] / original_a[1], 1.])
        sb = np.diag([b.shape[1] / original_b[0], b.shape[0] / original_b[1], 1.])
        for method in methods:
            try:
                candidates, info = align(a, b, method, request.form.get('rotation_search') == 'true')
                result = {**info, 'candidates': [c.summary() for c in candidates[:6]], 'images': {}}
                if candidates:
                    best = candidates[0]
                    result.update(status='reliable' if best.reliable else 'tentative',
                                  best=best.summary(),
                                  matrix_original=(np.linalg.inv(sa) @ best.matrix @ sb).tolist())
                    try:
                        result['images'] = {k: encode(v) for k, v in render(a, b, best).items()}
                    except AlignmentError as exc:
                        result['warning'] = str(exc)
                else:
                    result.update(status='no_match', warning='找不到足夠且幾何一致的對應點；影像可能無重疊或紋理不足。')
                results.append(result)
            except (ImportError, OSError, RuntimeError) as exc:
                app.logger.exception('Matcher failed: %s', method)
                results.append({'method': method, 'status': 'error',
                                'warning': '模型無法執行，請依 README 安裝依賴及預先下載權重。', 'images': {}})
        return jsonify(results=results, working_sizes={'a': list(a.shape[1::-1]), 'b': list(b.shape[1::-1])},
                       original_sizes={'a': list(original_a), 'b': list(original_b)},
                       note='像素誤差使用處理後尺寸；matrix_original 將 EXIF 校正後的原圖 B 座標映射到 A。分數是啟發式排序，不是成功機率。')
    except (AlignmentError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, OSError, ValueError) as exc:
        return jsonify(error=str(exc) if isinstance(exc, AlignmentError) else '圖片無法解碼、尺寸過大或格式不支援。'), 400
    finally:
        work_lock.release()


@app.errorhandler(413)
def too_large(_):
    return jsonify(error='兩張圖片的上傳總量需小於 32 MB。'), 413


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Overlap image alignment development server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
