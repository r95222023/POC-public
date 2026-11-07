"""Lazy, CPU-friendly official SuperPoint + LightGlue adapter."""
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def models():
    os.environ.setdefault('TORCH_HOME', str(Path(__file__).parent / '.cache' / 'torch'))
    import torch
    from lightglue import LightGlue, SuperPoint
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    return (SuperPoint(max_num_keypoints=1024).eval(),
            LightGlue(features='superpoint', flash=False).eval())


def match(a, b):
    import numpy as np
    import torch
    extractor, matcher = models()
    def tensor(im):
        return torch.from_numpy(np.ascontiguousarray(im[..., ::-1])).permute(2, 0, 1).float() / 255
    with torch.inference_mode():
        fa = extractor.extract(tensor(a), resize=None)
        fb = extractor.extract(tensor(b), resize=None)
        output = matcher({'image0': fb, 'image1': fa})
        pairs = output['matches'][0]
        src = fb['keypoints'][0][pairs[:, 0]].cpu().numpy()
        dst = fa['keypoints'][0][pairs[:, 1]].cpu().numpy()
    return src.astype('float32'), dst.astype('float32')


if __name__ == '__main__':
    models()
    print('SuperPoint + LightGlue weights ready.')
