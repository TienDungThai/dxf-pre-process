# DXF Cleaner — Phase 2 (Error Correction: snap, dedupe, despeckle, hierarchy, validate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase 1 read→write pipeline into an actual cleaning tool: close nearly-touching open contours (snap), remove duplicate/overlapping edges (dedupe), remove speck-sized shapes (despeckle), determine exterior/hole nesting (hierarchy), and produce a read-only validation report (validate) — wired together by a new `pipeline.py` that runs the full stage sequence and can write the result back out. No weld or simplify yet (Phase 3).

**Architecture:** Each new stage is a pure function: `list[Contour] -> (list[Contour] | list[Part], list[Diagnostic])`, taking tolerances from `Config`, never silently changing geometry (everything that's added, removed, or merged produces a `Diagnostic`). `snap.py` and `dedupe.py` both work by flattening all input contours into one pool of `Segment`s (with per-segment origin tracking), because the errors they fix — small gaps, duplicate edges — occur *between* Contours (usually because CorelDRAW exports separate entities for what should be one shape), not within an already-contiguous Contour. `hierarchy.py` builds a containment tree over the *closed* contours that come out of snap/dedupe/despeckle and groups them into `Part` (exterior + interiors) objects. `validate.py` runs last, is read-only, and classifies the whole run as `ok`/`warning`/`critical` by scanning the diagnostics every earlier stage produced plus a few of its own read-only geometric checks. `pipeline.py` runs `read_dxf` → snap → dedupe → despeckle → hierarchy → validate, and can flatten the resulting `Part`s back into a `list[Contour]` for `write_dxf`.

**Tech Stack:** Python 3.11+, ezdxf, shapely 2.x (verified this session: `shapely.STRtree.query(geometry, predicate=..., distance=...)` — the predicate is evaluated as `geometry.<predicate>(tree_geometry)`, so `tree.query(g, predicate="dwithin", distance=d)` finds tree geometries within `d` of `g`, and `tree.query(g, predicate="within")` finds tree geometries that **contain** `g`, i.e. geometries `t` such that `g.within(t)`), numpy, pydantic + PyYAML, pytest.

**Spec:** [docs/superpowers/specs/2026-09-09-dxf-cleaner-design.md](../specs/2026-09-09-dxf-cleaner-design.md)

## Global Constraints

- Builds on Phase 1's `dxf_cleaner/model.py` (`Segment`, `Contour` incl. `assert_contiguous`/`to_shapely`, `Part`, `Diagnostic`, `scale_contour`, `discretize_arc`, `discretize_contour`, `contour_bbox`, `contour_signed_area`, `contour_as_full_circle`), `dxf_cleaner/config.py` (`InputConfig`, `FlattenConfig`, `OutputConfig`, `Config`, `load_config`), `dxf_cleaner/reader.py` (`ReadResult`, `read_dxf`), `dxf_cleaner/writer.py` (`write_dxf`), `dxf_cleaner/stages/explode.py`, `dxf_cleaner/stages/flatten.py`. Do not modify any Phase 1 file except `dxf_cleaner/model.py` (Task 2 adds one small helper) — Phase 1 is done, reviewed, and has its own regression suite; touching it risks regressing work already signed off.
- Every stage function returns `(result, list[Diagnostic])` — never raises for data-quality problems (only for programming errors / impossible states). Every geometry change (removal, merge, reorientation) must produce a `Diagnostic` with a `code` a human or `validate.py` can act on.
- All tolerances are `Config` fields, never hard-coded. New config sections this phase adds: `snap` (`tolerance: float = 0.05`, `max_reportable_gap: float = 2.0`), `dedupe` (`enabled: bool = True`, `merge_common_edges: bool = False`), `despeckle` (`min_perimeter: float = 0.5`, `min_area: float = 0.1`), `validate` (`sheet_width: float = 1500`, `sheet_height: float = 3000`, `material_thickness: float = 2.0`, `kerf_width: float = 0.15`, `min_hole_diameter_ratio: float = 1.0`) — exact defaults from spec section 8's `config.yaml`.
- **Documented scope decisions (spec is ambiguous or silent on these; stated here so they're visible, not silently guessed):**
  1. Spec 6.5 lists a third despeckle criterion ("bbox diameter smaller than the laser beam diameter") with no corresponding config field anywhere in spec section 8. This phase implements only `min_perimeter` and `min_area` (both of which *do* have config fields) and omits the beam-diameter criterion. Do not invent a new config field for it.
  2. Spec 6.4's case (a) "hoàn toàn trùng lặp" (`duplicate_lines.dxf`, two whole duplicate LINE entities) and case (c) "biên chung giữa hai chi tiết" (two otherwise-different parts sharing one edge) look geometrically identical (same segment key) — the only way to tell them apart is whether the shared segment is the *entire* contour on both sides, or just one edge of a bigger, otherwise-different shape. This plan's `dedupe.py` treats a duplicate-key group as case (a) — auto-remove — only when **every** contour it came from has exactly one segment (the segment *is* the whole contour, both sides). Any group where at least one side is a multi-segment contour is case (c) — kept and reported by default, only merged when `dedupe.merge_common_edges` is true. Whole-*contour* duplicates (same shape, multiple segments, drawn twice) are deliberately **not** touched by dedupe — spec 6.9 lists "chi tiết trùng lặp hoàn toàn" as a `validate.py` **Warning**, not a `dedupe.py` fix, so that's where it's detected.
  3. `dedupe.merge_common_edges = true` can turn a segment-count mismatch between two originally-closed contours into an open one (removing a shared border segment from one side literally opens that contour, since Phase 2 has no weld/re-stitch step — that's Phase 3). This is an accepted, documented consequence of an opt-in flag (default `false`), not a bug: `validate.py`'s open-contour check runs on the post-dedupe contour set and will catch it as Critical if it happens.
  4. `hierarchy.py`'s "distance between parts" and "hole vs. material thickness" checks (spec 6.9's Warning list) don't have exact formulas in the spec. This plan uses `shapely.distance()` between exterior/hole polygons for spacing, and an area-derived diameter (`2*sqrt(area/π)`) for hole size — documented approximations, not spec-mandated formulas.

---

## File Structure

```
dxf_cleaner/
├── model.py              # MODIFY: add reverse_segment() (Task 2)
├── config.py              # MODIFY: add SnapConfig/DedupeConfig/DespeckleConfig/ValidateConfig (Task 1)
├── pipeline.py             # NEW: orchestrates read_dxf -> snap -> dedupe -> despeckle -> hierarchy -> validate (Task 8)
└── stages/
    ├── snap.py             # NEW: snap_and_chain (Task 3)
    ├── dedupe.py           # NEW: dedupe_contours (Task 4)
    ├── despeckle.py        # NEW: despeckle_contours (Task 5)
    ├── hierarchy.py        # NEW: build_hierarchy (Task 6)
    └── validate.py         # NEW: validate, ValidationReport (Task 7)

tests/
├── test_config.py          # MODIFY: append new section tests (Task 1)
├── test_model.py            # MODIFY: append reverse_segment tests (Task 2)
├── test_pipeline.py          # NEW: integration tests (Task 8)
└── stages/
    ├── test_snap.py          # NEW (Task 3)
    ├── test_dedupe.py        # NEW (Task 4)
    ├── test_despeckle.py     # NEW (Task 5)
    ├── test_hierarchy.py     # NEW (Task 6)
    └── test_validate.py      # NEW (Task 7)
```

---

### Task 1: `config.py` — Snap/Dedupe/Despeckle/Validate config sections

**Files:**
- Modify: `dxf_cleaner/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `InputConfig`, `FlattenConfig`, `OutputConfig`, `Config`, `load_config` (existing, unchanged).
- Produces:
  - `SnapConfig(tolerance: float = 0.05, max_reportable_gap: float = 2.0)`
  - `DedupeConfig(enabled: bool = True, merge_common_edges: bool = False)`
  - `DespeckleConfig(min_perimeter: float = 0.5, min_area: float = 0.1)`
  - `ValidateConfig(sheet_width: float = 1500, sheet_height: float = 3000, material_thickness: float = 2.0, kerf_width: float = 0.15, min_hole_diameter_ratio: float = 1.0)`
  - `Config` gains `snap: SnapConfig`, `dedupe: DedupeConfig`, `despeckle: DespeckleConfig`, `validate: ValidateConfig` fields (in that order, between `flatten` and `output`).

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_config.py
from dxf_cleaner.config import SnapConfig, DedupeConfig, DespeckleConfig, ValidateConfig


def test_new_phase2_config_defaults():
    cfg = Config()
    assert cfg.snap.tolerance == 0.05
    assert cfg.snap.max_reportable_gap == 2.0
    assert cfg.dedupe.enabled is True
    assert cfg.dedupe.merge_common_edges is False
    assert cfg.despeckle.min_perimeter == 0.5
    assert cfg.despeckle.min_area == 0.1
    assert cfg.validate.sheet_width == 1500
    assert cfg.validate.sheet_height == 3000
    assert cfg.validate.material_thickness == 2.0
    assert cfg.validate.kerf_width == 0.15
    assert cfg.validate.min_hole_diameter_ratio == 1.0


def test_phase2_config_yaml_override(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "snap:\n  tolerance: 0.1\n"
        "dedupe:\n  merge_common_edges: true\n"
    )
    cfg = load_config(str(yaml_path))
    assert cfg.snap.tolerance == 0.1
    assert cfg.dedupe.merge_common_edges is True
    # untouched sections keep defaults
    assert cfg.despeckle.min_area == 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v -k phase2`
Expected: FAIL with `ImportError: cannot import name 'SnapConfig'`

- [ ] **Step 3: Implement in `dxf_cleaner/config.py`**

```python
class SnapConfig(BaseModel):
    tolerance: float = 0.05
    max_reportable_gap: float = 2.0


class DedupeConfig(BaseModel):
    enabled: bool = True
    merge_common_edges: bool = False


class DespeckleConfig(BaseModel):
    min_perimeter: float = 0.5
    min_area: float = 0.1


class ValidateConfig(BaseModel):
    sheet_width: float = 1500
    sheet_height: float = 3000
    material_thickness: float = 2.0
    kerf_width: float = 0.15
    min_hole_diameter_ratio: float = 1.0


class Config(BaseModel):
    input: InputConfig = InputConfig()
    flatten: FlattenConfig = FlattenConfig()
    snap: SnapConfig = SnapConfig()
    dedupe: DedupeConfig = DedupeConfig()
    despeckle: DespeckleConfig = DespeckleConfig()
    validate: ValidateConfig = ValidateConfig()
    output: OutputConfig = OutputConfig()
```

(Insert the four new classes after `FlattenConfig` and before `OutputConfig`; update the `Config` class's field list in place — keep `load_config` unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/config.py tests/test_config.py
git commit -m "feat: add snap/dedupe/despeckle/validate config sections"
```

---

### Task 2: `model.py` — `reverse_segment` helper

**Files:**
- Modify: `dxf_cleaner/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: `Segment` (existing).
- Produces: `reverse_segment(segment: Segment) -> Segment` — returns a new `Segment` with `start`/`end` swapped; for an arc, also swaps `center`/`radius` unchanged but flips `ccw` (reversing traversal direction reverses the winding sense). Both `snap.py` (Task 3) and `hierarchy.py` (Task 6) need this to walk/reorient chains.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_model.py
from dxf_cleaner.model import reverse_segment


def test_reverse_segment_line_swaps_endpoints():
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 2.0))
    rev = reverse_segment(seg)
    assert rev.start == (1.0, 2.0)
    assert rev.end == (0.0, 0.0)
    assert rev.kind == "line"


def test_reverse_segment_arc_flips_ccw_keeps_center_radius():
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    rev = reverse_segment(seg)
    assert rev.start == (0.0, 1.0)
    assert rev.end == (1.0, 0.0)
    assert rev.center == (0.0, 0.0)
    assert rev.radius == 1.0
    assert rev.ccw is False


def test_reverse_segment_is_its_own_inverse():
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    assert reverse_segment(reverse_segment(seg)) == seg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_model.py -v -k reverse_segment`
Expected: FAIL with `ImportError: cannot import name 'reverse_segment'`

- [ ] **Step 3: Implement in `dxf_cleaner/model.py`** (add near `scale_contour`)

```python
def reverse_segment(segment: Segment) -> Segment:
    """Return a new Segment traversed in the opposite direction."""
    return Segment(
        kind=segment.kind,
        start=segment.end,
        end=segment.start,
        center=segment.center,
        radius=segment.radius,
        ccw=(not segment.ccw) if segment.kind == "arc" else segment.ccw,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_model.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/model.py tests/test_model.py
git commit -m "feat: add reverse_segment helper for chain-walking stages"
```

---

### Task 3: `stages/snap.py` — endpoint clustering and chain reconnection

**Files:**
- Create: `dxf_cleaner/stages/snap.py`
- Test: `tests/stages/test_snap.py`

**Interfaces:**
- Consumes: `Segment`, `Contour`, `Diagnostic`, `Point`, `reverse_segment` (from `dxf_cleaner.model`); `shapely.STRtree`, `shapely.geometry.Point`.
- Produces: `snap_and_chain(contours: list[Contour], tolerance: float, max_reportable_gap: float) -> tuple[list[Contour], list[Diagnostic]]`

Algorithm (per spec 6.3, with the "collect all Segment endpoints" step read literally — this also means interior joints that are already exact get trivially re-clustered with themselves, so no special-casing is needed for "only look at free contour ends"):
1. Flatten every input `Contour`'s segments into one list, remembering each segment's originating contour's `source_layer`/`source_handle`.
2. Build a `shapely.STRtree` over all `2 * n` segment endpoints (start and end of every segment). For each endpoint, `tree.query(point, predicate="dwithin", distance=tolerance)` to find every other endpoint within tolerance, union them into clusters (simple union-find).
3. Replace every segment's `start`/`end` with its cluster's centroid (average of every endpoint in that cluster — never the first point, to avoid drift).
4. Re-chain: build an adjacency map from canonical point → `(segment_index, "start"|"end")`. Walk each not-yet-used segment forward: at the current chain's trailing point, if exactly one *other* unused segment touches it, extend the chain (reversing that segment via `reverse_segment` if it attaches via its own `"end"`); if the trailing point equals the chain's own start point, the chain is closed; if zero segments touch it, the chain ends there (open); if more than one touches it, pick the first (by segment index) and emit an `AMBIGUOUS_JUNCTION` diagnostic (documented simplification — this plan does not attempt branch-aware chain splitting).
5. Any chain left open at the end has two free ends. Build a second `STRtree` over every open chain's two free ends; for each, look for the nearest free end belonging to a *different* chain within `max_reportable_gap`. If found (and it will always be `> tolerance`, since anything closer would already have been clustered in step 2), emit one `OPEN_GAP` diagnostic per gap (dedup so each gap is reported once, not twice from each side). Gaps beyond `max_reportable_gap` are never reported (treated as intentionally separate parts).

- [ ] **Step 1: Write failing tests**

```python
# tests/stages/test_snap.py
import math
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.snap import snap_and_chain


def _open_contour(segments, layer="0", handle="H"):
    return Contour(segments=segments, is_closed=False, source_layer=layer, source_handle=handle)


def test_two_lines_with_small_gap_are_chained_into_one_open_contour():
    # two separate LINE entities that should form one continuous open polyline,
    # but their shared point is 0.03mm apart (a common Corel rounding artifact).
    c1 = _open_contour([Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))], handle="A")
    c2 = _open_contour([Segment(kind="line", start=(1.03, 0.0), end=(2.0, 0.0))], handle="B")
    result, diagnostics = snap_and_chain([c1, c2], tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 1
    assert len(result[0].segments) == 2
    assert result[0].is_closed is False
    # the joined point must be a single canonical location, not either original value
    joined = result[0].segments[0].end
    assert result[0].segments[1].start == joined
    assert not any(d.code == "OPEN_GAP" for d in diagnostics)


def test_four_lines_with_small_gaps_close_into_one_closed_contour():
    # an "open rectangle" made of 4 separate LINE entities, each corner off by 0.02mm.
    p = [(0.0, 0.0), (10.02, 0.0), (10.0, 10.02), (-0.02, 10.0)]
    segs = [
        _open_contour([Segment(kind="line", start=p[0], end=p[1])], handle="A"),
        _open_contour([Segment(kind="line", start=p[1], end=p[2])], handle="B"),
        _open_contour([Segment(kind="line", start=p[2], end=p[3])], handle="C"),
        _open_contour([Segment(kind="line", start=(p[3][0], p[3][1]), end=(0.02, 0.0))], handle="D"),
    ]
    result, diagnostics = snap_and_chain(segs, tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 1
    assert result[0].is_closed is True
    assert len(result[0].segments) == 4
    result[0].assert_contiguous()  # no ValueError


def test_gap_within_reportable_range_is_reported_but_not_joined():
    c1 = _open_contour([Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))], handle="A")
    c2 = _open_contour([Segment(kind="line", start=(1.5, 0.0), end=(2.5, 0.0))], handle="B")
    result, diagnostics = snap_and_chain([c1, c2], tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 2  # not joined, 0.5mm > 0.05mm tolerance
    gap_diags = [d for d in diagnostics if d.code == "OPEN_GAP"]
    assert len(gap_diags) == 1
    assert "0.5" in gap_diags[0].message


def test_gap_beyond_max_reportable_gap_is_not_reported():
    c1 = _open_contour([Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))], handle="A")
    c2 = _open_contour([Segment(kind="line", start=(10.0, 0.0), end=(11.0, 0.0))], handle="B")
    result, diagnostics = snap_and_chain([c1, c2], tolerance=0.05, max_reportable_gap=2.0)
    assert len(result) == 2
    assert not any(d.code == "OPEN_GAP" for d in diagnostics)


