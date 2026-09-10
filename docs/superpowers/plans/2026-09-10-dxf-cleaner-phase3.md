# DXF Cleaner — Phase 3 (Weld & Optimize: weld, simplify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two remaining cleanup stages from the spec's core pipeline — `weld.py` (merge genuinely-overlapping closed shapes into one boundary, then re-detect arcs on the welded boundary so unary_union's polyline output doesn't permanently flatten every rounded corner and circular hole) and `simplify.py` (reduce node count on contours that were actually transformed this run, with a hard area-preservation safety check) — and wire both into `pipeline.py` in spec order (weld → hierarchy → simplify), replacing the Phase 2 placeholder `weld_count=0`.

**Architecture:** `weld.py` operates on the flat `list[Contour]` before hierarchy runs (welding changes contour boundaries and counts, so hierarchy must see the post-weld set, exactly like Phase 2's snap/dedupe/despeckle do). It clusters closed contours by genuine polygon overlap (not mere touching) via `STRtree`, `unary_union`s each cluster with 2+ members, leaves singleton clusters as their original untouched `Contour` (preserving native arcs), and for every welded cluster re-runs circular/arc-fit detection over the union's boundary before emitting new `Segment`s. `simplify.py` runs after `hierarchy.py` (per spec order 6.6→6.7→6.8) and only touches contours whose `source_handle` is in a `touched_handles` set assembled by `pipeline.py` from what `weld.py` produced and what `reader.py`/`flatten.py` produced from SPLINE/ELLIPSE entities — never contours read verbatim from LINE/ARC/CIRCLE/LWPOLYLINE. It applies shapely's Douglas-Peucker (`preserve_topology=True`), then a collinear-segment merge pass, then reverts to the pre-simplify contour (with a diagnostic) if the resulting area drifts more than the configured percentage.

**Tech Stack:** Python 3.11+, ezdxf, shapely 2.x (`shapely.ops.unary_union`, `shapely.make_valid`, `STRtree` with `predicate="intersects"`), numpy, pydantic + PyYAML, pytest.

**Spec:** [docs/superpowers/specs/2026-09-09-dxf-cleaner-design.md](../specs/2026-09-09-dxf-cleaner-design.md) — sections 6.6 (`weld.py`), 6.7 (`hierarchy.py`, already built in Phase 2, unchanged), 6.8 (`simplify.py`).

## Global Constraints

- Builds on Phase 1 + Phase 2's `dxf_cleaner/model.py`, `config.py`, `reader.py`, `writer.py`, `stages/{explode,flatten,snap,dedupe,despeckle,hierarchy,validate}.py`, `pipeline.py`. Do not modify any existing stage's *behavior* — only extend `model.py` (new shared helpers, additive), `reader.py` (add one field to `ReadResult`, additive), `flatten.py` (replace two private helpers with imports of the now-shared versions in `model.py`, byte-for-byte same logic so Phase 1/2 tests keep passing unmodified), `pipeline.py` (insert two stages + real `weld_count`), and `validate.py` (nothing — `weld_count`/`node_count_after` already flow through `stats` unchanged, no code change needed there).
- Every stage function returns `(result, list[Diagnostic])` — never raises for data-quality problems. Every geometry change produces a `Diagnostic` with an actionable `code`.
- New config sections, exact defaults from the user's settled design (not literally in spec section 8's YAML sample, which predates this phase, but consistent with its style): `weld` (`mode: Literal["off","overlapping","all"] = "overlapping"`), `simplify` (`enabled: bool = True`, `tolerance: float = 0.01`, `collinear_angle_deg: float = 0.1`, `max_area_deviation_pct: float = 0.1`).
- **Documented scope decisions:**
  1. "Genuinely intersecting" (spec: "thực sự giao nhau") is implemented as a *positive-area* intersection, not merely touching. Two polygons that only share a boundary edge (zero-area intersection) are left as independent, unwelded contours — welding them would still discard their native arcs for zero benefit, since `unary_union` has nothing to add for two shapes that already don't overlap.
  2. Mode `"all"` welds every closed contour into one `unary_union`, per spec 6.6 ("tương đương Weld toàn bộ trong Corel"), regardless of overlap. Its output may still be a `MultiPolygon` (e.g. two shapes on opposite corners of the sheet) — each resulting `Polygon` in the union becomes its own welded `Contour`.
  3. Arc re-detection on a welded boundary (spec 6.6, last paragraph) needs a *segment-wise* scan (some stretches of the new boundary are arcs, others are straight), which is a different algorithm from `flatten.py`'s `_try_fit_circle` (which classifies one whole point list as a single circle or gives up). This plan extracts `flatten.py`'s two private helpers (`_fit_circle_3pt`, `_signed_area_sign`) into `model.py` as public functions so `weld.py` can reuse the exact same 3-point-fit primitive inside its own windowed scan, and updates `flatten.py` to import them (no behavior change — verified by Phase 1's existing `test_flatten.py` passing unmodified).
  4. `simplify.py`'s "contour was transformed" test uses two independent signals threaded through `pipeline.py`: (a) `ReadResult.flattened_handles` — handles of contours that came from `flatten_entity` (SPLINE/ELLIPSE), since even an *unwelded* flattened curve was already discretized and re-fit, unlike a native LINE/ARC/CIRCLE/LWPOLYLINE; (b) `weld.py`'s own returned `welded_handles` — synthetic handles it assigns to every contour it produced via `unary_union`. A contour whose handle is in neither set is left completely alone by `simplify.py`, per spec 6.8's "không áp dụng cho contour giữ nguyên từ file gốc".
  5. The area-preservation check (spec 6.8) compares the *discretized* area of the pre- and post-simplify contour (via `contour_signed_area`, `abs()` of both), since a Douglas-Peucker or collinear-merge pass never re-introduces the true arc geometry that isn't already present in the tolerance-window; comparing discretized areas at the same `arc_tolerance` on both sides keeps the check apples-to-apples.

---

## File Structure

```
dxf_cleaner/
├── model.py                # MODIFY: add fit_circle_3pt(), signed_area_sign() (Task 1)
├── config.py                # MODIFY: add WeldConfig/SimplifyConfig (Task 1)
├── reader.py                 # MODIFY: ReadResult gains flattened_handles: set[str] (Task 4)
├── pipeline.py                # MODIFY: insert weld + simplify stages, real weld_count (Task 4)
└── stages/
    ├── flatten.py              # MODIFY: import fit_circle_3pt/signed_area_sign from model.py instead of local copies (Task 1)
    ├── weld.py                  # NEW: weld_contours (Task 2)
    └── simplify.py               # NEW: simplify_contours (Task 3)

tests/
├── test_config.py            # MODIFY: append WeldConfig/SimplifyConfig tests (Task 1)
├── test_model.py               # MODIFY: append fit_circle_3pt/signed_area_sign tests (Task 1)
├── test_reader.py               # MODIFY: append flattened_handles test (Task 4)
├── test_pipeline.py               # MODIFY: append weld+simplify integration tests (Task 4)
└── stages/
    ├── test_weld.py                # NEW (Task 2)
    └── test_simplify.py             # NEW (Task 3)
```

---

### Task 1: `model.py` shared arc-fit helpers + `config.py` sections

**Files:**
- Modify: `dxf_cleaner/model.py`
- Modify: `dxf_cleaner/stages/flatten.py`
- Modify: `dxf_cleaner/config.py`
- Test: `tests/test_model.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new (pure refactor + config additions).
- Produces: `model.fit_circle_3pt(p1: Point, p2: Point, p3: Point) -> tuple[Point, float] | None`, `model.signed_area_sign(points: list[Point], center: Point) -> int`, `config.WeldConfig(mode: Literal["off","overlapping","all"] = "overlapping")`, `config.SimplifyConfig(enabled: bool = True, tolerance: float = 0.01, collinear_angle_deg: float = 0.1, max_area_deviation_pct: float = 0.1)`, both added as `Config.weld` / `Config.simplify` fields. Task 2 and Task 3 depend on all of these.

- [ ] **Step 1: Write failing tests for the two new `model.py` functions**

Append to `tests/test_model.py`:

```python
from dxf_cleaner.model import fit_circle_3pt, signed_area_sign


def test_fit_circle_3pt_returns_center_and_radius():
    center, radius = fit_circle_3pt((10, 0), (0, 10), (-10, 0))
    assert math.dist(center, (0, 0)) < 1e-9
    assert abs(radius - 10) < 1e-9


def test_fit_circle_3pt_returns_none_for_collinear_points():
    assert fit_circle_3pt((0, 0), (1, 0), (2, 0)) is None


def test_signed_area_sign_ccw_positive():
    points = [(10, 0), (0, 10), (-10, 0), (0, -10), (10, 0)]
    assert signed_area_sign(points, (0, 0)) == 1


def test_signed_area_sign_cw_negative():
    points = [(10, 0), (0, -10), (-10, 0), (0, 10), (10, 0)]
    assert signed_area_sign(points, (0, 0)) == -1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_model.py -k "fit_circle_3pt or signed_area_sign" -v`
Expected: FAIL with `ImportError: cannot import name 'fit_circle_3pt'`

- [ ] **Step 3: Move the two helpers into `model.py`, verbatim logic**

Append to `dxf_cleaner/model.py` (after `contour_as_full_circle`):

```python
def fit_circle_3pt(p1: Point, p2: Point, p3: Point) -> tuple[Point, float] | None:
    """Circumcircle through 3 points. Returns None if (near-)collinear."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax ** 2 + ay ** 2) * (by - cy) + (bx ** 2 + by ** 2) * (cy - ay) + (cx ** 2 + cy ** 2) * (ay - by)) / d
    uy = ((ax ** 2 + ay ** 2) * (cx - bx) + (bx ** 2 + by ** 2) * (ax - cx) + (cx ** 2 + cy ** 2) * (bx - ax)) / d
    radius = math.dist((ux, uy), p1)
    if radius < 1e-9:
        return None
    return (ux, uy), radius


def signed_area_sign(points: list[Point], center: Point) -> int:
    """Angular direction of `points` *about `center`* (see fit_circle_3pt usage in
    flatten.py / weld.py: reconstructing an arc needs winding about its own center,
    not the coordinate origin, or it comes back as its mirror image across the chord)."""
    cx, cy = center
    total = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        total += (x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)
    return 1 if total >= 0 else -1
```

Note: `model.py` already has `import math` at the top from Phase 1 — no new import needed.

- [ ] **Step 4: Update `flatten.py` to use the shared helpers**

In `dxf_cleaner/stages/flatten.py`, delete the local `_fit_circle_3pt` and `_signed_area_sign` function bodies entirely and replace the top of the file:

```python
import math
from dxf_cleaner.model import Segment, Contour, Point, fit_circle_3pt, signed_area_sign
```

Then replace every call site `_fit_circle_3pt(...)` with `fit_circle_3pt(...)` and `_signed_area_sign(...)` with `signed_area_sign(...)` in `_try_fit_circle`.

- [ ] **Step 5: Run full existing test suite to verify no regression**

Run: `pytest tests/test_model.py tests/stages/test_flatten.py -v`
Expected: PASS (all Phase 1 `test_flatten.py` tests pass unmodified, plus the 4 new `test_model.py` tests)

- [ ] **Step 6: Write failing tests for the two new config sections**

Append to `tests/test_config.py`:

```python
def test_weld_config_defaults():
    cfg = Config()
    assert cfg.weld.mode == "overlapping"


def test_simplify_config_defaults():
    cfg = Config()
    assert cfg.simplify.enabled is True
    assert cfg.simplify.tolerance == 0.01
    assert cfg.simplify.collinear_angle_deg == 0.1
    assert cfg.simplify.max_area_deviation_pct == 0.1


def test_weld_mode_rejects_invalid_value():
    with pytest.raises(ValidationError):
        Config(weld={"mode": "bogus"})
```

Make sure `tests/test_config.py`'s imports include `pytest` and `from pydantic import ValidationError` (add if not already present).

- [ ] **Step 7: Run to verify failure**

Run: `pytest tests/test_config.py -k "weld_config or simplify_config or weld_mode" -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'weld'`

- [ ] **Step 8: Add `WeldConfig` and `SimplifyConfig` to `config.py`**

In `dxf_cleaner/config.py`, add after `DespeckleConfig`:

```python
class WeldConfig(BaseModel):
    mode: Literal["off", "overlapping", "all"] = "overlapping"


class SimplifyConfig(BaseModel):
    enabled: bool = True
    tolerance: float = 0.01
    collinear_angle_deg: float = 0.1
    max_area_deviation_pct: float = 0.1
```

Add both as fields on `Config`, right after `despeckle` and before `validate` (matches spec's stage order: despeckle → weld → hierarchy → simplify → validate; `validate` stays last in the class body too, cosmetic but keeps the file readable in pipeline order):

```python
class Config(BaseModel):
    input: InputConfig = InputConfig()
    flatten: FlattenConfig = FlattenConfig()
    snap: SnapConfig = SnapConfig()
    dedupe: DedupeConfig = DedupeConfig()
    despeckle: DespeckleConfig = DespeckleConfig()
    weld: WeldConfig = WeldConfig()
    simplify: SimplifyConfig = SimplifyConfig()
    validate: ValidateConfig = ValidateConfig()
    output: OutputConfig = OutputConfig()
```

- [ ] **Step 9: Run to verify passing**

Run: `pytest tests/test_config.py -v`
Expected: PASS (all Phase 1/2 config tests plus the 3 new ones)

- [ ] **Step 10: Commit**

```bash
git add dxf_cleaner/model.py dxf_cleaner/stages/flatten.py dxf_cleaner/config.py tests/test_model.py tests/test_config.py
git commit -m "feat: share arc-fit helpers via model.py, add weld/simplify config sections"
```

---

### Task 2: `stages/weld.py` — merge overlapping closed contours, re-detect arcs

**Files:**
- Create: `dxf_cleaner/stages/weld.py`
- Test: `tests/stages/test_weld.py`

**Interfaces:**
- Consumes: `Contour`, `Segment`, `Diagnostic`, `fit_circle_3pt`, `signed_area_sign` from `dxf_cleaner.model` (Task 1); `shapely.ops.unary_union`, `shapely.make_valid`, `shapely.STRtree`, `shapely.geometry.Polygon`.
- Produces: `weld_contours(contours: list[Contour], mode: Literal["off", "overlapping", "all"], arc_tolerance: float = 0.02) -> tuple[list[Contour], list[Diagnostic], set[str]]` — the third return value, `welded_handles`, is the set of `source_handle`s belonging to contours this call actually produced via `unary_union` (used by Task 4's `pipeline.py` to scope `simplify.py`). Task 4 (`pipeline.py`) and Task 3 (`simplify.py`, indirectly via `pipeline.py`) depend on this exact 3-tuple signature.

- [ ] **Step 1: Write failing tests for the "off" and "no-overlap" pass-through cases**

Create `tests/stages/test_weld.py`:

```python
import math
import pytest
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.weld import weld_contours


def _square(x0, y0, size, layer="0", handle="H1"):
    x1, y1 = x0 + size, y0 + size
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    segs = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer=layer, source_handle=handle)


def test_mode_off_returns_contours_unchanged():
    squares = [_square(0, 0, 10, handle="A"), _square(5, 5, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="off")
    assert result == squares
    assert diags == []
    assert welded == set()


def test_non_overlapping_squares_untouched_in_overlapping_mode():
    squares = [_square(0, 0, 10, handle="A"), _square(100, 100, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="overlapping")
    assert len(result) == 2
    assert {c.source_handle for c in result} == {"A", "B"}
    assert welded == set()


def test_overlapping_squares_are_merged_into_one_contour():
    squares = [_square(0, 0, 10, handle="A"), _square(5, 0, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="overlapping")
    assert len(result) == 1
    merged = result[0]
    assert merged.is_closed
    assert merged.source_handle in welded
    # union of two overlapping 10x10 squares offset by 5 has area 150
    from dxf_cleaner.model import contour_signed_area
    assert abs(abs(contour_signed_area(merged)) - 150.0) < 0.5


def test_touching_but_not_overlapping_squares_stay_independent():
    # share only an edge (zero-area intersection) -> not "genuinely overlapping"
    squares = [_square(0, 0, 10, handle="A"), _square(10, 0, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="overlapping")
    assert len(result) == 2
    assert welded == set()


def test_mode_all_welds_even_non_overlapping_shapes():
    squares = [_square(0, 0, 10, handle="A"), _square(100, 100, 10, handle="B")]
    result, diags, welded = weld_contours(squares, mode="all")
    assert len(result) == 2  # MultiPolygon -> one Contour per disjoint piece
    assert all(c.source_handle in welded for c in result)


def test_open_contours_pass_through_untouched():
    open_line = Contour(
        segments=[Segment(kind="line", start=(0, 0), end=(1, 1))],
        is_closed=False, source_layer="0", source_handle="OPEN",
    )
    result, diags, welded = weld_contours([open_line], mode="overlapping")
    assert result == [open_line]
    assert welded == set()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/stages/test_weld.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.weld'`

- [ ] **Step 3: Implement `weld.py`**

Create `dxf_cleaner/stages/weld.py`:

```python
import math
from typing import Literal

from shapely import make_valid
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from dxf_cleaner.model import Segment, Contour, Diagnostic, Point, fit_circle_3pt, signed_area_sign

_MIN_OVERLAP_AREA = 1e-6
_MIN_ARC_WINDOW = 5  # fewest consecutive boundary points that may form one arc


def _to_valid_polygon(contour: Contour, arc_tolerance: float, diagnostics: list[Diagnostic]) -> Polygon | None:
    poly = Polygon(contour.to_shapely(arc_tolerance))
    if poly.is_valid:
        return poly
    fixed = make_valid(poly)
    diagnostics.append(Diagnostic(
        code="INVALID_POLYGON_FIXED",
        message="Repaired self-intersecting polygon before weld",
        handle=contour.source_handle,
    ))
    if fixed.geom_type != "Polygon":
        candidates = [g for g in getattr(fixed, "geoms", []) if g.geom_type == "Polygon"]
        if not candidates:
            diagnostics.append(Diagnostic(
                code="POLYGON_UNRECOVERABLE",
                message="Could not recover a usable polygon for weld",
                handle=contour.source_handle,
            ))
            return None
        fixed = max(candidates, key=lambda g: g.area)
    return fixed


def _cluster_by_overlap(polygons: list[Polygon]) -> list[list[int]]:
    """Union-find over pairs whose intersection has positive area."""
    n = len(polygons)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    tree = STRtree(polygons)
    for i, poly in enumerate(polygons):
        for j in tree.query(poly, predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            if poly.intersection(polygons[j]).area > _MIN_OVERLAP_AREA:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def _ring_points(ring) -> list[Point]:
    coords = list(ring.coords)
    if math.dist(coords[0], coords[-1]) < 1e-9:
        coords = coords[:-1]
    return [(x, y) for x, y in coords]


def _detect_arcs_in_ring(points: list[Point], tolerance: float) -> list[Segment]:
    """Segment-wise scan: greedily grow an arc window from each start point as far
    as it still fits a single circle within `tolerance`; fall back to a line
    segment for any stretch too short or too irregular to be an arc."""
    n = len(points)
    if n < 3:
        return [Segment(kind="line", start=points[i], end=points[(i + 1) % n]) for i in range(n)]

    segments: list[Segment] = []
    i = 0
    while i < n:
        best_end = None
        best_fit = None
        j = i + _MIN_ARC_WINDOW - 1
        while j < i + n:  # allow wraparound across the closing point
            window = [points[k % n] for k in range(i, j + 1)]
            fit = fit_circle_3pt(window[0], window[len(window) // 2], window[-1])
            if fit is None:
                break
            center, radius = fit
            if all(abs(math.dist(p, center) - radius) <= tolerance for p in window):
                best_end = j
                best_fit = (center, radius)
                j += 1
            else:
                break
        if best_end is not None:
            center, radius = best_fit
            window = [points[k % n] for k in range(i, best_end + 1)]
            ccw = signed_area_sign(window, center) > 0
            segments.append(Segment(
                kind="arc", start=points[i % n], end=points[best_end % n],
                center=center, radius=radius, ccw=ccw,
            ))
            i = best_end
        else:
            segments.append(Segment(kind="line", start=points[i % n], end=points[(i + 1) % n]))
            i += 1
        if i >= n:
            break
    return segments


def _polygon_to_contours(poly: Polygon, arc_tolerance: float, handle: str, layer: str) -> list[Contour]:
    out: list[Contour] = []
    for ring, is_exterior in [(poly.exterior, True)] + [(r, False) for r in poly.interiors]:
        points = _ring_points(ring)
        segments = _detect_arcs_in_ring(points, arc_tolerance)
        ring_handle = handle if is_exterior else f"{handle}_hole{len(out)}"
        out.append(Contour(segments=segments, is_closed=True, source_layer=layer, source_handle=ring_handle))
    return out


def weld_contours(
    contours: list[Contour], mode: Literal["off", "overlapping", "all"], arc_tolerance: float = 0.02
) -> tuple[list[Contour], list[Diagnostic], set[str]]:
    if mode == "off":
        return contours, [], set()

    diagnostics: list[Diagnostic] = []
    open_contours = [c for c in contours if not c.is_closed]
    closed_contours = [c for c in contours if c.is_closed]

    polygons: list[Polygon] = []
    valid_closed: list[Contour] = []
    for c in closed_contours:
        poly = _to_valid_polygon(c, arc_tolerance, diagnostics)
        if poly is None:
            continue
        polygons.append(poly)
        valid_closed.append(c)

    if not polygons:
        return list(open_contours), diagnostics, set()

    if mode == "all":
        clusters = [list(range(len(polygons)))]
    else:
        clusters = _cluster_by_overlap(polygons)

    result: list[Contour] = list(open_contours)
    welded_handles: set[str] = set()
    for cluster in clusters:
        if len(cluster) < 2:
            result.append(valid_closed[cluster[0]])
            continue
        cluster_polys = [polygons[i] for i in cluster]
        union_geom = unary_union(cluster_polys)
        pieces = list(union_geom.geoms) if isinstance(union_geom, MultiPolygon) else [union_geom]
        base_handle = "_".join(sorted(valid_closed[i].source_handle for i in cluster))
        layer = valid_closed[cluster[0]].source_layer
        for idx, piece in enumerate(pieces):
            piece_handle = f"WELD_{base_handle}" if len(pieces) == 1 else f"WELD_{base_handle}_{idx}"
            new_contours = _polygon_to_contours(piece, arc_tolerance, piece_handle, layer)
            for nc in new_contours:
                welded_handles.add(nc.source_handle)
            result.extend(new_contours)
        diagnostics.append(Diagnostic(
            code="CONTOURS_WELDED",
            message=f"Merged {len(cluster)} overlapping contours into {len(pieces)} welded contour(s)",
            handle=base_handle,
        ))

    return result, diagnostics, welded_handles
```

- [ ] **Step 4: Run to verify passing**

Run: `pytest tests/stages/test_weld.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/weld.py tests/stages/test_weld.py
git commit -m "feat: add weld.py — merge overlapping contours, re-detect arcs on welded boundary"
```

---

### Task 3: `stages/simplify.py` — node reduction with area-preservation guard

**Files:**
- Create: `dxf_cleaner/stages/simplify.py`
- Test: `tests/stages/test_simplify.py`

**Interfaces:**
- Consumes: `Contour`, `Segment`, `Diagnostic`, `contour_signed_area` from `dxf_cleaner.model`; `Contour.to_shapely()`.
- Produces: `simplify_contours(contours: list[Contour], touched_handles: set[str], tolerance: float, collinear_angle_deg: float, max_area_deviation_pct: float, arc_tolerance: float = 0.02) -> tuple[list[Contour], list[Diagnostic]]`. Task 4 (`pipeline.py`) depends on this exact signature and calls it with `touched_handles = flattened_handles | welded_handles`.

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_simplify.py`:

```python
import math
from dxf_cleaner.model import Segment, Contour, contour_signed_area
from dxf_cleaner.stages.simplify import simplify_contours


def _dense_square(size=10, handle="A", subdivisions=20):
    """A square whose 4 edges are each subdivided into many collinear segments —
    the kind of over-noded boundary weld/flatten produce."""
    corners = [(0, 0), (size, 0), (size, size), (0, size)]
    points: list[tuple[float, float]] = []
    for i in range(4):
        p0, p1 = corners[i], corners[(i + 1) % 4]
        for k in range(subdivisions):
            t = k / subdivisions
            points.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
    segs = [Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)]) for i in range(len(points))]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


def test_untouched_contour_is_left_alone():
    dense = _dense_square(handle="A")
    result, diags = simplify_contours([dense], touched_handles=set(), tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert result == [dense]
    assert diags == []


def test_touched_contour_gets_node_count_reduced():
    dense = _dense_square(handle="A")
    before_count = len(dense.segments)
    result, diags = simplify_contours([dense], touched_handles={"A"}, tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert len(result) == 1
    assert len(result[0].segments) < before_count
    assert len(result[0].segments) == 4  # collinear merge collapses each edge back to one segment


def test_area_preserved_within_tolerance():
    dense = _dense_square(handle="A", size=10)
    before_area = abs(contour_signed_area(dense))
    result, diags = simplify_contours([dense], touched_handles={"A"}, tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    after_area = abs(contour_signed_area(result[0]))
    assert abs(after_area - before_area) / before_area < 0.001


def test_reverts_when_area_deviation_exceeds_threshold():
    dense = _dense_square(handle="A")
    # tolerance so large Douglas-Peucker would cut a real corner off -> big area change
    result, diags = simplify_contours([dense], touched_handles={"A"}, tolerance=5.0,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert result == [dense]
    assert any(d.code == "SIMPLIFY_REVERTED_AREA_DEVIATION" for d in diags)


def test_arc_segments_in_touched_contour_are_preserved_as_arcs():
    seg1 = Segment(kind="arc", start=(10, 0), end=(-10, 0), center=(0, 0), radius=10, ccw=True)
    seg2 = Segment(kind="arc", start=(-10, 0), end=(10, 0), center=(0, 0), radius=10, ccw=True)
    circle = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="A")
    result, diags = simplify_contours([circle], touched_handles={"A"}, tolerance=0.01,
                                       collinear_angle_deg=0.1, max_area_deviation_pct=0.1)
    assert len(result) == 1
    assert all(s.kind == "arc" for s in result[0].segments)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/stages/test_simplify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.simplify'`

- [ ] **Step 3: Implement `simplify.py`**

Create `dxf_cleaner/stages/simplify.py`:

```python
import math

from dxf_cleaner.model import Segment, Contour, Diagnostic, Point, contour_signed_area


def _angle_of(seg: Segment) -> float:
    return math.atan2(seg.end[1] - seg.start[1], seg.end[0] - seg.start[0])


def _merge_collinear_lines(segments: list[Segment], collinear_angle_deg: float, is_closed: bool) -> list[Segment]:
    """Merge consecutive `line` segments whose direction differs by less than
    `collinear_angle_deg`. Arc segments are never merged and act as boundaries."""
    if not segments:
        return segments
    tol_rad = math.radians(collinear_angle_deg)
    merged: list[Segment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if prev.kind == "line" and seg.kind == "line" and math.dist(prev.end, seg.start) < 1e-9:
            angle_diff = abs((_angle_of(prev) - _angle_of(seg) + math.pi) % (2 * math.pi) - math.pi)
            if angle_diff < tol_rad:
                merged[-1] = Segment(kind="line", start=prev.start, end=seg.end)
                continue
        merged.append(seg)
    # wraparound merge for closed contours: first and last segment may now be collinear too
    if is_closed and len(merged) > 1:
        first, last = merged[0], merged[-1]
        if first.kind == "line" and last.kind == "line" and math.dist(last.end, first.start) < 1e-9:
            angle_diff = abs((_angle_of(last) - _angle_of(first) + math.pi) % (2 * math.pi) - math.pi)
            if angle_diff < tol_rad:
                merged[0] = Segment(kind="line", start=last.start, end=first.end)
                merged.pop()
    return merged


def _points_to_line_segments(points: list[Point], is_closed: bool) -> list[Segment]:
    count = len(points) if is_closed else len(points) - 1
    return [Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)]) for i in range(count)]


def _simplify_one(contour: Contour, tolerance: float, collinear_angle_deg: float,
                   max_area_deviation_pct: float, arc_tolerance: float) -> tuple[Contour, Diagnostic | None]:
    has_arcs = any(s.kind == "arc" for s in contour.segments)
    before_area = abs(contour_signed_area(contour, arc_tolerance))

    if has_arcs:
        # Arc segments already carry minimal, exact geometry -- only the
        # collinear-line-merge pass applies; Douglas-Peucker would discretize
        # and destroy them.
        new_segments = _merge_collinear_lines(contour.segments, collinear_angle_deg, contour.is_closed)
        candidate = Contour(segments=new_segments, is_closed=contour.is_closed,
                             source_layer=contour.source_layer, source_handle=contour.source_handle)
    else:
        shape = contour.to_shapely(arc_tolerance)
        simplified = shape.simplify(tolerance, preserve_topology=True)
        points = [(x, y) for x, y in simplified.coords]
        if contour.is_closed and len(points) > 1 and math.dist(points[0], points[-1]) < 1e-9:
            points = points[:-1]
        new_segments = _points_to_line_segments(points, contour.is_closed)
        new_segments = _merge_collinear_lines(new_segments, collinear_angle_deg, contour.is_closed)
        candidate = Contour(segments=new_segments, is_closed=contour.is_closed,
                             source_layer=contour.source_layer, source_handle=contour.source_handle)

    if len(candidate.segments) < 1:
        return contour, None

    after_area = abs(contour_signed_area(candidate, arc_tolerance))
    if before_area > 1e-9:
        deviation_pct = abs(after_area - before_area) / before_area * 100
        if deviation_pct > max_area_deviation_pct:
            return contour, Diagnostic(
                code="SIMPLIFY_REVERTED_AREA_DEVIATION",
                message=f"Simplify reverted: area deviated {deviation_pct:.3f}% "
                        f"(limit {max_area_deviation_pct}%)",
                handle=contour.source_handle,
            )
    return candidate, None


def simplify_contours(
    contours: list[Contour], touched_handles: set[str], tolerance: float,
    collinear_angle_deg: float, max_area_deviation_pct: float, arc_tolerance: float = 0.02,
) -> tuple[list[Contour], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    result: list[Contour] = []
    for contour in contours:
        if contour.source_handle not in touched_handles:
            result.append(contour)
            continue
        simplified, revert_diag = _simplify_one(
            contour, tolerance, collinear_angle_deg, max_area_deviation_pct, arc_tolerance
        )
        result.append(simplified)
        if revert_diag is not None:
            diagnostics.append(revert_diag)
    return result, diagnostics
```

- [ ] **Step 4: Run to verify passing**

Run: `pytest tests/stages/test_simplify.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/simplify.py tests/stages/test_simplify.py
git commit -m "feat: add simplify.py — node reduction with area-preservation guard"
```

---

### Task 4: Wire `weld` + `simplify` into the pipeline; track flatten-derived handles

**Files:**
- Modify: `dxf_cleaner/reader.py`
- Modify: `dxf_cleaner/pipeline.py`
- Test: `tests/test_reader.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `weld_contours` (Task 2), `simplify_contours` (Task 3), `Config.weld`/`Config.simplify` (Task 1).
- Produces: `ReadResult.flattened_handles: set[str]`; `run_pipeline` now runs `... → despeckle → weld → hierarchy → simplify → validate` and populates `stats["weld_count"]` for real.

- [ ] **Step 1: Write failing test for `flattened_handles`**

Append to `tests/test_reader.py` (check the file's existing imports/fixtures first; it already builds small in-memory `ezdxf` documents for Phase 1 tests — follow that pattern):

```python
def test_flattened_handles_tracks_spline_and_ellipse_entities():
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 0))  # LINE: not flattened
    ellipse = msp.add_ellipse((0, 0), major_axis=(5, 0), ratio=0.5)  # ELLIPSE: flattened
    doc.header["$INSUNITS"] = 4
    path = tmp_path_dxf(doc)  # use whatever existing helper this test file uses to save+reload a doc
    result = read_dxf(path, Config())
    ellipse_handle = ellipse.dxf.handle
    assert ellipse_handle in result.flattened_handles
    line_handles = {c.source_handle for c in result.contours} - result.flattened_handles
    assert len(line_handles) == 1
```

If `tests/test_reader.py` has no existing "save doc to a temp path and read it back" helper, inspect how its other tests construct a `ReadResult` (it must already do this for Phase 1's LINE/ARC/CIRCLE/SPLINE tests) and reuse that exact helper name instead of `tmp_path_dxf` — match the file's existing convention rather than introducing a new one.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_reader.py -k flattened_handles -v`
Expected: FAIL with `AttributeError: 'ReadResult' object has no attribute 'flattened_handles'`

- [ ] **Step 3: Add `flattened_handles` to `reader.py`**

In `dxf_cleaner/reader.py`, modify the `ReadResult` dataclass:

```python
@dataclass
class ReadResult:
    contours: list[Contour]
    diagnostics: list[Diagnostic]
    unit_scale: float
    flattened_handles: set[str]
```

In `read_dxf`, track handles of entities routed through `flatten_entity` (dxftype in `{"SPLINE", "ELLIPSE"}`). Modify the loop body:

```python
    contours: list[Contour] = []
    flattened_handles: set[str] = set()
    for entity in kept_entities:
        handle = getattr(entity.dxf, "handle", None)
        try:
            contour, diag = _convert_entity(entity, config)
        except Exception as exc:  # one malformed entity must not abort the file
            diagnostics.append(Diagnostic(
                code="ENTITY_CONVERSION_FAILED",
                message=f"{entity.dxftype()} entity could not be converted: {exc}",
                handle=handle,
            ))
            continue
        if diag is not None:
            diagnostics.append(diag)
        if contour is None:
            continue
        if not contour.segments:
            diagnostics.append(Diagnostic(
                code="DEGENERATE_ENTITY_SKIPPED",
                message=f"{entity.dxftype()} entity produced no geometry; skipped",
                handle=handle,
            ))
            continue
        if entity.dxftype() in {"SPLINE", "ELLIPSE"}:
            flattened_handles.add(contour.source_handle)
        contours.append(scale_contour(contour, unit_scale))

    return ReadResult(contours=contours, diagnostics=diagnostics, unit_scale=unit_scale,
                       flattened_handles=flattened_handles)
```

- [ ] **Step 4: Run to verify passing**

Run: `pytest tests/test_reader.py -v`
Expected: PASS (all Phase 1 reader tests plus the new one)

- [ ] **Step 5: Write failing integration tests for the pipeline**

Append to `tests/test_pipeline.py` (match its existing style — it already builds small `ezdxf` docs and calls `run_pipeline`):

```python
def test_pipeline_welds_overlapping_squares_and_reports_weld_count():
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    # two overlapping 10x10 squares as closed LWPOLYLINEs
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    msp.add_lwpolyline([(5, 0), (15, 0), (15, 10), (5, 10)], close=True)
    doc.header["$INSUNITS"] = 4
    path = save_and_get_path(doc)  # reuse this test file's existing doc->path helper
    config = Config()
    result = run_pipeline(path, config)
    assert len(result.parts) == 1
    assert result.report.info["weld_count"] == 1


def test_pipeline_simplify_reduces_node_count_only_on_touched_contours():
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True)  # untouched, 4 nodes already
    ellipse = msp.add_ellipse((50, 50), major_axis=(5, 0), ratio=1.0)  # circle-shaped ellipse, gets flattened+simplified
    doc.header["$INSUNITS"] = 4
    path = save_and_get_path(doc)
    config = Config()
    result = run_pipeline(path, config)
    untouched = next(p for p in result.parts if len(p.exterior.segments) == 4)
    assert untouched is not None


def test_weld_mode_off_disables_welding():
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    msp.add_lwpolyline([(5, 0), (15, 0), (15, 10), (5, 10)], close=True)
    doc.header["$INSUNITS"] = 4
    path = save_and_get_path(doc)
    config = Config()
    config.weld.mode = "off"
    result = run_pipeline(path, config)
    assert len(result.parts) == 2
    assert result.report.info["weld_count"] == 0
```

- [ ] **Step 6: Run to verify failure**

Run: `pytest tests/test_pipeline.py -k "weld or simplify" -v`
Expected: FAIL (weld/simplify not yet wired into `run_pipeline`, so squares stay separate and `weld_count` stays 0)

- [ ] **Step 7: Wire both stages into `pipeline.py`**

Replace the body of `dxf_cleaner/pipeline.py` from the imports through `run_pipeline`:

```python
from dataclasses import dataclass

from dxf_cleaner.config import Config
from dxf_cleaner.model import Part, Contour, Diagnostic
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.writer import write_dxf
from dxf_cleaner.stages.snap import snap_and_chain
from dxf_cleaner.stages.dedupe import dedupe_contours
from dxf_cleaner.stages.despeckle import despeckle_contours
from dxf_cleaner.stages.weld import weld_contours
from dxf_cleaner.stages.hierarchy import build_hierarchy
from dxf_cleaner.stages.simplify import simplify_contours
from dxf_cleaner.stages.validate import validate, ValidationReport


@dataclass
class PipelineResult:
    parts: list[Part]
    diagnostics: list[Diagnostic]
    report: ValidationReport


def parts_to_contours(parts: list[Part]) -> list[Contour]:
    contours: list[Contour] = []
    for part in parts:
        contours.append(part.exterior)
        contours.extend(part.interiors)
    return contours


def run_pipeline(input_path: str, config: Config) -> PipelineResult:
    read_result = read_dxf(input_path, config)
    diagnostics: list[Diagnostic] = list(read_result.diagnostics)

    contour_count_before = len(read_result.contours)
    node_count_before = sum(len(c.segments) for c in read_result.contours)

    contours, snap_diags = snap_and_chain(
        read_result.contours, config.snap.tolerance, config.snap.max_reportable_gap
    )
    diagnostics.extend(snap_diags)
    closed_count = sum(1 for c in contours if c.is_closed)

    dedup_count = 0
    if config.dedupe.enabled:
        contours, dedupe_diags = dedupe_contours(contours, config.dedupe.merge_common_edges)
        diagnostics.extend(dedupe_diags)
        dedup_count = sum(
            1 for d in dedupe_diags if d.code in ("DUPLICATE_SEGMENT_REMOVED", "OVERLAPPING_SEGMENTS_MERGED")
        )

    contours, despeckle_diags = despeckle_contours(
        contours, config.despeckle.min_perimeter, config.despeckle.min_area
    )
    diagnostics.extend(despeckle_diags)

    contours, weld_diags, welded_handles = weld_contours(contours, config.weld.mode)
    diagnostics.extend(weld_diags)
    weld_count = sum(1 for d in weld_diags if d.code == "CONTOURS_WELDED")

    parts, hierarchy_diags = build_hierarchy(contours)
    diagnostics.extend(hierarchy_diags)

    if config.simplify.enabled:
        touched_handles = read_result.flattened_handles | welded_handles
        flat_contours = parts_to_contours(parts)
        simplified, simplify_diags = simplify_contours(
            flat_contours, touched_handles, config.simplify.tolerance,
            config.simplify.collinear_angle_deg, config.simplify.max_area_deviation_pct,
        )
        diagnostics.extend(simplify_diags)
        by_handle = {c.source_handle: c for c in simplified}
        for part in parts:
            part.exterior = by_handle.get(part.exterior.source_handle, part.exterior)
            part.interiors = [by_handle.get(h.source_handle, h) for h in part.interiors]

    contour_count_after = len(parts_to_contours(parts))
    node_count_after = sum(len(c.segments) for c in parts_to_contours(parts))

    stats = dict(
        contour_count_before=contour_count_before,
        contour_count_after=contour_count_after,
        node_count_before=node_count_before,
        node_count_after=node_count_after,
        dedup_count=dedup_count,
        closed_count=closed_count,
        weld_count=weld_count,
    )
    report = validate(parts, diagnostics, config.validate, stats)

    return PipelineResult(parts=parts, diagnostics=diagnostics, report=report)


def write_pipeline_result(result: PipelineResult, output_path: str, config: Config) -> None:
    write_dxf(parts_to_contours(result.parts), output_path, config)
```

- [ ] **Step 8: Run to verify passing**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (all Phase 1/2 pipeline tests plus the 3 new ones)

- [ ] **Step 9: Run the full test suite**

Run: `pytest -v`
Expected: PASS, all tests across Phase 1, 2, and 3 green.

- [ ] **Step 10: Commit**

```bash
git add dxf_cleaner/reader.py dxf_cleaner/pipeline.py tests/test_reader.py tests/test_pipeline.py
git commit -m "feat: wire weld and simplify stages into run_pipeline, populate real weld_count"
```

---

## Preflight Note (reference-code review before implementation starts)

Two spots in this plan's reference code are worth a second look by whoever picks up Task 1/Task 2, since Phase 1 and Phase 2 each turned up a real bug in the plan's own reference code at this same stage:

1. **`_detect_arcs_in_ring`'s wraparound indexing** (Task 2, Step 3): the window scan uses `points[k % n]` and allows `j` up to `i + n - 1` so an arc can straddle the ring's closing point (e.g., a circle whose `_ring_points` happens to start mid-arc). Trace through a case where `i` is near the end of `points` (e.g. `i = n - 2` on an all-arc ring) by hand before trusting it — confirm `best_end % n` and the final segment's `end` point line up with the *next* segment's `start` (i.e. re-run `Contour.assert_contiguous()` on the result in a scratch script), since a contiguity break here would only surface much later, in `writer.py`.
2. **`simplify.py`'s `has_arcs` branch skips Douglas-Peucker entirely** (Task 3, `_simplify_one`): a welded contour with, say, 3 arcs and 200 straight-line segments between them only gets the collinear-merge pass, never shapely's `simplify()`, on its line segments — meaning non-collinear-but-nearly-straight noise (a real-world common case after `unary_union`) won't be reduced. If a task's implementer or reviewer decides this under-simplifies real welded output, the fix is to run Douglas-Peucker on the maximal *runs* of consecutive line segments between arcs (not skip it for the whole contour) — flag this to the user as a candidate scope addition rather than silently expanding Task 3, since it's not in the original spec text either way.
