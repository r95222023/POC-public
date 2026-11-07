from io import BytesIO
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
import pytest
from PIL import Image
from align import AlignmentError, align, estimate, evaluate, project, render, rotation_view
from app import app


def texture():
    rng = np.random.default_rng(7)
    im = np.full((480, 640, 3), 100, np.uint8)
    for _ in range(500):
        center = tuple(map(int, rng.integers([12, 12], [628, 468])))
        color = tuple(map(int, rng.integers(0, 256, 3)))
        cv2.circle(im, center, int(rng.integers(2, 12)), color, -1)
    cv2.putText(im, 'OVERLAP 123', (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 3)
    return im


@pytest.mark.parametrize('kind', ['similarity', 'affine', 'homography'])
def test_geometry_with_outliers(kind):
    rng = np.random.default_rng(33)
    src = rng.uniform([20, 20], [600, 450], (120, 2)).astype(np.float32)
    matrices = {'similarity': np.array([[.9,-.2,40],[.2,.9,5],[0,0,1.]]),
                'affine': np.array([[.9,.12,30],[.04,.8,10],[0,0,1.]]),
                'homography': np.array([[.9,.12,30],[.04,.8,10],[.0004,-.0002,1.]])}
    truth = matrices[kind]
    dst = project(src, truth) + rng.normal(0, .15, src.shape).astype(np.float32)
    dst[:30] = rng.uniform([0,0], [640,480], (30,2))
    candidates = estimate(src, dst, (480,640,3), (480,640,3))
    best = max(candidates, key=lambda c: (c.reliable,c.score))
    assert best.reliable
    assert np.median(np.linalg.norm(project(src[30:], best.matrix)-project(src[30:],truth),axis=1)) < .5


def test_sift_rotation_scale_light_crop():
    a = texture()
    truth = np.vstack([cv2.getRotationMatrix2D((320,240), 31, .8), [0,0,1]])
    b = cv2.warpPerspective(a, truth, (640,480))[30:450,40:600]
    b = np.clip(b.astype(float)*.7+25,0,255).astype(np.uint8)
    truth = np.array([[1.,0,-40],[0,1.,-30],[0,0,1.]]) @ truth
    candidates, _ = align(a,b)
    assert candidates and candidates[0].reliable
    p = np.float32([[100,100],[300,100],[300,300],[100,300]])
    assert np.max(np.linalg.norm(project(p,candidates[0].matrix)-project(p,np.linalg.inv(truth)),axis=1)) < 2


@pytest.mark.parametrize('k', range(4))
def test_rotation_coordinate_mapping(k):
    im = np.zeros((40,60,3),np.uint8);im[8,17]=255
    turned, mat = rotation_view(im,k)
    x,y = np.round(project([[17,8]],mat)[0]).astype(int)
    assert np.all(turned[y,x]==255)


def test_blank_abstains():
    image=np.zeros((200,200,3),np.uint8)
    candidates, _ = align(image,image)
    assert not candidates


def test_projective_horizon_rejected():
    points=np.float32([[10,10],[90,10],[90,90],[10,90],[50,20],[20,50]])
    mat=np.array([[1.,0,0],[0,1,0],[.02,0,-1]])
    assert evaluate('homography',mat,points,points,(100,100,3),(100,100,3)) is None


def test_render_and_black_pixels():
    a=texture(); a[50:100,50:100]=0
    candidates,_=align(a,a)
    images=render(a,a,candidates[0])
    assert np.all(images['warped'][70,70,:3]==0)
    assert images['warped'][70,70,3]==255
    assert images['blend'].shape[2]==3


def png(image):
    buf=BytesIO();Image.fromarray(image).save(buf,format='PNG');buf.seek(0);return buf


def test_api_valid_upload():
    a=texture()
    response=app.test_client().post('/api/align',data={'method':'sift','a':(png(a),'a.png'),'b':(png(a),'b.png')})
    assert response.status_code==200
    result=response.json['results'][0]
    assert result['status']=='reliable'
    assert result['images']['warped'].startswith('data:image/png;base64,')
    assert np.allclose(result['matrix_original'],np.eye(3),atol=.1)


@pytest.mark.parametrize('data',[{'method':'invalid'},{'method':'sift'}, {'method':'sift','a':(BytesIO(b'not an image'),'a.png'),'b':(BytesIO(b'bad'),'b.png')}])
def test_api_bad_input(data):
    response=app.test_client().post('/api/align',data=data)
    assert response.status_code==400
    assert 'error' in response.json
