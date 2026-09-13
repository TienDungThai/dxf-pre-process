# PNG → DXF Raster Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `dxfclean` accept a PNG/JPG file, trace it into contours, and run those contours through the existing DXF-cleaning stage pipeline (dedupe, despeckle, weld, hierarchy, simplify, validate) instead of a second, separate implementation.

**Architecture:** A new `dxf_cleaner/raster.py` module ports the already-validated tracing/measurement logic from the reference `png2dxf.py` script, and exposes `read_raster()` returning the same `ReadResult` shape `read_dxf()` returns. `run_pipeline` dispatches on file extension between the two readers; everything downstream of the read step is unchanged code. A new thin-feature check is added to `validate.py`. CLI gains raster-specific options and writes the `_KIEMTRA.png` preview image that shop operators are required to review.

**Tech Stack:** Python 3.11, ezdxf, shapely, numpy, click, pydantic (existing) + Pillow, opencv-python-headless, scikit-image, scipy (new, for raster tracing).

**Spec:** [docs/superpowers/specs/2026-09-13-png-to-dxf-design.md](../specs/2026-09-13-png-to-dxf-design.md)

## Global Constraints

- New dependencies: `Pillow`, `opencv-python-headless`, `scikit-image`, `scipy` — add to `pyproject.toml` `dependencies`, loosely pinned like existing entries (e.g. `opencv-python-headless>=4.9`).
- Raster contours are scaled to mm and closed line-only `Contour`s (`is_closed=True`, every `Segment.kind == "line"`) before entering the shared pipeline — no arcs, no unit rescaling downstream (`unit_scale=1.0` from `read_raster`).
- The `_KIEMTRA.png` preview image is written for every raster input, in `--check` mode too — it is the shop's mandatory pre-cut visual check, not a debug artifact.
- Reuse `build_hierarchy`, `simplify_contours`, `despeckle_contours`, `validate` unchanged — do not duplicate their logic in `raster.py`.
- File dispatch is by extension only: `.dxf` → existing `read_dxf`; `.png`/`.jpg`/`.jpeg` → new `read_raster`. No new CLI subcommand.

---

### Task 1: Add raster dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `Pillow`, `opencv-python-headless`, `scikit-image`, `scipy` importable as `PIL`, `cv2`, `skimage`, `scipy` in the active venv.

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

In the `dependencies = [...]` list (currently ending with `"click>=8.1",`), add:

```toml
    "Pillow>=10.0",
    "opencv-python-headless>=4.9",
    "scikit-image>=0.22",
    "scipy>=1.11",
```

- [ ] **Step 2: Install and verify**

Run: `./.venv/bin/pip install -e .`
Expected: install succeeds, no errors.

Run: `./.venv/bin/python -c "import cv2, PIL, skimage, scipy; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add raster tracing dependencies (Pillow, opencv, scikit-image, scipy)"
```

---

### Task 2: `RasterConfig`

**Files:**
- Modify: `dxf_cleaner/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `RasterConfig` (pydantic `BaseModel`) with fields `pixels_per_mm: float | None = None`, `threshold: int | None = None`, `invert: bool = False`, `min_area_px: float = 20.0`, `smooth_sigma: float = 1.0`, `prune_mm: float = 5.0`. `Config.raster: RasterConfig = RasterConfig()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_raster_config_defaults():
    from dxf_cleaner.config import Config
    config = Config()
    assert config.raster.pixels_per_mm is None
    assert config.raster.threshold is None
    assert config.raster.invert is False
    assert config.raster.min_area_px == 20.0
    assert config.raster.smooth_sigma == 1.0
    assert config.raster.prune_mm == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_config.py::test_raster_config_defaults -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'raster'`.

- [ ] **Step 3: Implement `RasterConfig`**

In `dxf_cleaner/config.py`, add after `class OutputConfig`:

```python
class RasterConfig(BaseModel):
    pixels_per_mm: float | None = None
    threshold: int | None = None
    invert: bool = False
    min_area_px: float = 20.0
    smooth_sigma: float = 1.0
    prune_mm: float = 5.0