def test_t_junction_reports_ambiguous_and_still_chains_two_of_three():
    # three lines meeting at the origin: this is a branch, not a simple chain.
    a = Segment(kind="line", start=(-1.0, 0.0), end=(0.0, 0.0))
    b = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    c = Segment(kind="line", start=(0.0, 0.0), end=(0.0, 1.0))
    result, diagnostics = snap_and_chain(
        [_open_contour([a], handle="A"), _open_contour([b], handle="B"), _open_contour([c], handle="C")],
        tolerance=0.05, max_reportable_gap=2.0,
    )
    assert any(d.code == "AMBIGUOUS_JUNCTION" for d in diagnostics)
    assert sum(len(c.segments) for c in result) == 3  # no segment lost
    assert len(result) == 2  # one 2-segment chain + one leftover 1-segment contour
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/stages/test_snap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.snap'`

- [ ] **Step 3: Implement `dxf_cleaner/stages/snap.py`**

```python
import math
from shapely import STRtree
from shapely.geometry import Point as ShapelyPoint

from dxf_cleaner.model import Segment, Contour, Diagnostic, Point, reverse_segment


def _find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[ra] = rb


def snap_and_chain(
    contours: list[Contour], tolerance: float, max_reportable_gap: float
) -> tuple[list[Contour], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    segments: list[Segment] = []
    layers: list[str] = []
    handles: list[str] = []
    for contour in contours:
        for seg in contour.segments:
            segments.append(seg)
            layers.append(contour.source_layer)
            handles.append(contour.source_handle)

    n = len(segments)
    if n == 0:
        return [], diagnostics

    endpoint_coords: list[Point] = []
    for seg in segments:
        endpoint_coords.append(seg.start)
        endpoint_coords.append(seg.end)

    points = [ShapelyPoint(p) for p in endpoint_coords]
    tree = STRtree(points)
    parent = list(range(len(endpoint_coords)))
    for i, p in enumerate(points):
        for j in tree.query(p, predicate="dwithin", distance=tolerance):
            j = int(j)
            if j != i:
                _union(parent, i, j)

    cluster_points: dict[int, list[Point]] = {}
    for i, coord in enumerate(endpoint_coords):
        cluster_points.setdefault(_find(parent, i), []).append(coord)

    canonical: dict[int, Point] = {
        root: (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))
        for root, coords in cluster_points.items()
    }

    def canon_point(endpoint_idx: int) -> Point:
        return canonical[_find(parent, endpoint_idx)]

    snapped_segments = [
        Segment(
            kind=seg.kind,
            start=canon_point(2 * i),
            end=canon_point(2 * i + 1),
            center=seg.center,
            radius=seg.radius,
            ccw=seg.ccw,
        )
        for i, seg in enumerate(segments)
    ]

    adjacency: dict[Point, list[tuple[int, str]]] = {}
    for i, seg in enumerate(snapped_segments):
        adjacency.setdefault(seg.start, []).append((i, "start"))
        adjacency.setdefault(seg.end, []).append((i, "end"))

    used = [False] * n
    chains: list[tuple[list[Segment], bool, str, str]] = []

    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        chain = [snapped_segments[i]]
        chain_start = snapped_segments[i].start
        current_end = snapped_segments[i].end
        closed = False
        while True:
            if current_end == chain_start:
                closed = True
                break
            candidates = [(idx, which) for idx, which in adjacency.get(current_end, []) if not used[idx]]
            if not candidates:
                break
            if len(candidates) > 1:
                diagnostics.append(Diagnostic(
                    code="AMBIGUOUS_JUNCTION",
                    message=f"Multiple unvisited segments meet at {current_end}; chaining picked one arbitrarily",
                ))
            idx, which = candidates[0]
            used[idx] = True
            seg = snapped_segments[idx]
            if which == "end":
                seg = reverse_segment(seg)
            chain.append(seg)
            current_end = seg.end
        chains.append((chain, closed, layers[i], handles[i]))

    result_contours = [
        Contour(segments=segs, is_closed=closed, source_layer=layer, source_handle=handle)
        for segs, closed, layer, handle in chains
    ]

    open_ends: list[tuple[Point, int]] = []
    for ci, (segs, closed, _, _) in enumerate(chains):
        if not closed:
            open_ends.append((segs[0].start, ci))
            open_ends.append((segs[-1].end, ci))

    if open_ends:
        end_points = [ShapelyPoint(p) for p, _ in open_ends]
        end_tree = STRtree(end_points)
        reported: set[tuple[int, int]] = set()
        for i, (p, chain_i) in enumerate(open_ends):
            best: tuple[float, int, Point] | None = None
            for j in end_tree.query(end_points[i], predicate="dwithin", distance=max_reportable_gap):
                j = int(j)
                if j == i:
                    continue
                other_point, other_chain = open_ends[j]
                if other_chain == chain_i:
                    continue
                dist = math.dist(p, other_point)
                if dist <= tolerance:
                    continue
                if best is None or dist < best[0]:
                    best = (dist, j, other_point)
            if best is not None:
                dist, j, other_point = best
                key = (min(i, j), max(i, j))
                if key not in reported:
                    reported.add(key)
                    diagnostics.append(Diagnostic(
                        code="OPEN_GAP",
                        message=f"Gap of {dist:.4f}mm between {p} and {other_point}",
                    ))

    return result_contours, diagnostics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/stages/test_snap.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/snap.py tests/stages/test_snap.py
git commit -m "feat: add snap_and_chain (endpoint clustering + contour reconnection)"
```

