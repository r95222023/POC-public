"""Reproducible paired-date evaluation, with known artificial perturbations."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from align import align, project, render, rotation_view
from download_samples import ROOT, NAMES


def perturb(image):
    h, w = image.shape[:2]
    transform = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), 27, .83), [0, 0, 1]])
    transform = np.array([[1., .03, 3], [.01, 1., -2], [.00012, -.00008, 1.]]) @ transform
    warped = cv2.warpPerspective(image, transform, (w, h))
    warped = np.clip(warped.astype(float) * np.array([.78, 1.08, .9]) + 16, 0, 255).astype(np.uint8)
    # Crop too, so the source only partially covers the reference.
    cropped = warped[16:h-12, 12:w-18]
    crop = np.array([[1., 0, -12], [0, 1., -16], [0, 0, 1.]])
    return cropped, np.linalg.inv(crop @ transform)


def grid_error(matrix, reference, shape):
    h, w = shape[:2]
    points = np.float32([[x, y] for y in np.linspace(h * .15, h * .85, 5)
                         for x in np.linspace(w * .15, w * .85, 5)])
    return float(np.median(np.linalg.norm(project(points, matrix) - project(points, reference), axis=1)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--methods', nargs='+', default=['sift', 'lightglue'], choices=['sift', 'lightglue'])
    args = parser.parse_args()
    out = Path(__file__).parent / 'outputs' / 'benchmark'
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, name in enumerate(NAMES):
        a, b = [cv2.imread(str(ROOT / phase / name)) for phase in ('A', 'B')]
        if a is None or b is None:
            raise RuntimeError('Missing data. Run download_samples.py first.')
        cases = [('paired_dates', b, np.eye(3), False)]
        if index < 6:
            modified, truth = perturb(b)
            cases.append(('rotated_scaled_perspective_color_crop', modified, truth, False))
        if index < 3:
            turned, turn = rotation_view(b, 1)
            cases.append(('quarter_turn_search', turned, np.linalg.inv(turn), True))
        if index < 6:
            same_date, same_truth = perturb(a)
            cases.append(('same_date_known_transform', same_date, same_truth, False))
        for case, image, reference, search in cases:
            for method in args.methods:
                cv2.setRNGSeed(42)
                candidates, info = align(a, image, method, search)
                row = {'sample': name, 'case': case, **info, 'found': bool(candidates)}
                if candidates:
                    best = candidates[0]
                    error = grid_error(best.matrix, reference, image.shape)
                    row.update(best.summary(), grid_error_px=error,
                               within_3px=bool(error <= 3), within_5px=bool(error <= 5))
                    if index in (0, 1, 9):
                        prefix = f'{Path(name).stem}-{case}-{method}'
                        cv2.imwrite(str(out / f'{prefix}-a.png'), a)
                        cv2.imwrite(str(out / f'{prefix}-b.png'), image)
                        for label, preview in render(a, image, best).items():
                            cv2.imwrite(str(out / f'{prefix}-{label}.png'), preview)
                rows.append(row)
                print(name, case, method, 'grid error', round(row.get('grid_error_px', -1), 3), flush=True)
    # A known unrelated pair and textureless pair test rejection rather than forcing a result.
    rng = np.random.default_rng(42)
    for case, a, b in [('unrelated_noise', rng.integers(0, 256, (256,256,3), np.uint8), rng.integers(0,256,(256,256,3), np.uint8)),
                       ('blank', np.zeros((256,256,3), np.uint8), np.full((256,256,3), 180, np.uint8))]:
        for method in args.methods:
            candidates, info = align(a,b,method)
            rows.append({'sample': case, 'case': 'negative_control', **info,
                         'found': bool(candidates), 'reliable': bool(candidates and candidates[0].reliable)})
    report = {'source': f'{ROOT.name}/sources.json', 'note': 'Paired dates are pre-registered, not independent camera poses. Identity is an approximate reference; residual source misregistration is possible. Grid error is independent of matched-point fit.', 'rows': rows}
    (out / 'results.json').write_text(json.dumps(report, indent=2))
    print('Saved', out / 'results.json')


if __name__ == '__main__':
    main()