```

In `class Config`, add the field (after `output: OutputConfig = OutputConfig()`):

```python
    raster: RasterConfig = RasterConfig()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_config.py::test_raster_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/config.py tests/test_config.py
git commit -m "feat: add RasterConfig for raster (PNG/JPG) input tuning"
```

---

### Task 3: `raster.load_binary` and `raster.trace_mask`

**Files:**
- Create: `dxf_cleaner/raster.py`
- Test: `tests/test_raster.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure image-processing functions).
- Produces:
  - `load_binary(path: str, threshold: int | None, invert: bool) -> tuple[np.ndarray, float, tuple[int, int]]` — returns `(mask, threshold_used, (width_px, height_px))`; `mask` is a `uint8` array of shape `(height_px, width_px)` with values `0` or `255`, `255` meaning "kept material".
  - `trace_mask(mask: np.ndarray, min_area_px: float, smooth_sigma: float) -> list[np.ndarray]` — returns a flat list of `(N, 2)` `float64` arrays, each an `(x, y)` pixel-space ring (no hole/exterior classification), sorted by descending area.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_raster.py`:

```python
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
    assert mask[10, 10] == 255
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_raster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.raster'`.

- [ ] **Step 3: Implement `load_binary` and `trace_mask`**

Create `dxf_cleaner/raster.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_raster.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/raster.py tests/test_raster.py
git commit -m "feat: add raster image loading and sub-pixel contour tracing"
```

---

### Task 4: `raster.measure_min_width_px`

**Files:**
- Modify: `dxf_cleaner/raster.py`
- Test: `tests/test_raster.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `measure_min_width_px(mask: np.ndarray, prune_iterations: int) -> tuple[float, int, np.ndarray, np.ndarray]` — returns `(min_width_px, n_parts, distance_transform, skeleton)`. `distance_transform` and `skeleton` are returned (not just the scalar) because Task 5's preview image needs them to mark thin regions without recomputing.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_raster.py`:

```python
def test_measure_min_width_detects_thin_bar(tmp_path):
    from dxf_cleaner.raster import load_binary, measure_min_width_px

    size = 200
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:80, 20:80] = 0     # thick block, 60px wide
    img[95:105, 20:180] = 0   # thin bar, 10px wide
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_raster.py -v -k measure`
Expected: FAIL with `ImportError: cannot import name 'measure_min_width_px'`.

- [ ] **Step 3: Implement `measure_min_width_px`**

Add to `dxf_cleaner/raster.py` (extend the imports at the top first):

```python
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize
```

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_raster.py -v -k measure`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/raster.py tests/test_raster.py
git commit -m "feat: add skeleton-based minimum feature width measurement"
```

---

### Task 5: `raster.render_preview`

**Files:**
- Modify: `dxf_cleaner/raster.py`
- Test: `tests/test_raster.py`