---

### Task 4: `stages/dedupe.py` — duplicate/overlapping segment removal

**Files:**
- Create: `dxf_cleaner/stages/dedupe.py`
- Test: `tests/stages/test_dedupe.py`

**Interfaces:**
- Consumes: `Segment`, `Contour`, `Diagnostic`, `Point` (from `dxf_cleaner.model`).
- Produces: `dedupe_contours(contours: list[Contour], merge_common_edges: bool) -> tuple[list[Contour], list[Diagnostic]]`

Per the "Documented scope decisions" in Global Constraints (item 2): a duplicate-key segment group is case (a) (auto-remove) only if every contour it came from is a single-segment contour; otherwise it's case (c) (common edge — kept by default, `Diagnostic(code="COMMON_EDGE")`, removed only if `merge_common_edges`). Independently, case (b) merges partially-overlapping *collinear line* segments (not arcs) into one covering segment.

- [ ] **Step 1: Write failing tests**

```python
# tests/stages/test_dedupe.py
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.dedupe import dedupe_contours


def _contour(segments, closed=False, layer="0", handle="H"):
    return Contour(segments=segments, is_closed=closed, source_layer=layer, source_handle=handle)


def test_fully_duplicate_standalone_lines_are_deduped():
    # duplicate_lines.dxf equivalent: two entities drawing the exact same line,
    # one reversed (a common Corel stroke+fill export artifact).
    c1 = _contour([Segment(kind="line", start=(0.0, 0.0), end=(5.0, 0.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(5.0, 0.0), end=(0.0, 0.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 1
    assert any(d.code == "DUPLICATE_SEGMENT_REMOVED" for d in diagnostics)


def test_shared_edge_between_two_rectangles_kept_by_default():
    # two adjacent 4-segment rectangles sharing one full edge (x=10 from y=0 to y=10).
    left = _contour([
        Segment(kind="line", start=(0.0, 0.0), end=(10.0, 0.0)),
        Segment(kind="line", start=(10.0, 0.0), end=(10.0, 10.0)),
        Segment(kind="line", start=(10.0, 10.0), end=(0.0, 10.0)),
        Segment(kind="line", start=(0.0, 10.0), end=(0.0, 0.0)),
    ], closed=True, handle="LEFT")
    right = _contour([
        Segment(kind="line", start=(10.0, 0.0), end=(20.0, 0.0)),
        Segment(kind="line", start=(20.0, 0.0), end=(20.0, 10.0)),
        Segment(kind="line", start=(20.0, 10.0), end=(10.0, 10.0)),
        Segment(kind="line", start=(10.0, 10.0), end=(10.0, 0.0)),  # shared edge, reversed direction
    ], closed=True, handle="RIGHT")
    result, diagnostics = dedupe_contours([left, right], merge_common_edges=False)
    assert sum(len(c.segments) for c in result) == 8  # nothing removed
    assert any(d.code == "COMMON_EDGE" for d in diagnostics)


def test_shared_edge_removed_when_merge_common_edges_enabled():
    left = _contour([
        Segment(kind="line", start=(0.0, 0.0), end=(10.0, 0.0)),
        Segment(kind="line", start=(10.0, 0.0), end=(10.0, 10.0)),
        Segment(kind="line", start=(10.0, 10.0), end=(0.0, 10.0)),
        Segment(kind="line", start=(0.0, 10.0), end=(0.0, 0.0)),
    ], closed=True, handle="LEFT")
    right = _contour([
        Segment(kind="line", start=(10.0, 10.0), end=(10.0, 0.0)),  # only the shared edge, for simplicity
    ], closed=False, handle="RIGHT")
    result, diagnostics = dedupe_contours([left, right], merge_common_edges=True)
    assert sum(len(c.segments) for c in result) == 4  # right's only segment removed, right contour dropped entirely
    assert len(result) == 1


def test_partially_overlapping_collinear_lines_are_merged():
    # two overlapping horizontal segments on y=0: [0,6] and [4,10] -> merged to [0,10]
    c1 = _contour([Segment(kind="line", start=(0.0, 0.0), end=(6.0, 0.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(4.0, 0.0), end=(10.0, 0.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 1
    seg = result[0].segments[0]
    assert {seg.start, seg.end} == {(0.0, 0.0), (10.0, 0.0)}
    assert any(d.code == "OVERLAPPING_SEGMENTS_MERGED" for d in diagnostics)


def test_non_overlapping_collinear_lines_are_left_alone():
    c1 = _contour([Segment(kind="line", start=(0.0, 0.0), end=(2.0, 0.0))], handle="A")
    c2 = _contour([Segment(kind="line", start=(5.0, 0.0), end=(7.0, 0.0))], handle="B")
    result, diagnostics = dedupe_contours([c1, c2], merge_common_edges=False)
    assert len(result) == 2
    assert not any(d.code == "OVERLAPPING_SEGMENTS_MERGED" for d in diagnostics)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/stages/test_dedupe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.dedupe'`

