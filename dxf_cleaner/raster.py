import numpy as np
import cv2
from PIL import Image
from shapely.geometry import Polygon
from skimage.measure import find_contours
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize

from dxf_cleaner.config import Config
from dxf_cleaner.model import Contour, Segment, Diagnostic
from dxf_cleaner.reader import ReadResult


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


def measure_min_width_px(mask: np.ndarray, prune_iterations: int) -> tuple[float, int, np.ndarray, np.ndarray]:
    """Minimum feature width via medial-axis distance transform. `prune_iterations`
    trims short skeleton spurs (sharp corners produce spurious thin branches)
    before taking the minimum distance-transform value along the skeleton."""
    kept = mask > 0
    dist = distance_transform_edt(kept)
    skel = skeletonize(kept)

    kernel = np.ones((3, 3), np.uint8)
    for _ in range(prune_iterations):
        neighbor_count = cv2.filter2D(skel.astype(np.uint8), -1, kernel, borderType=cv2.BORDER_CONSTANT)
        skel = skel & ~((neighbor_count <= 2) & skel)
    if not skel.any():
        skel = skeletonize(kept)

    widths = dist[skel] * 2.0
    n_parts = cv2.connectedComponents(mask)[0] - 1
    min_width_px = float(widths.min()) if widths.size else 0.0
    return min_width_px, n_parts, dist, skel


def render_preview(
    mask: np.ndarray,
    rings: list[np.ndarray],
    holes_by_ring: list[bool],
    dist: np.ndarray,
    skel: np.ndarray,
    thin_threshold_px: float,
    out_path: str,
) -> None:
    """Write the shop's required pre-cut check image: gray = kept material,
    white = background, green outline = outer boundary, blue outline = hole,
    red = feature thinner than `thin_threshold_px`."""
    preview = np.full((mask.shape[0], mask.shape[1], 3), 255, np.uint8)
    preview[mask > 0] = (205, 205, 205)

    thin_overlay = np.zeros(mask.shape, np.uint8)
    ys, xs = np.nonzero(skel)
    for y, x in zip(ys, xs):
        radius = dist[y, x]
        if radius < thin_threshold_px / 2.0:
            cv2.circle(thin_overlay, (int(x), int(y)), max(1, int(radius)), 255, -1)
    thin_overlay = cv2.bitwise_and(thin_overlay, mask)
    preview[thin_overlay > 0] = (60, 60, 230)  # BGR-ish order kept for cv2.imwrite below

    for ring, is_hole in zip(rings, holes_by_ring):
        color = (200, 120, 0) if is_hole else (0, 140, 0)
        cv2.polylines(preview, [ring.astype(np.int32)], True, color, 1)

    cv2.imwrite(out_path, preview)


def _ring_to_contour(ring_mm: np.ndarray, index: int) -> Contour:
    points = [(float(x), float(y)) for x, y in ring_mm]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    segments = [
        Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)])
        for i in range(len(points))
    ]
    return Contour(segments=segments, is_closed=True, source_layer="CUT",
                    source_handle=f"raster-{index}")


def read_raster(
    path: str,
    config: Config,
    *,
    width_mm: float | None = None,
    height_mm: float | None = None,
    preview_path: str | None = None,
) -> ReadResult:
    raster_config = config.raster
    mask, threshold_used, (width_px, height_px) = load_binary(
        path, raster_config.threshold, raster_config.invert
    )

    diagnostics: list[Diagnostic] = []

    fill_ratio = float((mask > 0).mean())
    border = np.concatenate([mask[0], mask[-1], mask[:, 0], mask[:, -1]])
    border_ratio = float((border > 0).mean())
    if fill_ratio > 0.80 or border_ratio > 0.90:
        diagnostics.append(Diagnostic(
            code="RASTER_POSSIBLE_INVERTED",
            message=(
                f"Kept-pixel fill ratio {fill_ratio:.2f} / border ratio {border_ratio:.2f} "
                f"look inverted; re-run with invert=True if the preview looks wrong"
            ),
        ))

    rings_px = trace_mask(mask, raster_config.min_area_px, raster_config.smooth_sigma)
    if not rings_px:
        return ReadResult(contours=[], diagnostics=diagnostics, unit_scale=1.0,
                           flattened_handles=set(),
                           raster_stats={"width_mm": 0.0, "height_mm": 0.0, "dpi": 0.0,
                                          "n_parts": 0,
                                          "threshold": threshold_used, "mm_per_px": 0.0})

    xs = np.concatenate([r[:, 0] for r in rings_px])
    ys = np.concatenate([r[:, 1] for r in rings_px])
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    span_w_px, span_h_px = x1 - x0, y1 - y0

    if width_mm is not None:
        if span_w_px == 0:
            raise ValueError(
                "Traced image has zero width/height -- check min_area_px and the input image"
            )
        mm_per_px = width_mm / span_w_px
    elif height_mm is not None:
        if span_h_px == 0:
            raise ValueError(
                "Traced image has zero width/height -- check min_area_px and the input image"
            )
        mm_per_px = height_mm / span_h_px
    elif raster_config.pixels_per_mm is not None:
        mm_per_px = 1.0 / raster_config.pixels_per_mm
    else:
        raise ValueError(
            "read_raster needs one of: width_mm, height_mm, or config.raster.pixels_per_mm"
        )

    dpi = 25.4 / mm_per_px
    if dpi < 300:
        diagnostics.append(Diagnostic(
            code="RASTER_LOW_DPI",
            message=f"Effective resolution {dpi:.0f} DPI is below 300; edges may deviate "
                    f"by roughly {mm_per_px / 2:.3f}mm",
        ))

    contours: list[Contour] = []
    for i, ring_px in enumerate(rings_px):
        ring_mm = np.column_stack([
            (ring_px[:, 0] - x0) * mm_per_px,
            (y1 - ring_px[:, 1]) * mm_per_px,  # flip Y so DXF Y grows upward
        ])
        contours.append(_ring_to_contour(ring_mm, i))

    prune_iterations = int(max(3, min(120, round(raster_config.prune_mm / mm_per_px))))
    min_width_px, n_parts, dist, skel = measure_min_width_px(mask, prune_iterations)
    min_feature_width_mm = min_width_px * mm_per_px

    if preview_path is not None:
        holes_by_ring = [False] * len(rings_px)  # exterior/hole not yet known here;
        # hierarchy classification happens downstream in build_hierarchy, so the
        # preview draws every ring the same color pending that. See Task 9 note.
        render_preview(
            mask, rings_px, holes_by_ring, dist, skel,
            thin_threshold_px=2 * config.validate.material_thickness / mm_per_px,
            out_path=preview_path,
        )

    raster_stats = {
        "width_mm": span_w_px * mm_per_px,
        "height_mm": span_h_px * mm_per_px,
        "dpi": dpi,
        "min_feature_width_mm": min_feature_width_mm,
        "n_parts": n_parts,
        "threshold": threshold_used,
        "mm_per_px": mm_per_px,
    }

    flattened_handles = {contour.source_handle for contour in contours}
    return ReadResult(contours=contours, diagnostics=diagnostics, unit_scale=1.0,
                       flattened_handles=flattened_handles, raster_stats=raster_stats)