**Interfaces:**
- Consumes: `mask: np.ndarray` (Task 3), `rings: list[np.ndarray]` (Task 3), `dist: np.ndarray`, `skel: np.ndarray` (Task 4).
- Produces: `render_preview(mask: np.ndarray, rings: list[np.ndarray], holes_by_ring: list[bool], dist: np.ndarray, skel: np.ndarray, thin_threshold_px: float, out_path: str) -> None`. `holes_by_ring[i]` is `True` when `rings[i]` is a hole (drawn blue) rather than an outer boundary (drawn green).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_raster.py`:

```python
def test_render_preview_writes_valid_png_matching_mask_size(tmp_path):
    from dxf_cleaner.raster import (
        load_binary, trace_mask, measure_min_width_px, render_preview,
    )

    img = _square_with_hole_image()
    path = _save_png(tmp_path, "square.png", img)
    mask, _, _ = load_binary(path, threshold=None, invert=False)
    rings = trace_mask(mask, min_area_px=20.0, smooth_sigma=0.0)
    _, _, dist, skel = measure_min_width_px(mask, prune_iterations=3)

    out_path = tmp_path / "square_KIEMTRA.png"
    render_preview(
        mask, rings, holes_by_ring=[False, True],
        dist=dist, skel=skel, thin_threshold_px=5.0, out_path=str(out_path),
    )

    assert out_path.exists()
    preview = Image.open(out_path)
    assert preview.size == (mask.shape[1], mask.shape[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_raster.py -v -k render_preview`
Expected: FAIL with `ImportError: cannot import name 'render_preview'`.

- [ ] **Step 3: Implement `render_preview`**

Add to `dxf_cleaner/raster.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_raster.py -v -k render_preview`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/raster.py tests/test_raster.py
git commit -m "feat: add _KIEMTRA-style preview image rendering for raster input"
```

---

### Task 6: `ReadResult.raster_stats` field and `raster.read_raster`

**Files:**
- Modify: `dxf_cleaner/reader.py` (add field to `ReadResult`)
- Modify: `dxf_cleaner/raster.py`
- Test: `tests/test_raster.py`

**Interfaces:**
- Consumes: `Contour`, `Segment` (`dxf_cleaner.model`), `Diagnostic`, `ReadResult` (`dxf_cleaner.reader`), `load_binary`/`trace_mask`/`measure_min_width_px`/`render_preview` (Tasks 3-5), `RasterConfig` via `Config` (Task 2).
- Produces: `read_raster(path: str, config: Config, *, width_mm: float | None = None, height_mm: float | None = None, preview_path: str | None = None) -> ReadResult`. `ReadResult.raster_stats: dict[str, float] | None` (new field, default `None`) carries `{"width_mm", "height_mm", "dpi", "min_feature_width_mm", "n_parts", "threshold", "mm_per_px"}` when the result came from `read_raster`, else stays `None` for `read_dxf` results.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_raster.py`:

```python
def test_read_raster_produces_closed_line_contours_with_stats(tmp_path):
    from dxf_cleaner.config import Config
    from dxf_cleaner.raster import read_raster

    img = _square_with_hole_image(size=200, margin=20, hole_radius=30)
    path = _save_png(tmp_path, "square.png", img)

    result = read_raster(str(path), Config(), width_mm=160.0)

    assert result.unit_scale == 1.0
    assert result.flattened_handles == set()
    assert len(result.contours) == 2
    for contour in result.contours:
        assert contour.is_closed
        assert all(seg.kind == "line" for seg in contour.segments)

    stats = result.raster_stats
    assert stats is not None
    assert stats["width_mm"] == pytest.approx(160.0, rel=0.01)
    assert stats["n_parts"] == 1
    assert stats["min_feature_width_mm"] > 0


def test_read_raster_flags_low_dpi(tmp_path):
    from dxf_cleaner.config import Config
    from dxf_cleaner.raster import read_raster

    img = _square_with_hole_image(size=200, margin=20, hole_radius=30)
    path = _save_png(tmp_path, "square.png", img)

    # 200px stretched to 500mm is well under 300 DPI
    result = read_raster(str(path), Config(), width_mm=500.0)

    codes = [d.code for d in result.diagnostics]
    assert "RASTER_LOW_DPI" in codes


def test_read_raster_flags_possible_inversion(tmp_path):
    from dxf_cleaner.config import Config
    from dxf_cleaner.raster import read_raster

    # nearly-all-black image with a small white speck: looks inverted
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    img[90:110, 90:110] = 255
    path = _save_png(tmp_path, "suspect.png", img)

    result = read_raster(str(path), Config(), width_mm=100.0)

    codes = [d.code for d in result.diagnostics]
    assert "RASTER_POSSIBLE_INVERTED" in codes


def test_read_raster_writes_preview_when_path_given(tmp_path):
    from dxf_cleaner.config import Config
    from dxf_cleaner.raster import read_raster

    img = _square_with_hole_image()
    path = _save_png(tmp_path, "square.png", img)
    preview_path = tmp_path / "square_KIEMTRA.png"

    read_raster(str(path), Config(), width_mm=160.0, preview_path=str(preview_path))

    assert preview_path.exists()


def test_read_raster_and_hierarchy_together_yield_one_part_one_hole(tmp_path):
    from dxf_cleaner.config import Config
    from dxf_cleaner.raster import read_raster
    from dxf_cleaner.stages.hierarchy import build_hierarchy

    img = _square_with_hole_image()
    path = _save_png(tmp_path, "square.png", img)

    result = read_raster(str(path), Config(), width_mm=160.0)
    parts, diagnostics = build_hierarchy(result.contours)

    assert len(parts) == 1
    assert len(parts[0].interiors) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_raster.py -v -k read_raster`
Expected: FAIL with `ImportError: cannot import name 'read_raster'`.

- [ ] **Step 3: Add the `raster_stats` field to `ReadResult`**

In `dxf_cleaner/reader.py`, change:

```python
@dataclass
class ReadResult:
    contours: list[Contour]
    diagnostics: list[Diagnostic]
    unit_scale: float
    flattened_handles: set[str]
```

to:

```python
@dataclass
class ReadResult:
    contours: list[Contour]
    diagnostics: list[Diagnostic]
    unit_scale: float
    flattened_handles: set[str]
    raster_stats: dict[str, float] | None = None
```

(`read_dxf`'s existing `return ReadResult(...)` call keeps working unchanged since the new field has a default.)

- [ ] **Step 4: Implement `read_raster`**

Add to `dxf_cleaner/raster.py` (extend imports first: `from dxf_cleaner.config import Config`, `from dxf_cleaner.model import Contour, Segment, Diagnostic` — note `Diagnostic` already lives in `dxf_cleaner.model`, not `reader`; `from dxf_cleaner.reader import ReadResult`):

```python
from dxf_cleaner.config import Config
from dxf_cleaner.model import Contour, Segment, Diagnostic
from dxf_cleaner.reader import ReadResult


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
                                          "min_feature_width_mm": 0.0, "n_parts": 0,
                                          "threshold": threshold_used, "mm_per_px": 0.0})

    xs = np.concatenate([r[:, 0] for r in rings_px])
    ys = np.concatenate([r[:, 1] for r in rings_px])
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    span_w_px, span_h_px = x1 - x0, y1 - y0

    if width_mm is not None:
        mm_per_px = width_mm / span_w_px
    elif height_mm is not None:
        mm_per_px = height_mm / span_h_px
    elif raster_config.pixels_per_mm is not None:
        mm_per_px = 1.0 / raster_config.pixels_per_mm
    else:
        raise ValueError(
            "read_raster needs one of: width_mm, height_mm, or config.raster.pixels_per_mm"
        )

    dpi = span_w_px / (span_w_px * mm_per_px / 25.4)
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

    return ReadResult(contours=contours, diagnostics=diagnostics, unit_scale=1.0,
                       flattened_handles=set(), raster_stats=raster_stats)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_raster.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 6: Commit**