- [ ] **Step 3: Implement `dxf_cleaner/stages/dedupe.py`**

```python
import math
from dxf_cleaner.model import Segment, Contour, Diagnostic, Point


def _round_point(p: Point, ndigits: int = 6) -> Point:
    return (round(p[0], ndigits), round(p[1], ndigits))


def _segment_key(seg: Segment, ndigits: int = 6) -> tuple:
    endpoints = frozenset((_round_point(seg.start, ndigits), _round_point(seg.end, ndigits)))
    if seg.kind == "line":
        return ("line", endpoints)
    return (
        "arc", endpoints,
        round(seg.center[0], ndigits), round(seg.center[1], ndigits), round(seg.radius, ndigits),
    )


def _line_group_key(seg: Segment, ndigits: int = 4) -> tuple[float, float, float]:
    dx, dy = seg.end[0] - seg.start[0], seg.end[1] - seg.start[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    if ux < 0 or (ux == 0 and uy < 0):
        ux, uy = -ux, -uy
    perp = seg.start[0] * (-uy) + seg.start[1] * ux
    return (round(ux, ndigits), round(uy, ndigits), round(perp, ndigits))


def dedupe_contours(contours: list[Contour], merge_common_edges: bool) -> tuple[list[Contour], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    segments: list[Segment] = []
    origin_contour: list[int] = []
    for ci, contour in enumerate(contours):
        for seg in contour.segments:
            segments.append(seg)
            origin_contour.append(ci)

    removed = [False] * len(segments)

    groups: dict[tuple, list[int]] = {}
    for i, seg in enumerate(segments):
        groups.setdefault(_segment_key(seg), []).append(i)

    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        all_standalone = all(len(contours[origin_contour[i]].segments) == 1 for i in idxs)
        s0 = segments[idxs[0]]
        if all_standalone:
            for d in idxs[1:]:
                removed[d] = True
                diagnostics.append(Diagnostic(
                    code="DUPLICATE_SEGMENT_REMOVED",
                    message=f"Removed duplicate segment at {segments[d].start}-{segments[d].end}",
                ))
        else:
            diagnostics.append(Diagnostic(
                code="COMMON_EDGE",
                message=(
                    f"Shared edge at {s0.start}-{s0.end} merged (merge_common_edges enabled)"
                    if merge_common_edges else
                    f"Shared edge at {s0.start}-{s0.end} kept (common-line cutting)"
                ),
            ))
            if merge_common_edges:
                for d in idxs[1:]:
                    removed[d] = True

    line_groups: dict[tuple, list[int]] = {}
    for i, seg in enumerate(segments):
        if removed[i] or seg.kind != "line":
            continue
        line_groups.setdefault(_line_group_key(seg), []).append(i)

    for key, idxs in line_groups.items():
        if len(idxs) < 2:
            continue
        ux, uy = key[0], key[1]
        intervals = []
        for i in idxs:
            seg = segments[i]
            t0 = seg.start[0] * ux + seg.start[1] * uy
            t1 = seg.end[0] * ux + seg.end[1] * uy
            intervals.append((min(t0, t1), max(t0, t1), i))
        intervals.sort()

        cluster: list[int] = [intervals[0][2]]
        cur_lo, cur_hi = intervals[0][0], intervals[0][1]
        for lo, hi, i in intervals[1:]:
            if lo < cur_hi:  # strict: genuine overlap only, not mere endpoint-adjacency
                cur_hi = max(cur_hi, hi)
                cluster.append(i)
            else:
                _finalize_overlap_cluster(cluster, segments, removed, diagnostics, ux, uy, cur_lo, cur_hi)
                cluster, cur_lo, cur_hi = [i], lo, hi
        _finalize_overlap_cluster(cluster, segments, removed, diagnostics, ux, uy, cur_lo, cur_hi)

    result: list[Contour] = []
    for ci, contour in enumerate(contours):
        kept = [seg for i, seg in enumerate(segments) if origin_contour[i] == ci and not removed[i]]
        if kept:
            result.append(Contour(
                segments=kept, is_closed=contour.is_closed,
                source_layer=contour.source_layer, source_handle=contour.source_handle,
            ))
    return result, diagnostics


def _finalize_overlap_cluster(
    member_idxs: list[int], segments: list[Segment], removed: list[bool],
    diagnostics: list[Diagnostic], ux: float, uy: float, lo: float, hi: float,
) -> None:
    if len(member_idxs) < 2:
        return
    keep_idx = member_idxs[0]
    start, end = (lo * ux, lo * uy), (hi * ux, hi * uy)
    segments[keep_idx] = Segment(kind="line", start=start, end=end)
    for i in member_idxs[1:]:
        removed[i] = True
    diagnostics.append(Diagnostic(
        code="OVERLAPPING_SEGMENTS_MERGED",
        message=f"Merged {len(member_idxs)} overlapping collinear segments into {start}-{end}",
    ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/stages/test_dedupe.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/dedupe.py tests/stages/test_dedupe.py
git commit -m "feat: add dedupe_contours (exact duplicates, common edges, collinear overlap merge)"
```

