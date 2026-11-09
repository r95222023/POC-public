from io import BytesIO
from zipfile import ZipFile

from PIL import Image
import numpy as np
import pytest

from reconstruct_sparse import point_quality_mask, prepare_images


def photo(make="DJI", model="FC2204", size=(24, 18)):
    image = Image.new("RGB", size, "green")
    exif = Image.Exif()
    exif[271], exif[272] = make, model
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_preserves_bytes_and_separates_camera_intrinsics(tmp_path):
    original = photo()
    archive = tmp_path / "photos.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("first.jpg", original)
        zipped.writestr("second.jpg", original)
        zipped.writestr("third.jpg", photo("Parrot", "ANAFI", (32, 18)))
    destination = tmp_path / "images"
    records = prepare_images(archive, destination)
    assert len(records) == 3
    assert records[0]["camera_group"] == records[1]["camera_group"]
    assert records[0]["camera_group"] != records[2]["camera_group"]
    assert (destination / records[0]["image"]).read_bytes() == original


@pytest.mark.parametrize("name", ["../escape.jpg", "C:/escape.jpg", "..\\escape.jpg"])
def test_rejects_unsafe_archive_paths(tmp_path, name):
    archive = tmp_path / "photos.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(name, photo())
    with pytest.raises(ValueError, match="Unsafe archive entry"):
        prepare_images(archive, tmp_path / "images")
    assert not (tmp_path / "escape.jpg").exists()


def test_rejects_duplicate_filenames_in_same_camera_group(tmp_path):
    archive = tmp_path / "photos.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("a/first.jpg", photo())
        zipped.writestr("b/first.jpg", photo())
    with pytest.raises(ValueError, match="Duplicate image name"):
        prepare_images(archive, tmp_path / "images")


def test_quality_filter_preserves_supported_elevated_structure():
    rng = np.random.default_rng(5)
    ground = rng.normal(0, .05, (40, 3))
    roof = rng.normal(0, .05, (40, 3)) + [0, 0, 10]
    xyz = np.vstack([ground, roof, [100, 100, 100]])
    keep, stats = point_quality_mask(xyz, np.zeros(81), np.full(81, 5))
    assert keep[:80].all()
    assert not keep[80]
    assert stats["isolated_points"] == 1


def test_quality_filter_checks_observation_support_and_fit():
    keep, _ = point_quality_mask([[0, 0, 0], [1, 0, 0], [2, 0, 0]],
                                  [.1, .2, 2], [4, 2, 5])
    assert keep.tolist() == [True, False, False]