```bash
git add dxf_cleaner/raster.py dxf_cleaner/reader.py tests/test_raster.py
git commit -m "feat: add read_raster() producing ReadResult from PNG/JPG input"
```

---

### Task 7: Thin-feature check in `validate.py`

**Files:**
- Modify: `dxf_cleaner/stages/validate.py`
- Test: `tests/stages/test_validate.py` (check this file exists; if not, create it following the pattern of `tests/stages/` siblings)

**Interfaces:**
- Consumes: `validate(parts, diagnostics, config, stats)` (existing signature in `dxf_cleaner/stages/validate.py`).
- Produces: `validate()` now additionally reads `stats.get("min_feature_width_mm")`; if present and `< config.material_thickness` → appends to `critical`; if present and `< 2 * config.material_thickness` (but not already critical) → appends to `warnings`.

- [ ] **Step 1: Check existing validate test file**

Run: `ls tests/stages/`

If `test_validate.py` doesn't exist, create it with this header:

```python
from dxf_cleaner.config import ValidateConfig
from dxf_cleaner.stages.validate import validate


def _base_stats():
    return {"contour_count_before": 0, "contour_count_after": 0, "node_count_before": 0,
            "node_count_after": 0, "dedup_count": 0, "closed_count": 0, "weld_count": 0}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/stages/test_validate.py`:

