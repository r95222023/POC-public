from io import BytesIO
from zipfile import ZipFile

from PIL import Image
import numpy as np
import pytest

from reconstruct_sparse import cyclic_pairs, point_quality_mask, prepare_images


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


def test_cyclic_pairs_link_neighbours_and_close_the_orbit():
    names = [f"f{i:02d}" for i in range(20)]
    pairs = cyclic_pairs(names, 3)
    as_index = {tuple(sorted((int(a[1:]), int(b[1:])))) for a, b in pairs}
    assert len(pairs) == len(as_index)
    assert {(0, 1), (0, 2), (0, 3), (0, 4), (0, 8), (0, 16)} <= as_index
    assert (0, 19) in as_index and (0, 17) in as_index  # wrap-around closes a full orbit
    assert (0, 5) not in as_index
    assert len(pairs) < len(names) * (len(names) - 1) // 2


def test_cyclic_pairs_never_pairs_an_image_with_itself():
    pairs = cyclic_pairs(["a", "b", "c"], 10)
    assert sorted(pairs) == [("a", "b"), ("a", "c"), ("b", "c")]