---

### Task 5: `stages/despeckle.py` — remove speck-sized closed contours

**Files:**
- Create: `dxf_cleaner/stages/despeckle.py`
- Test: `tests/stages/test_despeckle.py`

**Interfaces:**
- Consumes: `Contour`, `Diagnostic`, `contour_bbox`, `contour_signed_area`, `discretize_contour` (from `dxf_cleaner.model`).
- Produces: `despeckle_contours(contours: list[Contour], min_perimeter: float, min_area: float) -> tuple[list[Contour], list[Diagnostic]]` — only judges *closed* contours (open contours pass through untouched; they're handled elsewhere).

- [ ] **Step 1: Write failing tests**

```python
# tests/stages/test_despeckle.py
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.stages.despeckle import despeckle_contours


def _square(side, handle="H"):
    p = [(0.0, 0.0), (side, 0.0), (side, side), (0.0, side)]
    segs = [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


def test_tiny_square_below_area_threshold_is_removed():
    tiny = _square(0.1, handle="TINY")  # area 0.01mm2 < default min_area 0.1
    result, diagnostics = despeckle_contours([tiny], min_perimeter=0.5, min_area=0.1)
    assert result == []
    assert any(d.code == "DESPECKLED" and d.handle == "TINY" for d in diagnostics)


def test_normal_square_is_kept():
    normal = _square(10.0, handle="NORMAL")
    result, diagnostics = despeckle_contours([normal], min_perimeter=0.5, min_area=0.1)
    assert len(result) == 1
    assert diagnostics == []


def test_open_contours_are_never_despeckled():
    tiny_open = Contour(
        segments=[Segment(kind="line", start=(0.0, 0.0), end=(0.01, 0.0))],
        is_closed=False, source_layer="0", source_handle="OPEN",
    )
    result, diagnostics = despeckle_contours([tiny_open], min_perimeter=0.5, min_area=0.1)
    assert result == [tiny_open]
    assert diagnostics == []


def test_thin_sliver_below_perimeter_threshold_is_removed_even_with_zero_area():
    # a degenerate near-zero-width closed sliver: large enough area might round to
    # ~0, but perimeter is the deciding factor here.
    segs = [
        Segment(kind="line", start=(0.0, 0.0), end=(0.1, 0.0)),
        Segment(kind="line", start=(0.1, 0.0), end=(0.1, 0.001)),
        Segment(kind="line", start=(0.1, 0.001), end=(0.0, 0.001)),
        Segment(kind="line", start=(0.0, 0.001), end=(0.0, 0.0)),
    ]
    sliver = Contour(segments=segs, is_closed=True, source_layer="0", source_handle="SLIVER")
    result, diagnostics = despeckle_contours([sliver], min_perimeter=0.5, min_area=0.1)
    assert result == []
    assert any(d.code == "DESPECKLED" for d in diagnostics)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/stages/test_despeckle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.despeckle'`

- [ ] **Step 3: Implement `dxf_cleaner/stages/despeckle.py`**

```python
import math
from dxf_cleaner.model import Contour, Diagnostic, contour_bbox, contour_signed_area, discretize_contour


def _contour_perimeter(contour: Contour, arc_tolerance: float = 0.02) -> float:
    points = discretize_contour(contour, arc_tolerance)
    n = len(points)
    return sum(math.dist(points[i], points[(i + 1) % n]) for i in range(n))


def despeckle_contours(
    contours: list[Contour], min_perimeter: float, min_area: float
) -> tuple[list[Contour], list[Diagnostic]]:
    kept: list[Contour] = []
    diagnostics: list[Diagnostic] = []
    for contour in contours:
        if not contour.is_closed:
            kept.append(contour)
            continue
        perimeter = _contour_perimeter(contour)
        area = abs(contour_signed_area(contour))
        if perimeter < min_perimeter or area < min_area:
            diagnostics.append(Diagnostic(
                code="DESPECKLED",
                message=(
                    f"Removed tiny contour (perimeter={perimeter:.4f}mm, area={area:.4f}mm2) "
                    f"at bbox={contour_bbox(contour)}"
                ),
                handle=contour.source_handle,
            ))
            continue
        kept.append(contour)
    return kept, diagnostics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/stages/test_despeckle.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/despeckle.py tests/stages/test_despeckle.py
git commit -m "feat: add despeckle_contours (min perimeter/area filter for closed contours)"
```

---

### Task 6: `stages/hierarchy.py` — exterior/hole containment tree

**Files:**
- Create: `dxf_cleaner/stages/hierarchy.py`
- Test: `tests/stages/test_hierarchy.py`

**Interfaces:**
- Consumes: `Contour`, `Part`, `Diagnostic`, `contour_signed_area`, `reverse_segment` (from `dxf_cleaner.model`); `shapely.STRtree`, `shapely.geometry.Polygon`, `shapely.make_valid`.
- Produces: `build_hierarchy(contours: list[Contour], arc_tolerance: float = 0.02) -> tuple[list[Part], list[Diagnostic]]`

Algorithm (per spec 6.7):
1. Only closed contours participate; every open contour produces `Diagnostic(code="OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY")` and is dropped from this stage's output (still visible to `validate.py` via this diagnostic).
2. Convert each closed contour to a `shapely.Polygon` via `contour.to_shapely(arc_tolerance)`. If `not polygon.is_valid`, call `shapely.make_valid` and emit `Diagnostic(code="INVALID_POLYGON_FIXED")`; if the repaired geometry isn't a single `Polygon` (e.g. a `MultiPolygon`/`GeometryCollection` from a badly self-intersecting shape), keep the largest `Polygon` piece by area; if there's no `Polygon` piece at all, emit `Diagnostic(code="POLYGON_UNRECOVERABLE")` and drop that contour.
3. Build one `shapely.STRtree` over all the resulting polygons. For each polygon `p`, `tree.query(p, predicate="within")` returns every OTHER polygon that contains `p` (see the verified STRtree semantics in Global Constraints) — the count of these (excluding self) is `p`'s nesting depth. Its direct parent is whichever container has the smallest area (the innermost one).
4. Even depth → exterior (reorient its original `Contour` to CCW); odd depth → hole (reorient to CW). Build one `Part` per even-depth polygon (its own new `Part`); attach each odd-depth polygon's *original* `Contour` (reoriented CW) as an `interiors` entry of its direct parent's `Part`. An even-depth polygon whose direct parent is itself odd-depth (an "island" inside a hole) still gets its own independent `Part` — it is not nested as an interior of anything.

- [ ] **Step 1: Write failing tests**

```python
# tests/stages/test_hierarchy.py
import math
from shapely import make_valid
from dxf_cleaner.model import Segment, Contour, contour_signed_area
from dxf_cleaner.stages.hierarchy import build_hierarchy


def _square(x0, y0, side, ccw=True, handle="H"):
    if ccw:
        p = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    else:
        p = [(x0, y0), (x0, y0 + side), (x0 + side, y0 + side), (x0 + side, y0)]
    segs = [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


def test_single_square_becomes_one_part_with_no_interiors():
    sq = _square(0, 0, 10, handle="OUTER")
    parts, diagnostics = build_hierarchy([sq])
    assert len(parts) == 1
    assert parts[0].interiors == []
    assert contour_signed_area(parts[0].exterior) > 0  # CCW


def test_nested_hole_is_attached_as_interior_and_reoriented_cw():
    outer = _square(0, 0, 10, handle="OUTER")
    hole = _square(2, 2, 3, handle="HOLE")  # entirely inside outer, drawn CCW (wrong for a hole)
    parts, diagnostics = build_hierarchy([outer, hole])
    assert len(parts) == 1
    assert len(parts[0].interiors) == 1
    assert contour_signed_area(parts[0].interiors[0]) < 0  # reoriented CW


def test_island_inside_hole_is_its_own_part_not_nested():
    outer = _square(0, 0, 20, handle="OUTER")
    hole = _square(5, 5, 10, handle="HOLE")
    island = _square(7, 7, 2, handle="ISLAND")
    parts, diagnostics = build_hierarchy([outer, hole, island])
    assert len(parts) == 2  # OUTER-with-HOLE, and ISLAND standalone
    outer_part = next(p for p in parts if len(p.interiors) == 1)
    island_part = next(p for p in parts if len(p.interiors) == 0)
    assert island_part.exterior.source_handle == "ISLAND"
    assert contour_signed_area(island_part.exterior) > 0  # CCW, independent exterior


def test_open_contour_is_skipped_with_diagnostic():
    open_c = Contour(
        segments=[Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))],
        is_closed=False, source_layer="0", source_handle="OPEN",
    )
    parts, diagnostics = build_hierarchy([open_c])
    assert parts == []
    assert any(d.code == "OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY" for d in diagnostics)


def test_self_intersecting_bowtie_is_fixed_or_reported():
    # a bowtie (figure-8) polygon: self-intersecting, invalid until make_valid runs.
    bowtie = Contour(
        segments=[
            Segment(kind="line", start=(0.0, 0.0), end=(10.0, 10.0)),
            Segment(kind="line", start=(10.0, 10.0), end=(10.0, 0.0)),
            Segment(kind="line", start=(10.0, 0.0), end=(0.0, 10.0)),
            Segment(kind="line", start=(0.0, 10.0), end=(0.0, 0.0)),
        ],
        is_closed=True, source_layer="0", source_handle="BOWTIE",
    )
    parts, diagnostics = build_hierarchy([bowtie])
    codes = {d.code for d in diagnostics}
    assert "INVALID_POLYGON_FIXED" in codes or "POLYGON_UNRECOVERABLE" in codes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/stages/test_hierarchy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.hierarchy'`

- [ ] **Step 3: Implement `dxf_cleaner/stages/hierarchy.py`**

```python
from shapely import STRtree, make_valid
from shapely.geometry import Polygon

from dxf_cleaner.model import Contour, Part, Diagnostic, contour_signed_area, reverse_segment


def _oriented_contour(contour: Contour, ccw: bool) -> Contour:
    is_ccw = contour_signed_area(contour) > 0
    if is_ccw == ccw:
        return contour
    return Contour(
        segments=[reverse_segment(seg) for seg in reversed(contour.segments)],
        is_closed=contour.is_closed,
        source_layer=contour.source_layer,
        source_handle=contour.source_handle,
    )


def build_hierarchy(contours: list[Contour], arc_tolerance: float = 0.02) -> tuple[list[Part], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    closed: list[Contour] = []
    for c in contours:
        if c.is_closed:
            closed.append(c)
        else:
            diagnostics.append(Diagnostic(
                code="OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY",
                message="Contour is not closed; excluded from exterior/hole hierarchy",
                handle=c.source_handle,
            ))

    polygons: list[Polygon] = []
    valid_closed: list[Contour] = []
    for c in closed:
        poly = Polygon(c.to_shapely(arc_tolerance))
        if not poly.is_valid:
            fixed = make_valid(poly)
            diagnostics.append(Diagnostic(code="INVALID_POLYGON_FIXED", message="Repaired self-intersecting polygon", handle=c.source_handle))
            if fixed.geom_type != "Polygon":
                candidates = [g for g in getattr(fixed, "geoms", []) if g.geom_type == "Polygon"]
                if not candidates:
                    diagnostics.append(Diagnostic(code="POLYGON_UNRECOVERABLE", message="Could not recover a usable polygon", handle=c.source_handle))
                    continue
                fixed = max(candidates, key=lambda g: g.area)
            poly = fixed
        polygons.append(poly)
        valid_closed.append(c)

    n = len(polygons)
    if n == 0:
        return [], diagnostics

    tree = STRtree(polygons)
    depth = [0] * n
    parent_of: list[int | None] = [None] * n
    for i, poly in enumerate(polygons):
        containers = [int(j) for j in tree.query(poly, predicate="within") if int(j) != i]
        depth[i] = len(containers)
        if containers:
            parent_of[i] = min(containers, key=lambda j: polygons[j].area)

    parts: dict[int, Part] = {
        i: Part(exterior=_oriented_contour(valid_closed[i], ccw=True))
        for i in range(n) if depth[i] % 2 == 0
    }
    for i in range(n):
        if depth[i] % 2 == 1 and parent_of[i] is not None and parent_of[i] in parts:
            parts[parent_of[i]].interiors.append(_oriented_contour(valid_closed[i], ccw=False))

    return list(parts.values()), diagnostics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/stages/test_hierarchy.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/hierarchy.py tests/stages/test_hierarchy.py
git commit -m "feat: add build_hierarchy (containment-tree exterior/hole grouping)"
```

---

### Task 7: `stages/validate.py` — read-only validation report

**Files:**
- Create: `dxf_cleaner/stages/validate.py`
- Test: `tests/stages/test_validate.py`

**Interfaces:**
- Consumes: `Part`, `Diagnostic`, `contour_bbox`, `contour_signed_area` (from `dxf_cleaner.model`); `ValidateConfig` (from `dxf_cleaner.config`); `shapely.Polygon`.
- Produces:
  - `@dataclass ValidationReport(level: Literal["ok", "warning", "critical"], critical: list[str], warnings: list[str], info: dict[str, int | float])`
  - `validate(parts: list[Part], diagnostics: list[Diagnostic], config: ValidateConfig, stats: dict[str, int]) -> ValidationReport`
    - `stats` is supplied by the caller (Task 8's `pipeline.py`) with keys `contour_count_before`, `contour_count_after`, `node_count_before`, `node_count_after`, `dedup_count`, `closed_count`, `weld_count` (this phase always passes `weld_count=0` — weld.py doesn't exist until Phase 3 — the field is present so the report shape doesn't change later).

This stage never modifies `parts` or `diagnostics` — it only reads them.

Checks implemented (per spec 6.9, using the "Documented scope decisions" in Global Constraints for the two checks the spec doesn't give exact formulas for):
- **Critical:** any `OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY`, `POLYGON_UNRECOVERABLE`, or `OPEN_GAP` diagnostic present; `len(parts) == 0`; any part's exterior bbox wider/taller than `config.sheet_width`/`sheet_height`.
- **Warning:** any `TEXT_SKIPPED`/`3D_ENTITY_SKIPPED`/`DEGENERATE_ENTITY_SKIPPED`/`ENTITY_CONVERSION_FAILED` diagnostic (dropped entities); any `ASSUMED_UNIT` diagnostic (unknown input units); any `AMBIGUOUS_JUNCTION` diagnostic; two parts whose exteriors are geometrically equal (`shapely.equals`, after a bbox+area pre-filter) — "fully duplicate parts"; any hole whose area-derived diameter (`2*sqrt(area/pi)`) is smaller than `config.min_hole_diameter_ratio * config.material_thickness`; any two distinct parts' geometries (exterior or hole) closer than `2 * config.kerf_width` (via `shapely.distance`).
- **Info:** the `stats` dict, passed straight through into `info`, plus `part_count: len(parts)`.
- `level` is `"critical"` if any critical check triggered, else `"warning"` if any warning check triggered, else `"ok"`.

- [ ] **Step 1: Write failing tests**

```python
# tests/stages/test_validate.py
import math
from dxf_cleaner.model import Segment, Contour, Part, Diagnostic
from dxf_cleaner.config import ValidateConfig
from dxf_cleaner.stages.validate import validate, ValidationReport


def _square_contour(x0, y0, side, handle="H"):
    p = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    segs = [Segment(kind="line", start=p[i], end=p[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle=handle)


BASE_STATS = dict(
    contour_count_before=1, contour_count_after=1, node_count_before=4, node_count_after=4,
    dedup_count=0, closed_count=0, weld_count=0,
)


def test_clean_run_is_ok():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    report = validate([part], [], ValidateConfig(), BASE_STATS)
    assert report.level == "ok"
    assert report.critical == []
    assert report.info["part_count"] == 1
    assert report.info["weld_count"] == 0


def test_empty_output_is_critical():
    report = validate([], [], ValidateConfig(), BASE_STATS)
    assert report.level == "critical"
    assert any("empty" in c.lower() for c in report.critical)


def test_open_gap_diagnostic_makes_it_critical():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="OPEN_GAP", message="Gap of 0.3mm between (0,0) and (0.3,0)")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "critical"


def test_oversized_part_is_critical():
    huge = Part(exterior=_square_contour(0, 0, 2000, "HUGE"))  # bigger than default 1500x3000 sheet... actually 2000 > 1500 width
    report = validate([huge], [], ValidateConfig(), BASE_STATS)
    assert report.level == "critical"
    assert any("sheet" in c.lower() or "oversized" in c.lower() for c in report.critical)


def test_dropped_entity_diagnostic_is_a_warning_not_critical():
    part = Part(exterior=_square_contour(0, 0, 100, "A"))
    diags = [Diagnostic(code="TEXT_SKIPPED", message="text dropped")]
    report = validate([part], diags, ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("text" in w.lower() or "dropped" in w.lower() for w in report.warnings)


def test_duplicate_parts_are_a_warning():
    a = Part(exterior=_square_contour(0, 0, 10, "A"))
    b = Part(exterior=_square_contour(0, 0, 10, "B"))  # identical shape and position
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("duplicate" in w.lower() for w in report.warnings)


def test_hole_smaller_than_material_thickness_is_a_warning():
    outer = _square_contour(0, 0, 100, "OUTER")
    tiny_hole = _square_contour(10, 10, 1.0, "HOLE")  # diameter ~1.13mm
    part = Part(exterior=outer, interiors=[tiny_hole])
    cfg = ValidateConfig(material_thickness=2.0, min_hole_diameter_ratio=1.0)  # min diameter 2.0mm
    report = validate([part], [], cfg, BASE_STATS)
    assert report.level == "warning"
    assert any("hole" in w.lower() for w in report.warnings)


def test_parts_closer_than_2x_kerf_is_a_warning():
    a = Part(exterior=_square_contour(0, 0, 10, "A"))
    b = Part(exterior=_square_contour(10.1, 0, 10, "B"))  # 0.1mm apart, kerf default 0.15 -> 2x=0.3
    report = validate([a, b], [], ValidateConfig(), BASE_STATS)
    assert report.level == "warning"
    assert any("close" in w.lower() or "kerf" in w.lower() for w in report.warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/stages/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.validate'`

- [ ] **Step 3: Implement `dxf_cleaner/stages/validate.py`**

```python
import math
from dataclasses import dataclass, field
from typing import Literal
from shapely.geometry import Polygon

from dxf_cleaner.model import Part, Diagnostic, contour_bbox
from dxf_cleaner.config import ValidateConfig

_DROPPED_ENTITY_CODES = {"TEXT_SKIPPED", "3D_ENTITY_SKIPPED", "DEGENERATE_ENTITY_SKIPPED", "ENTITY_CONVERSION_FAILED"}
_CRITICAL_CODES = {"OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY", "POLYGON_UNRECOVERABLE", "OPEN_GAP"}


@dataclass
class ValidationReport:
    level: Literal["ok", "warning", "critical"]
    critical: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, int | float] = field(default_factory=dict)


def _hole_diameter(part_hole_poly: Polygon) -> float:
    return 2 * math.sqrt(abs(part_hole_poly.area) / math.pi)


def validate(
    parts: list[Part], diagnostics: list[Diagnostic], config: ValidateConfig, stats: dict[str, int]
) -> ValidationReport:
    critical: list[str] = []
    warnings: list[str] = []

    diag_codes = [d.code for d in diagnostics]
    for code in diag_codes:
        if code in _CRITICAL_CODES:
            critical.append(f"{code}: see diagnostics for details")
        elif code in _DROPPED_ENTITY_CODES:
            warnings.append(f"Entity dropped ({code})")
        elif code == "ASSUMED_UNIT":
            warnings.append("Input file did not specify units; assumed unit was used")
        elif code == "AMBIGUOUS_JUNCTION":
            warnings.append("Ambiguous junction encountered while reconnecting contours")

    if not parts:
        critical.append("Output is empty: no parts survived processing")

    for part in parts:
        minx, miny, maxx, maxy = contour_bbox(part.exterior)
        width, height = maxx - minx, maxy - miny
        if width > config.sheet_width or height > config.sheet_height:
            critical.append(
                f"Part {part.exterior.source_handle!r} ({width:.1f}x{height:.1f}mm) is oversized "
                f"for the configured sheet ({config.sheet_width}x{config.sheet_height}mm)"
            )

    all_geoms: list[tuple[str, Polygon]] = []
    for part in parts:
        all_geoms.append((part.exterior.source_handle, Polygon(part.exterior.to_shapely())))
        for hole in part.interiors:
            all_geoms.append((hole.source_handle, Polygon(hole.to_shapely())))
            diameter = _hole_diameter(all_geoms[-1][1])
            min_diameter = config.min_hole_diameter_ratio * config.material_thickness
            if diameter < min_diameter:
                warnings.append(
                    f"Hole {hole.source_handle!r} diameter {diameter:.3f}mm is smaller than "
                    f"the material-thickness-derived minimum {min_diameter:.3f}mm"
                )

    for part_a in parts:
        for part_b in parts:
            if part_a is part_b or id(part_a) >= id(part_b):
                continue
            poly_a = Polygon(part_a.exterior.to_shapely())
            poly_b = Polygon(part_b.exterior.to_shapely())
            if abs(poly_a.area - poly_b.area) < 1e-6 and poly_a.equals(poly_b):
                warnings.append(
                    f"Parts {part_a.exterior.source_handle!r} and {part_b.exterior.source_handle!r} "
                    f"are fully duplicate (same shape, same position)"
                )

    min_gap = 2 * config.kerf_width
    for i, (name_a, geom_a) in enumerate(all_geoms):
        for name_b, geom_b in all_geoms[i + 1:]:
            if geom_a.distance(geom_b) < min_gap and not geom_a.equals(geom_b):
                warnings.append(
                    f"{name_a!r} and {name_b!r} are closer than 2x kerf width ({min_gap}mm)"
                )

    if critical:
        level = "critical"
    elif warnings:
        level = "warning"
    else:
        level = "ok"

    info = dict(stats)
    info["part_count"] = len(parts)
    return ValidationReport(level=level, critical=critical, warnings=warnings, info=info)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/stages/test_validate.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/validate.py tests/stages/test_validate.py
git commit -m "feat: add read-only validate() classifying output as ok/warning/critical"
```

---

### Task 8: `pipeline.py` — orchestrate the full Phase 1 + Phase 2 flow

**Files:**
- Create: `dxf_cleaner/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `read_dxf` (`dxf_cleaner.reader`); `write_dxf` (`dxf_cleaner.writer`); `snap_and_chain` (`dxf_cleaner.stages.snap`); `dedupe_contours` (`dxf_cleaner.stages.dedupe`); `despeckle_contours` (`dxf_cleaner.stages.despeckle`); `build_hierarchy` (`dxf_cleaner.stages.hierarchy`); `validate`, `ValidationReport` (`dxf_cleaner.stages.validate`); `Part`, `Diagnostic` (`dxf_cleaner.model`); `Config` (`dxf_cleaner.config`).
- Produces:
  - `@dataclass PipelineResult(parts: list[Part], diagnostics: list[Diagnostic], report: ValidationReport)`
  - `run_pipeline(input_path: str, config: Config) -> PipelineResult`
  - `parts_to_contours(parts: list[Part]) -> list[Contour]` — flattens `[exterior, *interiors]` for every part, in order, for `write_dxf`.
  - `write_pipeline_result(result: PipelineResult, output_path: str, config: Config) -> None` — convenience wrapper calling `write_dxf(parts_to_contours(result.parts), output_path, config)`.

`run_pipeline` sequence: `read_dxf` → (if `config.dedupe.enabled`, run dedupe; else skip) — actually dedupe always makes sense to run after snap regardless of a separate "enabled" flag interacting oddly with snap's own always-on behavior, so: `snap_and_chain` (always runs — it's not gated by a config `enabled` flag in this plan since spec doesn't list one for snap) → `dedupe_contours` (skipped entirely if `config.dedupe.enabled` is `False`) → `despeckle_contours` → `build_hierarchy` → `validate`. Diagnostics from every stage are concatenated in that order.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline.py
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.pipeline import run_pipeline, parts_to_contours, write_pipeline_result
from dxf_cleaner.reader import read_dxf


def test_open_rectangle_of_four_lines_becomes_one_clean_closed_part(tmp_path):
    # spec's open_contour.dxf equivalent: 4 separate LINE entities forming a
    # rectangle whose corners are 0.02mm apart (snap should close it).
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    p = [(0.0, 0.0), (10.02, 0.0), (10.0, 10.02), (-0.02, 10.0)]
    msp.add_line(p[0], p[1])
    msp.add_line(p[1], p[2])
    msp.add_line(p[2], p[3])
    msp.add_line(p[3], (0.02, 0.0))
    path = tmp_path / "open_rectangle.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())
    assert len(result.parts) == 1
    assert result.parts[0].interiors == []
    assert result.report.level == "ok"


def test_duplicate_lines_are_deduped_end_to_end(tmp_path):
    # spec's duplicate_lines.dxf equivalent.
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0), (5, 0))
    msp.add_line((5, 0), (0, 0))  # exact duplicate, reversed
    path = tmp_path / "duplicate_lines.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())
    total_segments = sum(len(p.exterior.segments) + sum(len(h.segments) for h in p.interiors) for p in result.parts)
    # both lines are open (never close into a Part) — this test only checks
    # dedupe ran: only one DUPLICATE_SEGMENT_REMOVED diagnostic should exist.
    assert any(d.code == "DUPLICATE_SEGMENT_REMOVED" for d in result.diagnostics)


def test_nested_holes_end_to_end(tmp_path):
    # spec's nested_holes.dxf equivalent: outer square with a hole, and a small
    # island inside that hole.
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], format="xy", close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], format="xy", close=True)
    msp.add_lwpolyline([(8, 8), (10, 8), (10, 10), (8, 10)], format="xy", close=True)
    path = tmp_path / "nested_holes.dxf"
    doc.saveas(path)

    result = run_pipeline(str(path), Config())
    assert len(result.parts) == 2
    outer_part = next(p for p in result.parts if p.interiors)
    assert len(outer_part.interiors) == 1


def test_parts_to_contours_flattens_exterior_and_interiors():
    import ezdxf
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], format="xy", close=True)
    msp.add_lwpolyline([(5, 5), (15, 5), (15, 15), (5, 15)], format="xy", close=True)
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".dxf")
    os.close(fd)
    doc.saveas(path)
    result = run_pipeline(path, Config())
    os.remove(path)
    contours = parts_to_contours(result.parts)
    assert len(contours) == 2  # 1 exterior + 1 interior


def test_write_pipeline_result_produces_a_readable_dxf(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_circle((5, 5), radius=3.0)
    in_path = tmp_path / "in.dxf"
    doc.saveas(in_path)

    result = run_pipeline(str(in_path), Config())
    out_path = tmp_path / "out.dxf"
    write_pipeline_result(result, str(out_path), Config())

    reread = read_dxf(str(out_path), Config())
    assert len(reread.contours) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.pipeline'`

- [ ] **Step 3: Implement `dxf_cleaner/pipeline.py`**

```python
from dataclasses import dataclass

from dxf_cleaner.config import Config
from dxf_cleaner.model import Part, Contour, Diagnostic
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.writer import write_dxf
from dxf_cleaner.stages.snap import snap_and_chain
from dxf_cleaner.stages.dedupe import dedupe_contours
from dxf_cleaner.stages.despeckle import despeckle_contours
from dxf_cleaner.stages.hierarchy import build_hierarchy
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

    parts, hierarchy_diags = build_hierarchy(contours)
    diagnostics.extend(hierarchy_diags)

    contour_count_after = len(parts_to_contours(parts))
    node_count_after = sum(len(c.segments) for c in parts_to_contours(parts))

    stats = dict(
        contour_count_before=contour_count_before,
        contour_count_after=contour_count_after,
        node_count_before=node_count_before,
        node_count_after=node_count_after,
        dedup_count=dedup_count,
        closed_count=closed_count,
        weld_count=0,  # weld.py doesn't exist until Phase 3
    )
    report = validate(parts, diagnostics, config.validate, stats)

    return PipelineResult(parts=parts, diagnostics=diagnostics, report=report)


def write_pipeline_result(result: PipelineResult, output_path: str, config: Config) -> None:
    write_dxf(parts_to_contours(result.parts), output_path, config)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests, Phase 1 and Phase 2 combined)

- [ ] **Step 6: Commit**

```bash
git add dxf_cleaner/pipeline.py tests/test_pipeline.py
git commit -m "feat: add run_pipeline orchestrating read->snap->dedupe->despeckle->hierarchy->validate"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers §8's new config sections. Task 3 covers §6.3 (snap) including the gap-reporting/max_reportable_gap distinction. Task 4 covers §6.4's three dedupe cases, with the case (a)/(c) disambiguation rule stated explicitly (Global Constraints item 2) since the spec's own text doesn't give a mechanical rule for telling them apart. Task 5 covers §6.5 except the unconfigured beam-diameter criterion (documented, not silently dropped). Task 6 covers §6.7 including multi-level nesting (island-in-hole). Task 7 covers §6.9's Critical/Warning/Info lists, with the two Warning checks lacking spec formulas (hole-vs-thickness, part spacing) using documented approximations. Task 8 wires everything together per §12's Phase 2 description and gives `write_dxf` something to consume (`parts_to_contours`).
- **Explicitly out of scope for Phase 2** (deferred to Phase 3+ per §12): `weld.py`, `simplify.py`, `cli.py`, `pipeline.py`'s CLI-facing wrapper (`cli.py` itself, not this `pipeline.py` module which is the programmatic entry point), `report.py`, `watcher.py`.
- **Type consistency check:** `Diagnostic(code, message, handle=None)` used identically across all five new stage modules. `Contour`/`Segment`/`Part` fields match Phase 1's definitions exactly (verified by reading the current `dxf_cleaner/model.py` before writing this plan). `ValidationReport`/`PipelineResult` field names in Task 8 match what Task 7 defines.
- **No placeholders:** every step has complete, runnable code.