```python
def test_min_feature_width_below_thickness_is_critical():
    stats = _base_stats()
    stats["min_feature_width_mm"] = 1.0
    config = ValidateConfig(material_thickness=2.0)

    report = validate(parts=[], diagnostics=[], config=config, stats=stats)

    assert report.level == "critical"
    assert any("1.0" in c or "1.00" in c for c in report.critical)


def test_min_feature_width_below_double_thickness_is_warning():
    stats = _base_stats()
    stats["min_feature_width_mm"] = 3.0
    config = ValidateConfig(material_thickness=2.0)

    report = validate(parts=[], diagnostics=[], config=config, stats=stats)

    assert report.level == "warning"
    assert any("3.0" in w or "3.00" in w for w in report.warnings)


def test_min_feature_width_above_double_thickness_is_ok():
    stats = _base_stats()
    stats["min_feature_width_mm"] = 5.0
    config = ValidateConfig(material_thickness=2.0)

    report = validate(parts=[], diagnostics=[], config=config, stats=stats)

    assert report.level == "ok"


def test_missing_min_feature_width_is_skipped():
    stats = _base_stats()  # no "min_feature_width_mm" key -- DXF input case
    config = ValidateConfig(material_thickness=2.0)

    report = validate(parts=[], diagnostics=[], config=config, stats=stats)

    assert report.level == "ok"
```

Note: `validate([], ..., stats)` with no parts currently appends `"Output is empty..."` to `critical` regardless — check that path. Since these tests need a clean `ok`/`warning`/`critical` read on *only* the thin-feature check, pass a single throwaway closed square `Part` instead of an empty list so the "Output is empty" critical doesn't interfere:

Replace `parts=[]` in all four tests above with a minimal square part:

```python
from dxf_cleaner.model import Contour, Segment, Part

def _square_part():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    segments = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % 4]) for i in range(4)]
    contour = Contour(segments=segments, is_closed=True, source_layer="CUT", source_handle="h0")
    return Part(exterior=contour)
```

and use `parts=[_square_part()]` in each of the four tests instead of `parts=[]`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/stages/test_validate.py -v -k min_feature_width`
Expected: FAIL (`critical`/`warnings` don't mention feature width — `report.level == "ok"` for all four instead of the expected differentiated levels).

- [ ] **Step 4: Implement the check**

In `dxf_cleaner/stages/validate.py`, inside `validate()`, after the existing hole-diameter loop (`for part_index, part in enumerate(parts): ...`) and before the duplicate-part-detection loop, add:

```python
    min_feature_width_mm = stats.get("min_feature_width_mm")
    if min_feature_width_mm is not None:
        thickness = config.material_thickness
        if min_feature_width_mm < thickness:
            critical.append(
                f"Thinnest feature {min_feature_width_mm:.2f}mm is narrower than the "
                f"material thickness {thickness:.2f}mm -- cannot cut"
            )
        elif min_feature_width_mm < 2 * thickness:
            warnings.append(
                f"Thinnest feature {min_feature_width_mm:.2f}mm is narrower than 2x the "
                f"material thickness ({2 * thickness:.2f}mm) -- risk of warping/burn"
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/stages/test_validate.py -v`
Expected: PASS (all tests in the file, including pre-existing ones if any)

- [ ] **Step 6: Commit**

```bash
git add dxf_cleaner/stages/validate.py tests/stages/test_validate.py
git commit -m "feat: validate minimum feature width against material thickness"
```

---

### Task 8: `run_pipeline` dispatch on file extension

**Files:**
- Modify: `dxf_cleaner/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `read_raster` (Task 6), `read_dxf` (existing), `ReadResult.raster_stats` (Task 6), `validate()` (Task 7).
- Produces: `run_pipeline(input_path: str, config: Config, *, width_mm: float | None = None, height_mm: float | None = None, preview_path: str | None = None) -> PipelineResult` — same return type as before, new keyword-only parameters default to `None` so every existing DXF-input call site is unaffected.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_run_pipeline_on_png_input_produces_one_part_with_hole(tmp_path):
    import numpy as np
    from PIL import Image
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    size = 200
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:180, 20:180] = 0
    yy, xx = np.mgrid[0:size, 0:size]
    hole = (xx - 100) ** 2 + (yy - 100) ** 2 <= 30 ** 2
    img[hole] = 255
    path = tmp_path / "square.png"
    Image.fromarray(img, mode="RGB").save(path)

    result = run_pipeline(str(path), Config(), width_mm=160.0)

    assert len(result.parts) == 1
    assert len(result.parts[0].interiors) == 1
    assert "min_feature_width_mm" in result.report.info


