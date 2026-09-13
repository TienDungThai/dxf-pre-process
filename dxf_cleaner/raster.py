import numpy as np
import cv2
from PIL import Image
from shapely.geometry import Polygon
from skimage.measure import find_contours


def load_binary(path: str, threshold: int | None, invert: bool) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Load an image and binarize it: 255 = kept material (ink), 0 = background.

    Composites transparency onto white first, so a transparent background
    never gets treated as "ink". Uses Otsu auto-threshold when `threshold`
    is None (matches the reference png2dxf.py behavior)."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im.convert("RGBA"))
    gray = np.array(im.convert("L"))
    if threshold is None:
        used_threshold, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        used_threshold = float(threshold)
        _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    if invert:
        mask = 255 - mask
    width_px, height_px = im.size
    return mask, float(used_threshold), (width_px, height_px)


def trace_mask(mask: np.ndarray, min_area_px: float, smooth_sigma: float) -> list[np.ndarray]:
    """Sub-pixel marching-squares contour extraction. Returns a flat list of
    (x, y) pixel-space rings, largest area first, with no exterior/hole
    classification -- that is the caller's job (dxf_cleaner.stages.hierarchy
    already does it for the resulting Contours)."""
    m = mask.astype(np.float32)
    if smooth_sigma > 0:
        m = cv2.GaussianBlur(m, (0, 0), smooth_sigma)
    m = np.pad(m, 1)  # avoid clipped shapes at the image edge

    rings: list[np.ndarray] = []
    for c in find_contours(m, 127.5):
        pts = np.array([(x - 1.0, y - 1.0) for y, x in c])  # (row, col) -> (x, y)
        if len(pts) < 4:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
        if poly.area < min_area_px:
            continue
        rings.append(np.array(poly.exterior.coords))

    rings.sort(key=lambda r: -Polygon(r).area)
    return rings
