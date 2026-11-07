"""Local research examples, fetched separately rather than redistributed."""
import cv2
import numpy as np
from download_samples import ROOT

SAMPLES = {
    'rotation-scale': {
        'title': '旋轉・縮放・裁切',
        'description': '同一張照片加入 27° 旋轉、縮小、裁切與色調變化，先試這組。',
        'name': 'train_386_0512_0768.png', 'phase_b': 'A', 'variant': 'transform',
        'rotation_search': False,
    },
    'different-dates': {
        'title': '同地點・不同時間',
        'description': '比較不同時間的屋頂、地面與陰影，觀察 SIFT 和 CNN 的差異。',
        'name': 'train_386_0512_0768.png', 'phase_b': 'B', 'variant': None,
        'rotation_search': False,
    },
    'different-dates-90': {
        'title': '跨時間・轉向 90°',
        'description': '不同時間再加 90° 轉向，自動開啟四方向搜尋。',
        'name': 'test_113_0256.png', 'phase_b': 'B', 'variant': 'quarter-turn',
        'rotation_search': True,
    },
    'major-change': {
        'title': '挑戰・空地變建物',
        'description': '場景大幅改變，可能找不到對應；用來觀察匹配的限制。',
        'name': 'test_102_0512_0000.png', 'phase_b': 'B', 'variant': None,
        'rotation_search': False,
    },
}


def sample_path(spec, phase):
    return ROOT / ('A' if phase == 'a' else spec['phase_b']) / spec['name']


def available(spec):
    return all(sample_path(spec, phase).is_file() for phase in ('a', 'b'))


def image_for(spec, phase):
    image = cv2.imread(str(sample_path(spec, phase)))
    if image is None:
        return None
    if phase == 'b' and spec['variant'] == 'quarter-turn':
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif phase == 'b' and spec['variant'] == 'transform':
        h, w = image.shape[:2]
        transform = cv2.getRotationMatrix2D((w / 2, h / 2), 27, .83)
        image = cv2.warpAffine(image, transform, (w, h))[16:h-12, 12:w-18]
        image = np.clip(image.astype(float) * [.78, 1.08, .9] + 16, 0, 255).astype(np.uint8)
    return image