def test_run_pipeline_on_dxf_input_still_works_without_new_kwargs(tmp_path):
    import ezdxf
    from dxf_cleaner.pipeline import run_pipeline
    from dxf_cleaner.config import Config

    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    path = tmp_path / "square.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())

    assert len(result.parts) == 1
    assert "min_feature_width_mm" not in result.report.info
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_pipeline.py -v -k "png_input or dxf_input_still_works"`
Expected: FAIL — `run_pipeline()` doesn't accept `width_mm`, or PNG input raises since `read_dxf` is called unconditionally on the `.png` path (`ezdxf.readfile` will error on a non-DXF file).

- [ ] **Step 3: Implement the dispatch**

In `dxf_cleaner/pipeline.py`, add `from pathlib import Path` to the imports, add `from dxf_cleaner.raster import read_raster` next to the existing `from dxf_cleaner.reader import read_dxf`, then change:

```python
def run_pipeline(input_path: str, config: Config) -> PipelineResult:
    read_result = read_dxf(input_path, config)
```

to:

```python
_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg"}


def run_pipeline(
    input_path: str,
    config: Config,
    *,
    width_mm: float | None = None,
    height_mm: float | None = None,
    preview_path: str | None = None,
) -> PipelineResult:
    if Path(input_path).suffix.lower() in _RASTER_SUFFIXES:
        read_result = read_raster(
            input_path, config, width_mm=width_mm, height_mm=height_mm, preview_path=preview_path
        )
    else:
        read_result = read_dxf(input_path, config)
```

Then, further down in the same function, where `stats = dict(...)` is built (just before `report = validate(parts, diagnostics, config.validate, stats)`), merge in the raster stats:

```python
    stats = dict(
        contour_count_before=contour_count_before,
        contour_count_after=contour_count_after,
        node_count_before=node_count_before,
        node_count_after=node_count_after,
        dedup_count=dedup_count,
        closed_count=closed_count,
        weld_count=weld_count,
    )
    if read_result.raster_stats is not None:
        stats.update(read_result.raster_stats)
    report = validate(parts, diagnostics, config.validate, stats)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_pipeline.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/pipeline.py tests/test_pipeline.py
git commit -m "feat: dispatch run_pipeline to read_raster for PNG/JPG input"
```

---

### Task 9: CLI options and directory glob

**Files:**
- Modify: `dxf_cleaner/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `run_pipeline(input_path, config, *, width_mm=None, height_mm=None, preview_path=None)` (Task 8).
- Produces: new CLI flags `-w/--width-mm`, `-H/--height-mm` (mutually exclusive), `--px-per-mm`, `--invert`, `--raster-threshold`; directory mode also globs `*.png`/`*.jpg`/`*.jpeg`; every raster input gets a `<stem>_KIEMTRA.png` written next to the input file, in both normal and `--check` mode.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
import numpy as np
from PIL import Image


def _square_with_hole_png(tmp_path, name="square.png", size=200):
    img = np.full((size, size, 3), 255, dtype=np.uint8)
    img[20:180, 20:180] = 0
    yy, xx = np.mgrid[0:size, 0:size]
    hole = (xx - 100) ** 2 + (yy - 100) ** 2 <= 30 ** 2
    img[hole] = 255
    path = tmp_path / name
    Image.fromarray(img, mode="RGB").save(path)
    return path


def test_png_input_with_width_mm_writes_dxf_and_preview(tmp_path):
    input_path = _square_with_hole_png(tmp_path)
    output_path = tmp_path / "out.dxf"

    result = CliRunner().invoke(
        main, [str(input_path), "-o", str(output_path), "-w", "160"]
    )

    assert result.exit_code in (0, 1)  # ok or warning, not a system/critical failure
    assert output_path.exists()
    assert (tmp_path / "square_KIEMTRA.png").exists()


