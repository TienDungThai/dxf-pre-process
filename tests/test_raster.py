import numpy as np
from PIL import Image
import pytest


def _save_png(tmp_path, name, array_uint8_rgb):
    path = tmp_path / name
    Image.fromarray(array_uint8_rgb, mode="RGB").save(path)
    return str(path)


def _square_with_hole_image(size=200, margin=20, hole_radius=30):
    """White background, black filled square with a white circular hole
    in the middle -- black is 'ink' (the shape to keep)."""
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[margin:size - margin, margin:size - margin] = 0
    yy, xx = np.mgrid[0:size, 0:size]
    center = size // 2
    hole = (xx - center) ** 2 + (yy - center) ** 2 <= hole_radius ** 2
    img[hole] = 255
    return img


def test_load_binary_thresholds_black_ink_as_kept(tmp_path):
    from dxf_cleaner.raster import load_binary

    img = _square_with_hole_image()
    path = _save_png(tmp_path, "square.png", img)

    mask, threshold, (w, h) = load_binary(path, threshold=None, invert=False)

    assert (w, h) == (200, 200)
    assert mask.shape == (200, 200)
    # center of the square (away from the hole) should be kept (255)
    assert mask[50, 50] == 255
    # center of the hole should be background (0)
    assert mask[100, 100] == 0


def test_load_binary_invert_flips_kept_region(tmp_path):
    from dxf_cleaner.raster import load_binary

    img = _square_with_hole_image()
    path = _save_png(tmp_path, "square.png", img)

    normal, _, _ = load_binary(path, threshold=None, invert=False)
    inverted, _, _ = load_binary(path, threshold=None, invert=True)

    assert inverted[10, 10] != normal[10, 10]


def test_trace_mask_finds_outer_ring_and_hole_ring(tmp_path):
    from dxf_cleaner.raster import load_binary, trace_mask

    img = _square_with_hole_image()
    path = _save_png(tmp_path, "square.png", img)
    mask, _, _ = load_binary(path, threshold=None, invert=False)

    rings = trace_mask(mask, min_area_px=20.0, smooth_sigma=0.0)

    assert len(rings) == 2
    areas = sorted(
        [0.5 * abs(np.dot(r[:, 0], np.roll(r[:, 1], -1)) - np.dot(r[:, 1], np.roll(r[:, 0], -1)))
         for r in rings]
    )
    # hole ring is smaller than the outer ring
    assert areas[0] < areas[1]


def test_trace_mask_drops_specks_below_min_area(tmp_path):
    from dxf_cleaner.raster import load_binary, trace_mask

    img = np.full((100, 100, 3), 255, dtype=np.uint8)
    img[40:60, 40:60] = 0     # 20x20 real shape
    img[5:7, 5:7] = 0         # 2x2 speck
    path = _save_png(tmp_path, "speck.png", img)
    mask, _, _ = load_binary(path, threshold=None, invert=False)

    rings = trace_mask(mask, min_area_px=50.0, smooth_sigma=0.0)

    assert len(rings) == 1


def test_measure_min_width_detects_thin_bar(tmp_path):
    from dxf_cleaner.raster import load_binary, measure_min_width_px

    size = 200
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:80, 20:80] = 0     # thick block, 60px wide
    img[75:85, 20:180] = 0    # thin bar, 10px wide (overlaps to connect blocks)
    img[20:80, 120:180] = 0   # another thick block
    path = _save_png(tmp_path, "dumbbell.png", img)
    mask, _, _ = load_binary(path, threshold=None, invert=False)

    min_width_px, n_parts, dist, skel = measure_min_width_px(mask, prune_iterations=3)

    assert 8.0 <= min_width_px <= 12.0
    assert n_parts == 1  # bar connects both blocks into one component
    assert dist.shape == mask.shape
    assert skel.shape == mask.shape


def test_measure_min_width_counts_disconnected_parts(tmp_path):
    from dxf_cleaner.raster import load_binary, measure_min_width_px

    img = np.full((100, 100, 3), 255, dtype=np.uint8)
    img[10:30, 10:30] = 0
    img[60:80, 60:80] = 0
    path = _save_png(tmp_path, "two_blocks.png", img)
    mask, _, _ = load_binary(path, threshold=None, invert=False)

    _, n_parts, _, _ = measure_min_width_px(mask, prune_iterations=3)

    assert n_parts == 2