def test_png_input_check_mode_still_writes_preview_but_not_dxf(tmp_path):
    input_path = _square_with_hole_png(tmp_path)
    output_path = tmp_path / "out.dxf"

    result = CliRunner().invoke(
        main, [str(input_path), "-o", str(output_path), "-w", "160", "--check"]
    )

    assert result.exit_code in (0, 1)
    assert not output_path.exists()
    assert (tmp_path / "square_KIEMTRA.png").exists()


def test_width_and_height_mm_are_mutually_exclusive(tmp_path):
    input_path = _square_with_hole_png(tmp_path)

    result = CliRunner().invoke(
        main, [str(input_path), "-w", "160", "-H", "160"]
    )

    assert result.exit_code != 0


def test_directory_mode_picks_up_png_files(tmp_path):
    _square_with_hole_png(tmp_path, name="a.png")
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        main, [str(tmp_path), "-o", str(output_dir), "-w", "160"]
    )

    assert result.exit_code in (0, 1)
    assert (output_dir / "a.clean.dxf").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_cli.py -v -k "png_input or mutually_exclusive or directory_mode_picks_up_png"`
Expected: FAIL — `-w`/`-H` options don't exist yet (`click` reports "no such option"), and directory mode only globs `*.dxf`.

- [ ] **Step 3: Implement the CLI changes**

In `dxf_cleaner/cli.py`:

Add `_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg"}` near the top (after imports).

Change `_process_one`'s signature and body to thread the new parameters through and write the preview:

```python
def _process_one(
    input_path: Path,
    output_path: Path | None,
    config: Config,
    check: bool,
    width_mm: float | None = None,
    height_mm: float | None = None,
) -> int:
    """Run the pipeline on one file, print a summary, and return its exit code
    (0 ok, 1 warning, 2 critical) -- 3 (system error) is handled by the caller."""
    is_raster = input_path.suffix.lower() in _RASTER_SUFFIXES
    preview_path = str(input_path.with_name(f"{input_path.stem}_KIEMTRA.png")) if is_raster else None

    result = run_pipeline(
        str(input_path), config, width_mm=width_mm, height_mm=height_mm, preview_path=preview_path
    )
    report = result.report

    click.echo(f"{input_path}: {report.level}")
    for line in report.critical:
        click.secho(f"  CRITICAL: {line}", fg="red")
    for line in report.warnings:
        click.secho(f"  WARNING: {line}", fg="yellow")
    for key, value in sorted(report.info.items()):
        click.echo(f"  {key}: {value}")

    if not check and report.level != "critical":
        target = output_path if output_path is not None else input_path.with_name(
            f"{input_path.stem}.clean.dxf"
        )
        write_pipeline_result(result, str(target), config)
        click.echo(f"  -> wrote {target}")

    return {"ok": 0, "warning": 1, "critical": 2}[report.level]
```

Add the new options to the `main` click command (after `--no-simplify`):

```python
@click.option("-w", "--width-mm", type=float, default=None,
              help="Target width in mm (raster input only).")
@click.option("-H", "--height-mm", type=float, default=None,
              help="Target height in mm (raster input only, mutually exclusive with --width-mm).")
@click.option("--px-per-mm", type=float, default=None,
              help="Override raster.pixels_per_mm (raster input only).")
@click.option("--invert", is_flag=True, default=False, help="Invert black/white (raster input only).")
@click.option("--raster-threshold", type=int, default=None,
              help="Override raster.threshold, 0-255 (raster input only).")
```

Update `main`'s signature to accept these (`width_mm: float | None, height_mm: float | None, px_per_mm: float | None, invert: bool, raster_threshold: int | None`), and at the top of the function body:

```python
def main(input_path: Path, output_path: Path | None, config_path: Path | None, check: bool,
          snap_tol: float | None, weld_mode: str | None, no_simplify: bool,
          width_mm: float | None, height_mm: float | None, px_per_mm: float | None,
          invert: bool, raster_threshold: int | None) -> None:
    """Clean a DXF file, or a PNG/JPG raster image, (or a directory of either) for laser cutting."""
    if width_mm is not None and height_mm is not None:
        raise click.UsageError("--width-mm and --height-mm are mutually exclusive")

    config = load_config(str(config_path) if config_path else None)
    config = _apply_overrides(config, snap_tol, weld_mode, no_simplify)

    raster_data = config.raster.model_dump()
    if px_per_mm is not None:
        raster_data["pixels_per_mm"] = px_per_mm
    if invert:
        raster_data["invert"] = True
    if raster_threshold is not None:
        raster_data["threshold"] = raster_threshold
    config = config.model_copy(update={"raster": type(config.raster)(**raster_data)})
```

Update the directory-glob line:

```python
            dxf_files = sorted(input_path.glob("*.dxf"))
```

to:

```python
            dxf_files = sorted(
                f for f in input_path.iterdir()
                if f.suffix.lower() in {".dxf"} | _RASTER_SUFFIXES
            )
```

and both call sites of `_process_one` (single-file and directory-loop) to pass the new kwargs:

```python
            worst = max(worst, _process_one(f, target, config, check, width_mm, height_mm))
```

```python
            code = _process_one(input_path, output_path, config, check, width_mm, height_mm)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/cli.py tests/test_cli.py
git commit -m "feat: add PNG/JPG CLI options (-w/-H/--px-per-mm/--invert/--raster-threshold)"
```

---

### Task 10: Manual end-to-end smoke test

**Files:** none (verification only)

**Interfaces:** none new.

- [ ] **Step 1: Run the full test suite**

Run: `./.venv/bin/pytest -v`
Expected: PASS, all tests (existing DXF tests + new raster tests).

- [ ] **Step 2: Manual CLI run against a real image**

If a sample PNG is available (e.g. copy `/Users/dung_tt/Desktop/Dragoncorp/files/*.png` if one exists, or any simple black-line-art PNG), run:

```bash
./.venv/bin/dxfclean sample.png -o sample.dxf -w 200 -t 2
```

Expected: prints `ok`/`warning` level, `size_mm`/`dpi`/`min_feature_width_mm`/`n_parts` info lines, writes `sample.dxf` and `sample_KIEMTRA.png` next to the input. Open `sample_KIEMTRA.png` and visually confirm gray = kept metal, green = outer boundary, blue = hole boundary — matching the operating doc's expected color coding.

- [ ] **Step 3: Commit (only if step 2 required a fix)**

If step 2 surfaces a bug, fix it, re-run the full suite, then:

```bash
git add -A
git commit -m "fix: <describe the fix>"
```

---

## Self-Review Notes

- **Spec coverage:** 3.1 (Task 3-5), 3.2 (Task 6), 3.3 (Task 8, 9), 3.4 (Task 7), 3.5 (Task 2), 3.6 (Task 9), 3.7 (Task 1). Section 4 (error handling) needs no new code — verified `cli.py`'s existing top-level `except Exception` already covers raster read failures (Task 9 doesn't touch that `try/except`). Section 5 (testing) covered across Tasks 3-9; the synthetic-PNG-with-thin-bar scenario from the spec is folded into Tasks 4 (measurement) and 7 (validate) rather than one combined test, since each is independently testable at its own layer.
- **Placeholder scan:** no TBD/TODO; every step has runnable code.
- **Type consistency:** `ReadResult.raster_stats` (Task 6) is the single field both `pipeline.py` (Task 8, `stats.update(...)`) and `validate.py` (Task 7, `stats.get("min_feature_width_mm")`) read from — confirmed the key name matches exactly in both places. `read_raster`'s keyword args (`width_mm`, `height_mm`, `preview_path`) match `run_pipeline`'s (Task 8) which match `_process_one`'s (Task 9).
- **Known simplification flagged in Task 6:** `render_preview`'s hole/exterior coloring is not yet available at `read_raster` time (hierarchy classification happens later, in `build_hierarchy`). The plan draws every ring in the "exterior" (green) color for now. If exact green/blue hole coloring turns out to matter operationally, that requires either calling `build_hierarchy` earlier (inside `read_raster`, then re-flattening — architecturally messier) or moving preview rendering to after `build_hierarchy` in `pipeline.py`/`cli.py`. Flagging this now rather than silently under-delivering; worth a quick decision before or during Task 6 if the shop cares about seeing the blue/green distinction on day one.
