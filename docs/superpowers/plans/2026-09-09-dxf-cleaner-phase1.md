# DXF Cleaner — Phase 1 (Core: read → write, dimensions unchanged) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read a DXF file into the hybrid `Segment`/`Contour` model, then write it back out to a new DXF file, with dimensions unchanged (<0.1% area/bbox drift) and arcs/circles preserved as arcs/circles (not flattened to polylines). No error-fixing (snap/dedupe/weld/etc.) yet — that is Phase 2/3.

**Architecture:** A hybrid geometry model (`model.py`) keeps `Segment`s as either `line` or `arc` (never losing arc identity to Shapely discretization). `reader.py` orchestrates: determine unit scale from `$INSUNITS`, recursively explode `INSERT` blocks and filter out unsupported entities (`stages/explode.py`), convert each surviving entity to a `Contour` (LINE/ARC/CIRCLE/LWPOLYLINE/POLYLINE directly; SPLINE/ELLIPSE via chord-tolerance flattening with circular-arc re-detection in `stages/flatten.py`), then scale all contours to mm. `writer.py` writes contours back to a fresh DXF: full-circle contours become native `CIRCLE` entities, everything else becomes `LWPOLYLINE` with bulge-encoded arcs. A regression test harness compares pre/post area and bounding box.

**Tech Stack:** Python 3.11+, ezdxf 1.4.x, shapely 2.x, numpy, pydantic + PyYAML for config, pytest.

**Spec:** [docs/superpowers/specs/2026-09-09-dxf-cleaner-design.md](../specs/2026-09-09-dxf-cleaner-design.md)

## Global Constraints

- Python 3.11+, use `from __future__ import annotations` is not required but type hints should use built-in generics (`list[X]`, `X | None`).
- Shapely is used **only** for computation (area/bbox helpers, future weld/dedupe stages). Writing a DXF file must always walk original `Segment` data, never a Shapely-discretized geometry.
- All internal coordinates are 2D `(x, y)` tuples of `float`, in millimeters, Z always 0. All angles inside the model are radians; DXF `ARC` start/end angles are in **degrees** — convert at the reader/writer boundary.
- Chord tolerance default: 0.02mm (config `flatten.chord_tolerance`). Never hard-code; always take from `Config`.
- Output DXF version: `AC1015` (R2000), single layer named from config (default `CUT`), `$INSUNITS = 4` (mm), `$EXTMIN`/`$EXTMAX` set correctly, all Z = 0.
- Never silently change geometry size. Every stage that discretizes or reconstructs geometry must be covered by the regression test (Task 14).
- Confirmed library APIs (verified against ezdxf 1.4.4 / shapely 2.1.2 in this session — rely on these, do not hand-roll equivalents):
  - `ezdxf.math.bulge_to_arc(start_point, end_point, bulge) -> (center: Vec2, start_angle: float, end_angle: float, radius: float)` — angles in **radians**.
  - `ezdxf.math.arc_to_bulge(center, start_angle, end_angle, radius) -> (start_point: Vec2, end_point: Vec2, bulge: float)` — angles in **radians**, computes bulge for CCW travel from `start_angle` to `end_angle`.
  - `entity.virtual_entities()` on an `Insert` applies that insert's transform to its block content **but does not recurse** into nested `INSERT`s — nested inserts come back as `INSERT` entities (already correctly positioned) and must be exploded again manually.
  - `Spline.flattening(distance, segments=4)` / `Ellipse.flattening(distance, segments=8)` return `Iterator[Vec3]` of points approximating the curve within `distance` (sagitta-style tolerance) of the true curve.
  - `LWPolyline.get_points('xyb')` returns `[(x, y, bulge), ...]`.
  - `ezdxf.new("AC1015")` is accepted directly (no alias mapping needed).
  - `ARC.dxf.start_angle` / `end_angle` are in **degrees**.

---

## File Structure

```
dxf_cleaner/
├── __init__.py
├── model.py            # Segment, Contour, Part, Diagnostic + geometry helpers
├── config.py            # pydantic Config schema (Phase 1 subset) + load_config()
├── reader.py            # read_dxf(): orchestrates unit scale, explode, convert, flatten
├── writer.py            # write_dxf(): Contour list -> DXF file
└── stages/
    ├── __init__.py
    ├── explode.py        # recursive INSERT explode + unsupported-entity filtering
    └── flatten.py        # SPLINE/ELLIPSE -> Segment list, with circular-arc detection

tests/
├── conftest.py
├── test_model.py
├── test_config.py
├── test_reader.py
├── test_writer.py
├── test_roundtrip_regression.py
└── stages/
    ├── test_explode.py
    └── test_flatten.py

pyproject.toml
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `dxf_cleaner/__init__.py`
- Create: `dxf_cleaner/stages/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/stages/__init__.py`
- Create: `pytest.ini`

**Interfaces:**
- Produces: an installable package `dxf_cleaner` and a working `pytest` command.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "dxf-cleaner"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "ezdxf>=1.4,<2.0",
    "shapely>=2.0,<3.0",
    "numpy>=1.26",
    "pydantic>=2.0,<3.0",
    "PyYAML>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["dxf_cleaner*"]
```

- [ ] **Step 2: Create empty package files**

```bash
mkdir -p dxf_cleaner/stages tests/stages
touch dxf_cleaner/__init__.py dxf_cleaner/stages/__init__.py tests/__init__.py tests/stages/__init__.py
```

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 4: Install the project in editable mode with dev deps**

Run: `pip install -e ".[dev]"`
Expected: installs without error.

- [ ] **Step 5: Verify pytest runs (no tests yet, should report 0 collected)**

Run: `pytest -q`
Expected: `no tests ran` (exit code 5) — this is fine, confirms pytest is wired up.

- [ ] **Step 6: Commit**

```bash
git init
git add pyproject.toml pytest.ini dxf_cleaner tests
git commit -m "chore: project scaffolding for dxf-cleaner"
```

---

### Task 2: `model.py` — Segment, Contour, Part, Diagnostic

**Files:**
- Create: `dxf_cleaner/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Produces:
  - `Point = tuple[float, float]`
  - `Segment(kind: Literal["line","arc"], start: Point, end: Point, center: Point|None=None, radius: float|None=None, ccw: bool|None=None)`
  - `Contour(segments: list[Segment], is_closed: bool, source_layer: str, source_handle: str)`
  - `Part(exterior: Contour, interiors: list[Contour] = [])`
  - `Diagnostic(code: str, message: str, handle: str|None=None)`
  - `scale_contour(contour: Contour, factor: float) -> Contour`

- [ ] **Step 1: Write failing test for dataclasses and `scale_contour`**

```python
# tests/test_model.py
from dxf_cleaner.model import Segment, Contour, Part, Diagnostic, scale_contour


def test_segment_line_defaults_are_none():
    seg = Segment(kind="line", start=(0, 0), end=(1, 0))
    assert seg.center is None
    assert seg.radius is None
    assert seg.ccw is None


def test_contour_holds_segments_and_metadata():
    seg = Segment(kind="line", start=(0, 0), end=(1, 0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="ABC")
    assert contour.segments == [seg]
    assert contour.source_handle == "ABC"


def test_part_defaults_to_no_interiors():
    ext = Contour(segments=[], is_closed=True, source_layer="0", source_handle="1")
    part = Part(exterior=ext)
    assert part.interiors == []


def test_scale_contour_scales_line_segment():
    seg = Segment(kind="line", start=(1.0, 2.0), end=(3.0, 4.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    scaled = scale_contour(contour, 25.4)
    assert scaled.segments[0].start == (25.4, 50.8)
    assert scaled.segments[0].end == (76.2, 101.6)
    # original untouched
    assert contour.segments[0].start == (1.0, 2.0)


def test_scale_contour_scales_arc_radius_and_center():
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    scaled = scale_contour(contour, 2.0)
    assert scaled.segments[0].radius == 2.0
    assert scaled.segments[0].center == (0.0, 0.0)
    assert scaled.segments[0].ccw is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.model'`

- [ ] **Step 3: Implement `dxf_cleaner/model.py`**

```python
from dataclasses import dataclass, field
from typing import Literal

Point = tuple[float, float]


@dataclass
class Segment:
    """One piece of a Contour. Keeps its true geometric nature (line or arc)."""
    kind: Literal["line", "arc"]
    start: Point
    end: Point
    center: Point | None = None
    radius: float | None = None
    ccw: bool | None = None


@dataclass
class Contour:
    segments: list[Segment]
    is_closed: bool
    source_layer: str
    source_handle: str


@dataclass
class Part:
    exterior: Contour
    interiors: list[Contour] = field(default_factory=list)


@dataclass
class Diagnostic:
    code: str
    message: str
    handle: str | None = None


def _scale_point(p: Point, factor: float) -> Point:
    return (p[0] * factor, p[1] * factor)


def scale_contour(contour: Contour, factor: float) -> Contour:
    """Return a new Contour with every coordinate multiplied by `factor`."""
    new_segments = [
        Segment(
            kind=seg.kind,
            start=_scale_point(seg.start, factor),
            end=_scale_point(seg.end, factor),
            center=_scale_point(seg.center, factor) if seg.center is not None else None,
            radius=seg.radius * factor if seg.radius is not None else None,
            ccw=seg.ccw,
        )
        for seg in contour.segments
    ]
    return Contour(
        segments=new_segments,
        is_closed=contour.is_closed,
        source_layer=contour.source_layer,
        source_handle=contour.source_handle,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/model.py tests/test_model.py
git commit -m "feat: add Segment/Contour/Part/Diagnostic model and scale_contour"
```

---

### Task 3: `model.py` — arc discretization (`discretize_arc`, `discretize_contour`, `to_shapely`)

**Files:**
- Modify: `dxf_cleaner/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: `Segment`, `Contour` from Task 2.
- Produces:
  - `discretize_arc(segment: Segment, tolerance: float) -> list[Point]`
  - `discretize_contour(contour: Contour, tolerance: float) -> list[Point]`
  - `Contour.to_shapely(self, arc_tolerance: float = 0.02) -> shapely.geometry.LinearRing | shapely.geometry.LineString`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_model.py
import math
from shapely.geometry import LinearRing, LineString
from dxf_cleaner.model import Segment, Contour, discretize_arc, discretize_contour


def test_discretize_arc_quarter_circle_endpoints_match():
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    points = discretize_arc(seg, tolerance=0.01)
    assert points[0] == (1.0, 0.0)
    assert math.isclose(points[-1][0], 0.0, abs_tol=1e-9)
    assert math.isclose(points[-1][1], 1.0, abs_tol=1e-9)


def test_discretize_arc_respects_chord_tolerance():
    # Large radius -> few points needed to stay within tolerance.
    seg = Segment(kind="arc", start=(100.0, 0.0), end=(0.0, 100.0), center=(0.0, 0.0), radius=100.0, ccw=True)
    points = discretize_arc(seg, tolerance=0.02)
    # Verify every point is within tolerance of the true circle.
    for x, y in points:
        dist = math.hypot(x, y)
        assert abs(dist - 100.0) < 1e-6  # points sit exactly on the circle by construction
    # Verify the max sagitta (midpoint deviation) between consecutive samples respects tolerance.
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        mid = ((x1 + x2) / 2, (y1 + y2) / 2)
        mid_dist_to_center = math.hypot(*mid)
        sagitta = 100.0 - mid_dist_to_center
        assert sagitta <= 0.02 + 1e-9


def test_discretize_arc_clockwise():
    seg = Segment(kind="arc", start=(0.0, 1.0), end=(1.0, 0.0), center=(0.0, 0.0), radius=1.0, ccw=False)
    points = discretize_arc(seg, tolerance=0.01)
    assert points[0] == (0.0, 1.0)
    assert math.isclose(points[-1][0], 1.0, abs_tol=1e-9)
    assert math.isclose(points[-1][1], 0.0, abs_tol=1e-9)


def test_discretize_contour_line_and_arc_chain_dedupes_shared_vertex():
    line = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    arc = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    contour = Contour(segments=[line, arc], is_closed=False, source_layer="0", source_handle="1")
    points = discretize_contour(contour, tolerance=0.01)
    # (1.0, 0.0) must appear exactly once (shared between line end and arc start)
    assert points.count((1.0, 0.0)) == 1


def test_contour_to_shapely_closed_gives_linear_ring():
    seg1 = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    seg2 = Segment(kind="line", start=(1.0, 0.0), end=(0.0, 0.0))
    contour = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="1")
    ring = contour.to_shapely(arc_tolerance=0.01)
    assert isinstance(ring, LinearRing)


def test_contour_to_shapely_open_gives_line_string():
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    line = contour.to_shapely(arc_tolerance=0.01)
    assert isinstance(line, LineString)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model.py -v -k "discretize or to_shapely"`
Expected: FAIL with `ImportError: cannot import name 'discretize_arc'`

- [ ] **Step 3: Implement in `dxf_cleaner/model.py`**

Add imports and functions:

```python
import math
from shapely.geometry import LinearRing, LineString


def discretize_arc(segment: Segment, tolerance: float) -> list[Point]:
    """Sample points along an arc segment so consecutive samples deviate from the
    true arc by at most `tolerance` (chord/sagitta tolerance)."""
    cx, cy = segment.center
    r = segment.radius
    a0 = math.atan2(segment.start[1] - cy, segment.start[0] - cx)
    a1 = math.atan2(segment.end[1] - cy, segment.end[0] - cx)
    two_pi = 2 * math.pi
    if segment.ccw:
        sweep = (a1 - a0) % two_pi
        if sweep == 0:
            sweep = two_pi
    else:
        sweep = -((a0 - a1) % two_pi)
        if sweep == 0:
            sweep = -two_pi

    tol = min(tolerance, r * 0.999)
    max_step = 2 * math.acos(1 - tol / r)
    steps = max(1, math.ceil(abs(sweep) / max_step))

    points: list[Point] = []
    for i in range(steps + 1):
        a = a0 + sweep * i / steps
        points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def discretize_contour(contour: Contour, tolerance: float) -> list[Point]:
    """Flatten every segment into a single ordered point list, without duplicating
    the shared vertex between consecutive segments."""
    points: list[Point] = []
    for i, seg in enumerate(contour.segments):
        seg_points = discretize_arc(seg, tolerance) if seg.kind == "arc" else [seg.start, seg.end]
        points.extend(seg_points if i == 0 else seg_points[1:])
    return points
```

Add a method to `Contour` (dataclasses support methods):

```python
@dataclass
class Contour:
    segments: list[Segment]
    is_closed: bool
    source_layer: str
    source_handle: str

    def to_shapely(self, arc_tolerance: float = 0.02) -> LinearRing | LineString:
        """Discretize for computation only. NEVER use this output to write a DXF file."""
        points = discretize_contour(self, arc_tolerance)
        return LinearRing(points) if self.is_closed else LineString(points)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/model.py tests/test_model.py
git commit -m "feat: add chord-tolerance arc discretization and Contour.to_shapely"
```

---

### Task 4: `model.py` — bbox, signed area, full-circle detection

**Files:**
- Modify: `dxf_cleaner/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: `discretize_contour` from Task 3.
- Produces:
  - `contour_bbox(contour: Contour, arc_tolerance: float = 0.02) -> tuple[float, float, float, float]` (minx, miny, maxx, maxy)
  - `contour_signed_area(contour: Contour, arc_tolerance: float = 0.02) -> float` (positive = CCW)
  - `contour_as_full_circle(contour: Contour, tolerance: float = 1e-6) -> tuple[Point, float] | None`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_model.py
from dxf_cleaner.model import contour_bbox, contour_signed_area, contour_as_full_circle


def _unit_square_ccw() -> Contour:
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    segs = [Segment(kind="line", start=pts[i], end=pts[(i + 1) % 4]) for i in range(4)]
    return Contour(segments=segs, is_closed=True, source_layer="0", source_handle="1")


def test_contour_bbox_unit_square():
    assert contour_bbox(_unit_square_ccw()) == (0.0, 0.0, 1.0, 1.0)


def test_contour_signed_area_ccw_is_positive():
    assert math.isclose(contour_signed_area(_unit_square_ccw()), 1.0, abs_tol=1e-9)


def test_contour_signed_area_cw_is_negative():
    square = _unit_square_ccw()
    square.segments = list(reversed([
        Segment(kind="line", start=s.end, end=s.start) for s in square.segments
    ]))
    assert math.isclose(contour_signed_area(square), -1.0, abs_tol=1e-9)


def test_contour_as_full_circle_detects_two_arc_circle():
    center = (0.0, 0.0)
    radius = 5.0
    seg1 = Segment(kind="arc", start=(5.0, 0.0), end=(-5.0, 0.0), center=center, radius=radius, ccw=True)
    seg2 = Segment(kind="arc", start=(-5.0, 0.0), end=(5.0, 0.0), center=center, radius=radius, ccw=True)
    contour = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="1")
    result = contour_as_full_circle(contour)
    assert result is not None
    got_center, got_radius = result
    assert math.isclose(got_center[0], 0.0, abs_tol=1e-9)
    assert math.isclose(got_radius, 5.0, abs_tol=1e-9)


def test_contour_as_full_circle_rejects_non_circle():
    assert contour_as_full_circle(_unit_square_ccw()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model.py -v -k "bbox or signed_area or full_circle"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement in `dxf_cleaner/model.py`**

```python
def contour_bbox(contour: Contour, arc_tolerance: float = 0.02) -> tuple[float, float, float, float]:
    points = discretize_contour(contour, arc_tolerance)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def contour_signed_area(contour: Contour, arc_tolerance: float = 0.02) -> float:
    """Shoelace formula over the discretized boundary. Positive area = CCW winding."""
    points = discretize_contour(contour, arc_tolerance)
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def contour_as_full_circle(contour: Contour, tolerance: float = 1e-6) -> tuple[Point, float] | None:
    """If this closed 2-arc contour is exactly a full circle (as produced when reading
    a CIRCLE entity, or re-detected after flatten/weld), return (center, radius)."""
    if not contour.is_closed or len(contour.segments) != 2:
        return None
    s0, s1 = contour.segments
    if s0.kind != "arc" or s1.kind != "arc":
        return None
    if s0.center is None or s1.center is None or s0.radius is None or s1.radius is None:
        return None
    if math.dist(s0.center, s1.center) > tolerance:
        return None
    if abs(s0.radius - s1.radius) > tolerance:
        return None
    if math.dist(s0.end, s1.start) > tolerance or math.dist(s1.end, s0.start) > tolerance:
        return None
    return (s0.center, s0.radius)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/model.py tests/test_model.py
git commit -m "feat: add contour bbox, signed area, and full-circle detection helpers"
```

---

### Task 5: `config.py` — Config schema (Phase 1 subset)

**Files:**
- Create: `dxf_cleaner/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `InputConfig(assumed_unit: Literal["mm", "inch"] = "mm")`
  - `FlattenConfig(chord_tolerance: float = 0.02, detect_circular_splines: bool = True)`
  - `OutputConfig(dxf_version: str = "AC1015", layer_name: str = "CUT", preserve_arcs: bool = True)`
  - `Config(input: InputConfig, flatten: FlattenConfig, output: OutputConfig)`
  - `load_config(path: str | None) -> Config`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import pytest
from dxf_cleaner.config import Config, load_config


def test_default_config_values():
    cfg = Config()
    assert cfg.input.assumed_unit == "mm"
    assert cfg.flatten.chord_tolerance == 0.02
    assert cfg.flatten.detect_circular_splines is True
    assert cfg.output.dxf_version == "AC1015"
    assert cfg.output.layer_name == "CUT"


def test_load_config_none_path_returns_defaults():
    cfg = load_config(None)
    assert cfg == Config()


def test_load_config_from_yaml_overrides_defaults(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "input:\n  assumed_unit: inch\n"
        "flatten:\n  chord_tolerance: 0.05\n"
    )
    cfg = load_config(str(yaml_path))
    assert cfg.input.assumed_unit == "inch"
    assert cfg.flatten.chord_tolerance == 0.05
    # untouched sections keep defaults
    assert cfg.output.layer_name == "CUT"


def test_config_rejects_invalid_assumed_unit():
    with pytest.raises(Exception):
        Config(input={"assumed_unit": "cm"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.config'`

- [ ] **Step 3: Implement `dxf_cleaner/config.py`**

```python
from typing import Literal
import yaml
from pydantic import BaseModel


class InputConfig(BaseModel):
    assumed_unit: Literal["mm", "inch"] = "mm"


class FlattenConfig(BaseModel):
    chord_tolerance: float = 0.02
    detect_circular_splines: bool = True


class OutputConfig(BaseModel):
    dxf_version: str = "AC1015"
    layer_name: str = "CUT"
    preserve_arcs: bool = True


class Config(BaseModel):
    input: InputConfig = InputConfig()
    flatten: FlattenConfig = FlattenConfig()
    output: OutputConfig = OutputConfig()


def load_config(path: str | None) -> Config:
    if path is None:
        return Config()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(**data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/config.py tests/test_config.py
git commit -m "feat: add pydantic Config schema and YAML loader"
```

---

### Task 6: `stages/explode.py` — recursive INSERT explode + entity filtering

**Files:**
- Create: `dxf_cleaner/stages/explode.py`
- Test: `tests/stages/test_explode.py`
- Test: `tests/conftest.py`

**Interfaces:**
- Consumes: `Diagnostic` from `dxf_cleaner.model` (Task 2), `Config` from `dxf_cleaner.config` (Task 5).
- Produces: `explode_and_filter(entities: list, config: Config) -> tuple[list, list[Diagnostic]]`
  - Input `entities` is any iterable of `ezdxf` `DXFGraphic` entities (e.g. `doc.modelspace()`).
  - Output list contains only entities of type `LINE`, `ARC`, `CIRCLE`, `LWPOLYLINE`, `POLYLINE`, `SPLINE`, `ELLIPSE` (INSERTs are recursively resolved into these; everything else is dropped, with a `Diagnostic` for the types the spec says must warn).

- [ ] **Step 1: Create shared test fixture helper `tests/conftest.py`**

```python
import ezdxf
import pytest


@pytest.fixture
def new_doc():
    return ezdxf.new("R2000")
```

- [ ] **Step 2: Write failing tests**

```python
# tests/stages/test_explode.py
from dxf_cleaner.config import Config
from dxf_cleaner.stages.explode import explode_and_filter


def test_passthrough_entities_are_kept(new_doc):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_circle((0, 0), radius=1.0)
    entities, diagnostics = explode_and_filter(msp, Config())
    types = sorted(e.dxftype() for e in entities)
    assert types == ["CIRCLE", "LINE"]
    assert diagnostics == []


def test_text_entities_are_dropped_with_warning(new_doc):
    msp = new_doc.modelspace()
    msp.add_text("hello")
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "TEXT_SKIPPED"


def test_point_and_dimension_are_dropped_silently(new_doc):
    msp = new_doc.modelspace()
    msp.add_point((0, 0))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert diagnostics == []


def test_3dface_is_dropped_with_warning(new_doc):
    msp = new_doc.modelspace()
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert diagnostics[0].code == "3D_ENTITY_SKIPPED"


def test_hatch_is_dropped_silently_by_default(new_doc):
    msp = new_doc.modelspace()
    hatch = msp.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (1, 0), (1, 1), (0, 1)], is_closed=True)
    entities, diagnostics = explode_and_filter(msp, Config())
    assert entities == []
    assert diagnostics == []


def test_single_level_insert_is_exploded_to_its_content(new_doc):
    block = new_doc.blocks.new("BLOCK1")
    block.add_line((0, 0), (1, 0))
    msp = new_doc.modelspace()
    msp.add_blockref("BLOCK1", (10, 10))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert len(entities) == 1
    line = entities[0]
    assert line.dxftype() == "LINE"
    assert tuple(line.dxf.start)[:2] == (10.0, 10.0)
    assert tuple(line.dxf.end)[:2] == (11.0, 10.0)


def test_nested_insert_is_exploded_recursively(new_doc):
    inner = new_doc.blocks.new("INNER")
    inner.add_line((0, 0), (1, 0))
    outer = new_doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (5, 5))
    msp = new_doc.modelspace()
    msp.add_blockref("OUTER", (10, 10))
    entities, diagnostics = explode_and_filter(msp, Config())
    assert len(entities) == 1
    line = entities[0]
    assert line.dxftype() == "LINE"
    assert tuple(line.dxf.start)[:2] == (15.0, 15.0)
    assert tuple(line.dxf.end)[:2] == (16.0, 15.0)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/stages/test_explode.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.explode'`

- [ ] **Step 4: Implement `dxf_cleaner/stages/explode.py`**

```python
from dxf_cleaner.config import Config
from dxf_cleaner.model import Diagnostic

_KEEP_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"}
_WARN_TYPES = {
    "TEXT": "TEXT_SKIPPED",
    "MTEXT": "TEXT_SKIPPED",
    "3DFACE": "3D_ENTITY_SKIPPED",
    "SOLID": "3D_ENTITY_SKIPPED",
    "MESH": "3D_ENTITY_SKIPPED",
}
_SILENT_DROP_TYPES = {"DIMENSION", "LEADER", "POINT", "HATCH"}


def _explode_insert(insert, diagnostics: list[Diagnostic]) -> list:
    result = []
    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            result.extend(_explode_insert(entity, diagnostics))
        else:
            result.extend(_classify(entity, diagnostics))
    return result


def _classify(entity, diagnostics: list[Diagnostic]) -> list:
    dxftype = entity.dxftype()
    if dxftype in _KEEP_TYPES:
        return [entity]
    if dxftype == "INSERT":
        return _explode_insert(entity, diagnostics)
    if dxftype in _WARN_TYPES:
        handle = getattr(entity.dxf, "handle", None)
        diagnostics.append(Diagnostic(code=_WARN_TYPES[dxftype], message=f"{dxftype} entity skipped", handle=handle))
        return []
    # includes _SILENT_DROP_TYPES and any other unrecognized entity type
    return []


def explode_and_filter(entities, config: Config) -> tuple[list, list[Diagnostic]]:
    """Recursively explode INSERTs and drop unsupported entities.

    Returns (kept_entities, diagnostics) where kept_entities only contains
    LINE/ARC/CIRCLE/LWPOLYLINE/POLYLINE/SPLINE/ELLIPSE entities.
    """
    diagnostics: list[Diagnostic] = []
    kept: list = []
    for entity in entities:
        kept.extend(_classify(entity, diagnostics))
    return kept, diagnostics
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/stages/test_explode.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add dxf_cleaner/stages/explode.py tests/stages/test_explode.py tests/conftest.py
git commit -m "feat: add recursive INSERT explode and entity filtering"
```

---

### Task 7: `stages/flatten.py` — chord-tolerance flattening for SPLINE/ELLIPSE

**Files:**
- Create: `dxf_cleaner/stages/flatten.py`
- Test: `tests/stages/test_flatten.py`

**Interfaces:**
- Consumes: `Segment`, `Contour` from `dxf_cleaner.model` (Task 2/3).
- Produces: `flatten_entity(entity, chord_tolerance: float, detect_circular: bool) -> Contour`
  - Works for `ezdxf` `Spline` and `Ellipse` entities.
  - `source_layer` / `source_handle` on the returned `Contour` come from `entity.dxf.layer` / `entity.dxf.handle`.
  - This task covers the plain flatten-to-line-segments path only; circular-arc re-detection is Task 8.

- [ ] **Step 1: Write failing tests**

```python
# tests/stages/test_flatten.py
import math
from dxf_cleaner.stages.flatten import flatten_entity


def test_flatten_open_spline_produces_line_segments(new_doc):
    msp = new_doc.modelspace()
    spline = msp.add_spline(
        fit_points=[(0, 0), (5, 5), (10, 0), (15, 5)], degree=3
    )
    spline.dxf.layer = "0"
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=False)
    assert contour.is_closed is False
    assert all(seg.kind == "line" for seg in contour.segments)
    assert contour.segments[0].start[0] == 0
    assert math.isclose(contour.segments[-1].end[0], 15, abs_tol=0.5)


def test_flatten_ellipse_produces_line_segments(new_doc):
    msp = new_doc.modelspace()
    ellipse = msp.add_ellipse(center=(0, 0), major_axis=(5, 0), ratio=0.5)
    contour = flatten_entity(ellipse, chord_tolerance=0.02, detect_circular=False)
    assert all(seg.kind == "line" for seg in contour.segments)
    # a full ellipse loop should start and end at (nearly) the same point
    assert math.isclose(contour.segments[0].start[0], contour.segments[-1].end[0], abs_tol=1e-6)
    assert math.isclose(contour.segments[0].start[1], contour.segments[-1].end[1], abs_tol=1e-6)


def test_flatten_uses_source_handle_and_layer(new_doc):
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=[(0, 0), (1, 1), (2, 0)], degree=2, dxfattribs={"layer": "MYLAYER"})
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=False)
    assert contour.source_layer == "MYLAYER"
    assert contour.source_handle == spline.dxf.handle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/stages/test_flatten.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.stages.flatten'`

- [ ] **Step 3: Implement `dxf_cleaner/stages/flatten.py`**

```python
import math
from dxf_cleaner.model import Segment, Contour, Point


def _is_closed_loop(points: list[tuple[float, float, float]], tol: float = 1e-6) -> bool:
    return math.dist(points[0][:2], points[-1][:2]) <= tol


def _flatten_to_lines(points: list[Point], layer: str, handle: str) -> Contour:
    is_closed = math.dist(points[0], points[-1]) <= 1e-6
    if is_closed:
        points = points[:-1]  # avoid a zero-length closing segment; is_closed implies wraparound
    segments = [
        Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)])
        for i in range(len(points) - (0 if is_closed else 1))
    ]
    return Contour(segments=segments, is_closed=is_closed, source_layer=layer, source_handle=handle)


def flatten_entity(entity, chord_tolerance: float, detect_circular: bool) -> Contour:
    """Flatten a SPLINE or ELLIPSE entity into a Contour of line segments within
    `chord_tolerance` of the true curve. Circular-arc re-detection (Task 8) is layered
    on top of this when `detect_circular` is True."""
    layer = entity.dxf.layer
    handle = entity.dxf.handle
    raw_points = list(entity.flattening(chord_tolerance))
    points: list[Point] = [(p.x, p.y) for p in raw_points]

    if detect_circular:
        from dxf_cleaner.stages.flatten import _try_fit_circle  # local import to keep Task 8 self-contained below
        circle_contour = _try_fit_circle(points, chord_tolerance, layer, handle)
        if circle_contour is not None:
            return circle_contour

    return _flatten_to_lines(points, layer, handle)
```

Note: `_try_fit_circle` does not exist yet — Task 8 adds it in the same file. Leave the `detect_circular=False` path fully working; Task 8's tests will drive the rest.

- [ ] **Step 4: Run test to verify it passes for the `detect_circular=False` cases**

Run: `pytest tests/stages/test_flatten.py -v`
Expected: PASS (3 tests; all pass `detect_circular=False`, so the not-yet-defined `_try_fit_circle` branch is never hit)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/flatten.py tests/stages/test_flatten.py
git commit -m "feat: add chord-tolerance flattening for SPLINE/ELLIPSE entities"
```

---

### Task 8: `stages/flatten.py` — circular-arc detection (3-point fit)

**Files:**
- Modify: `dxf_cleaner/stages/flatten.py`
- Test: `tests/stages/test_flatten.py`

**Interfaces:**
- Consumes: `Segment`, `Contour`, `contour_as_full_circle` from `dxf_cleaner.model`.
- Produces: `_try_fit_circle(points: list[Point], tolerance: float, layer: str, handle: str) -> Contour | None`
  - Fits a circle through `points[0]`, `points[len//2]`, `points[-1]`; returns `None` if the points are (near-)collinear or if any sampled point deviates from that circle by more than `tolerance`.
  - If it fits and the points form a closed loop (spline that traces a full circle), returns a 2-arc closed `Contour` matching the shape `contour_as_full_circle` recognizes.
  - If it fits but the points are an open arc, returns a single-`Segment(kind="arc")` open `Contour`.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/stages/test_flatten.py
import math
from dxf_cleaner.model import contour_as_full_circle
from dxf_cleaner.stages.flatten import flatten_entity


def _points_on_arc(cx, cy, r, a0_deg, a1_deg, n=20):
    a0, a1 = math.radians(a0_deg), math.radians(a1_deg)
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / (n - 1)), cy + r * math.sin(a0 + (a1 - a0) * i / (n - 1))) for i in range(n)]


def test_spline_that_is_really_a_quarter_circle_is_detected_as_arc(new_doc):
    pts = _points_on_arc(0, 0, 10, 0, 90)
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts, degree=3, dxfattribs={"layer": "0"})
    # Monkeypatch flattening to return our exact arc points regardless of fit accuracy,
    # isolating the circle-detection logic from spline-fitting noise.
    spline.flattening = lambda distance, segments=4: (type("P", (), {"x": x, "y": y})() for x, y in pts)
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert len(contour.segments) == 1
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert math.isclose(seg.radius, 10.0, abs_tol=0.02)
    assert contour.is_closed is False


def test_spline_that_is_really_a_full_circle_is_detected_as_two_arc_circle(new_doc):
    pts = _points_on_arc(0, 0, 10, 0, 360, n=40)
    pts[-1] = pts[0]  # close the loop exactly
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts[:-1], degree=3, dxfattribs={"layer": "0"})
    spline.flattening = lambda distance, segments=4: (type("P", (), {"x": x, "y": y})() for x, y in pts)
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert contour.is_closed is True
    result = contour_as_full_circle(contour, tolerance=0.02)
    assert result is not None
    center, radius = result
    assert math.isclose(radius, 10.0, abs_tol=0.02)


def test_non_circular_spline_falls_back_to_line_segments(new_doc):
    pts = [(0, 0), (5, 5), (10, 0), (15, 5)]
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts, degree=3, dxfattribs={"layer": "0"})
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert all(seg.kind == "line" for seg in contour.segments)


def test_three_collinear_points_do_not_crash_circle_fit(new_doc):
    pts = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
    msp = new_doc.modelspace()
    spline = msp.add_spline(fit_points=pts, degree=1, dxfattribs={"layer": "0"})
    spline.flattening = lambda distance, segments=4: (type("P", (), {"x": x, "y": y})() for x, y in pts)
    contour = flatten_entity(spline, chord_tolerance=0.02, detect_circular=True)
    assert all(seg.kind == "line" for seg in contour.segments)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/stages/test_flatten.py -v -k circle`
Expected: FAIL — either `AttributeError`/`ImportError` for `_try_fit_circle`, or assertions failing because `flatten_entity` still always falls back to lines.

- [ ] **Step 3: Implement `_try_fit_circle` and wire it up in `dxf_cleaner/stages/flatten.py`**

Replace the local-import placeholder from Task 7 with a real implementation. Full updated file:

```python
import math
from dxf_cleaner.model import Segment, Contour, Point


def _fit_circle_3pt(p1: Point, p2: Point, p3: Point) -> tuple[Point, float] | None:
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


def _signed_area_sign(points: list[Point]) -> int:
    area = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        area += x1 * y2 - x2 * y1
    return 1 if area >= 0 else -1


def _try_fit_circle(points: list[Point], tolerance: float, layer: str, handle: str) -> Contour | None:
    if len(points) < 3:
        return None
    p1, p2, p3 = points[0], points[len(points) // 2], points[-1]
    fit = _fit_circle_3pt(p1, p2, p3)
    if fit is None:
        return None
    center, radius = fit
    for x, y in points:
        if abs(math.dist((x, y), center) - radius) > tolerance:
            return None

    is_closed = math.dist(points[0], points[-1]) <= tolerance
    ccw = _signed_area_sign(points) > 0

    if is_closed:
        cx, cy = center
        p_start = points[0]
        # split the full circle into two half-circle arcs so it matches the shape
        # produced when reading a native CIRCLE entity (see model.contour_as_full_circle).
        angle_start = math.atan2(p_start[1] - cy, p_start[0] - cx)
        angle_mid = angle_start + (math.pi if ccw else -math.pi)
        p_mid = (cx + radius * math.cos(angle_mid), cy + radius * math.sin(angle_mid))
        seg1 = Segment(kind="arc", start=p_start, end=p_mid, center=center, radius=radius, ccw=ccw)
        seg2 = Segment(kind="arc", start=p_mid, end=p_start, center=center, radius=radius, ccw=ccw)
        return Contour(segments=[seg1, seg2], is_closed=True, source_layer=layer, source_handle=handle)

    seg = Segment(kind="arc", start=points[0], end=points[-1], center=center, radius=radius, ccw=ccw)
    return Contour(segments=[seg], is_closed=False, source_layer=layer, source_handle=handle)


def _flatten_to_lines(points: list[Point], layer: str, handle: str) -> Contour:
    is_closed = math.dist(points[0], points[-1]) <= 1e-6
    if is_closed:
        points = points[:-1]
    segments = [
        Segment(kind="line", start=points[i], end=points[(i + 1) % len(points)])
        for i in range(len(points) - (0 if is_closed else 1))
    ]
    return Contour(segments=segments, is_closed=is_closed, source_layer=layer, source_handle=handle)


def flatten_entity(entity, chord_tolerance: float, detect_circular: bool) -> Contour:
    layer = entity.dxf.layer
    handle = entity.dxf.handle
    raw_points = list(entity.flattening(chord_tolerance))
    points: list[Point] = [(p.x, p.y) for p in raw_points]

    if detect_circular:
        circle_contour = _try_fit_circle(points, chord_tolerance, layer, handle)
        if circle_contour is not None:
            return circle_contour

    return _flatten_to_lines(points, layer, handle)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/stages/test_flatten.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/stages/flatten.py tests/stages/test_flatten.py
git commit -m "feat: detect circular arcs hiding inside flattened splines via 3-point fit"
```

---

### Task 9: `reader.py` — unit scale determination

**Files:**
- Create: `dxf_cleaner/reader.py`
- Test: `tests/test_reader.py`

**Interfaces:**
- Consumes: `Config`, `Diagnostic`.
- Produces: `determine_unit_scale(doc, config: Config) -> tuple[float, Diagnostic | None]`
  - `$INSUNITS == 1` (inch) -> scale `25.4`, no diagnostic.
  - `$INSUNITS == 4` (mm) -> scale `1.0`, no diagnostic.
  - `$INSUNITS == 0` (unspecified, common Corel export) -> use `config.input.assumed_unit` (`25.4` for inch, `1.0` for mm), **with** a `Diagnostic(code="ASSUMED_UNIT")` explaining the assumption.
  - Any other `$INSUNITS` value -> treat as mm (`1.0`) with a `Diagnostic(code="UNSUPPORTED_INSUNITS")`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_reader.py
from dxf_cleaner.config import Config
from dxf_cleaner.reader import determine_unit_scale


def test_insunits_inch_gives_scale_25_4(new_doc):
    new_doc.header["$INSUNITS"] = 1
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 25.4
    assert diag is None


def test_insunits_mm_gives_scale_1(new_doc):
    new_doc.header["$INSUNITS"] = 4
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 1.0
    assert diag is None


def test_insunits_unset_falls_back_to_assumed_unit_mm_with_warning(new_doc):
    new_doc.header["$INSUNITS"] = 0
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 1.0
    assert diag is not None
    assert diag.code == "ASSUMED_UNIT"


def test_insunits_unset_falls_back_to_assumed_unit_inch_with_warning(new_doc):
    new_doc.header["$INSUNITS"] = 0
    cfg = Config(input={"assumed_unit": "inch"})
    scale, diag = determine_unit_scale(new_doc, cfg)
    assert scale == 25.4
    assert diag.code == "ASSUMED_UNIT"


def test_unsupported_insunits_warns_and_defaults_to_mm(new_doc):
    new_doc.header["$INSUNITS"] = 2  # feet — unsupported by this tool
    scale, diag = determine_unit_scale(new_doc, Config())
    assert scale == 1.0
    assert diag.code == "UNSUPPORTED_INSUNITS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.reader'`

- [ ] **Step 3: Implement (partial) `dxf_cleaner/reader.py`**

```python
from dxf_cleaner.config import Config
from dxf_cleaner.model import Diagnostic

_SUPPORTED_UNIT_SCALES = {1: 25.4, 4: 1.0}
_ASSUMED_UNIT_SCALES = {"mm": 1.0, "inch": 25.4}


def determine_unit_scale(doc, config: Config) -> tuple[float, Diagnostic | None]:
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits in _SUPPORTED_UNIT_SCALES:
        return _SUPPORTED_UNIT_SCALES[insunits], None
    if insunits == 0:
        scale = _ASSUMED_UNIT_SCALES[config.input.assumed_unit]
        return scale, Diagnostic(
            code="ASSUMED_UNIT",
            message=f"$INSUNITS not set in file; assuming {config.input.assumed_unit} per config",
        )
    return 1.0, Diagnostic(
        code="UNSUPPORTED_INSUNITS",
        message=f"$INSUNITS={insunits} is not supported (only mm/inch); treating as mm",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reader.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/reader.py tests/test_reader.py
git commit -m "feat: determine DXF unit scale from \$INSUNITS with assumed-unit fallback"
```

---

### Task 10: `reader.py` — direct entity conversion (LINE/ARC/CIRCLE)

**Files:**
- Modify: `dxf_cleaner/reader.py`
- Test: `tests/test_reader.py`

**Interfaces:**
- Consumes: `Segment`, `Contour` from `dxf_cleaner.model`.
- Produces: `convert_simple_entity(entity) -> Contour` for `dxftype()` in `{"LINE", "ARC", "CIRCLE"}`.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_reader.py
import math
from dxf_cleaner.reader import convert_simple_entity


def test_convert_line(new_doc):
    msp = new_doc.modelspace()
    line = msp.add_line((0, 0), (1, 2))
    contour = convert_simple_entity(line)
    assert contour.is_closed is False
    assert len(contour.segments) == 1
    assert contour.segments[0].kind == "line"
    assert contour.segments[0].start == (0.0, 0.0)
    assert contour.segments[0].end == (1.0, 2.0)


def test_convert_arc_degrees_to_radians_and_ccw(new_doc):
    msp = new_doc.modelspace()
    arc = msp.add_arc(center=(0, 0), radius=2.0, start_angle=0, end_angle=90)
    contour = convert_simple_entity(arc)
    assert contour.is_closed is False
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert seg.ccw is True
    assert math.isclose(seg.start[0], 2.0, abs_tol=1e-9)
    assert math.isclose(seg.start[1], 0.0, abs_tol=1e-9)
    assert math.isclose(seg.end[0], 0.0, abs_tol=1e-9)
    assert math.isclose(seg.end[1], 2.0, abs_tol=1e-9)


def test_convert_circle_becomes_two_arc_closed_contour(new_doc):
    msp = new_doc.modelspace()
    circle = msp.add_circle(center=(1, 1), radius=3.0)
    contour = convert_simple_entity(circle)
    assert contour.is_closed is True
    assert len(contour.segments) == 2
    assert all(seg.kind == "arc" for seg in contour.segments)
    assert all(seg.radius == 3.0 for seg in contour.segments)
    assert all(seg.center == (1.0, 1.0) for seg in contour.segments)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reader.py -v -k convert`
Expected: FAIL with `ImportError: cannot import name 'convert_simple_entity'`

- [ ] **Step 3: Implement in `dxf_cleaner/reader.py`**

```python
import math
from dxf_cleaner.model import Segment, Contour, Point


def _p2(v) -> Point:
    return (v.x, v.y)


def convert_simple_entity(entity) -> Contour:
    dxftype = entity.dxftype()
    layer = entity.dxf.layer
    handle = entity.dxf.handle

    if dxftype == "LINE":
        seg = Segment(kind="line", start=_p2(entity.dxf.start), end=_p2(entity.dxf.end))
        return Contour(segments=[seg], is_closed=False, source_layer=layer, source_handle=handle)

    if dxftype == "ARC":
        center = _p2(entity.dxf.center)
        radius = entity.dxf.radius
        a0 = math.radians(entity.dxf.start_angle)
        a1 = math.radians(entity.dxf.end_angle)
        start = (center[0] + radius * math.cos(a0), center[1] + radius * math.sin(a0))
        end = (center[0] + radius * math.cos(a1), center[1] + radius * math.sin(a1))
        # ARC entities are always defined CCW from start_angle to end_angle.
        seg = Segment(kind="arc", start=start, end=end, center=center, radius=radius, ccw=True)
        return Contour(segments=[seg], is_closed=False, source_layer=layer, source_handle=handle)

    if dxftype == "CIRCLE":
        center = _p2(entity.dxf.center)
        radius = entity.dxf.radius
        p_east = (center[0] + radius, center[1])
        p_west = (center[0] - radius, center[1])
        seg1 = Segment(kind="arc", start=p_east, end=p_west, center=center, radius=radius, ccw=True)
        seg2 = Segment(kind="arc", start=p_west, end=p_east, center=center, radius=radius, ccw=True)
        return Contour(segments=[seg1, seg2], is_closed=True, source_layer=layer, source_handle=handle)

    raise ValueError(f"convert_simple_entity does not handle {dxftype}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reader.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/reader.py tests/test_reader.py
git commit -m "feat: convert LINE/ARC/CIRCLE entities directly to Contour"
```

---

### Task 11: `reader.py` — LWPOLYLINE/POLYLINE conversion with bulge decoding

**Files:**
- Modify: `dxf_cleaner/reader.py`
- Test: `tests/test_reader.py`

**Interfaces:**
- Consumes: `ezdxf.math.bulge_to_arc`, `Segment`, `Contour`.
- Produces: `convert_polyline_entity(entity) -> tuple[Contour, Diagnostic | None]`
  - Handles `LWPOLYLINE` and 2D `POLYLINE` via bulge decoding.
  - A 3D `POLYLINE` (any vertex with non-zero Z) is projected to Z=0 and returns a `Diagnostic(code="3D_POLYLINE_PROJECTED")`.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_reader.py
from dxf_cleaner.reader import convert_polyline_entity


def test_convert_lwpolyline_straight_edges(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_lwpolyline([(0, 0), (1, 0), (1, 1)], format="xy")
    contour, diag = convert_polyline_entity(pl)
    assert diag is None
    assert contour.is_closed is False
    assert len(contour.segments) == 2
    assert all(seg.kind == "line" for seg in contour.segments)


def test_convert_lwpolyline_with_bulge_becomes_arc(new_doc):
    msp = new_doc.modelspace()
    # bulge=1.0 on the first vertex -> a semicircle from (0,0) to (2,0)
    pl = msp.add_lwpolyline([(0, 0, 0, 0, 1.0), (2, 0, 0, 0, 0.0)], format="xyseb")
    contour, diag = convert_polyline_entity(pl)
    assert len(contour.segments) == 1
    seg = contour.segments[0]
    assert seg.kind == "arc"
    assert math.isclose(seg.radius, 1.0, abs_tol=1e-9)


def test_convert_closed_lwpolyline(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], format="xy", close=True)
    contour, diag = convert_polyline_entity(pl)
    assert contour.is_closed is True
    assert len(contour.segments) == 4


def test_convert_2d_polyline_entity(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_polyline2d([(0, 0), (1, 0), (1, 1)])
    contour, diag = convert_polyline_entity(pl)
    assert diag is None
    assert len(contour.segments) == 2


def test_convert_3d_polyline_projects_and_warns(new_doc):
    msp = new_doc.modelspace()
    pl = msp.add_polyline3d([(0, 0, 5), (1, 0, 5), (1, 1, 5)])
    contour, diag = convert_polyline_entity(pl)
    assert diag is not None
    assert diag.code == "3D_POLYLINE_PROJECTED"
    for seg in contour.segments:
        assert len(seg.start) == 2  # projected to 2D, Z dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reader.py -v -k polyline`
Expected: FAIL with `ImportError: cannot import name 'convert_polyline_entity'`

- [ ] **Step 3: Implement in `dxf_cleaner/reader.py`**

```python
from ezdxf.math import bulge_to_arc
from dxf_cleaner.model import Diagnostic


def _segment_from_bulge(p0: Point, p1: Point, bulge: float) -> Segment:
    if bulge == 0:
        return Segment(kind="line", start=p0, end=p1)
    center, _start_angle, _end_angle, radius = bulge_to_arc(p0, p1, bulge)
    return Segment(kind="arc", start=p0, end=p1, center=(center.x, center.y), radius=radius, ccw=bulge > 0)


def convert_polyline_entity(entity) -> tuple[Contour, Diagnostic | None]:
    dxftype = entity.dxftype()
    layer = entity.dxf.layer
    handle = entity.dxf.handle
    diagnostic: Diagnostic | None = None

    if dxftype == "LWPOLYLINE":
        raw_points = entity.get_points("xyb")
        vertices = [((x, y), b) for x, y, b in raw_points]
        is_closed = entity.closed
    elif dxftype == "POLYLINE":
        is_3d = any(abs(v.dxf.location.z) > 1e-9 for v in entity.vertices)
        vertices = [((v.dxf.location.x, v.dxf.location.y), v.dxf.bulge) for v in entity.vertices]
        is_closed = entity.is_closed
        if is_3d:
            diagnostic = Diagnostic(
                code="3D_POLYLINE_PROJECTED",
                message="3D POLYLINE projected to Z=0",
                handle=handle,
            )
    else:
        raise ValueError(f"convert_polyline_entity does not handle {dxftype}")

    n = len(vertices)
    count = n if is_closed else n - 1
    segments = [
        _segment_from_bulge(vertices[i][0], vertices[(i + 1) % n][0], vertices[i][1])
        for i in range(count)
    ]
    contour = Contour(segments=segments, is_closed=is_closed, source_layer=layer, source_handle=handle)
    return contour, diagnostic
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reader.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/reader.py tests/test_reader.py
git commit -m "feat: convert LWPOLYLINE/POLYLINE to Contour with bulge-to-arc decoding"
```

---

### Task 12: `reader.py` — `read_dxf()` orchestration

**Files:**
- Modify: `dxf_cleaner/reader.py`
- Test: `tests/test_reader.py`

**Interfaces:**
- Consumes: `determine_unit_scale`, `convert_simple_entity`, `convert_polyline_entity` (this file); `explode_and_filter` (`dxf_cleaner.stages.explode`); `flatten_entity` (`dxf_cleaner.stages.flatten`); `scale_contour` (`dxf_cleaner.model`).
- Produces:
  - `@dataclass ReadResult(contours: list[Contour], diagnostics: list[Diagnostic], unit_scale: float)`
  - `read_dxf(path: str, config: Config) -> ReadResult`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_reader.py
from dxf_cleaner.config import Config
from dxf_cleaner.reader import read_dxf, ReadResult


def test_read_dxf_mm_file_with_line_and_circle(new_doc, tmp_path):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_circle((5, 5), radius=2.0)
    new_doc.header["$INSUNITS"] = 4
    path = tmp_path / "test.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert isinstance(result, ReadResult)
    assert result.unit_scale == 1.0
    assert len(result.contours) == 2
    assert result.diagnostics == []


def test_read_dxf_inch_file_scales_coordinates(new_doc, tmp_path):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    new_doc.header["$INSUNITS"] = 1
    path = tmp_path / "inch.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    line_contour = result.contours[0]
    assert math.isclose(line_contour.segments[0].end[0], 25.4, abs_tol=1e-6)


def test_read_dxf_skips_text_and_records_diagnostic(new_doc, tmp_path):
    msp = new_doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_text("hello")
    path = tmp_path / "with_text.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert len(result.contours) == 1
    assert any(d.code == "TEXT_SKIPPED" for d in result.diagnostics)


def test_read_dxf_explodes_blocks(new_doc, tmp_path):
    block = new_doc.blocks.new("B1")
    block.add_line((0, 0), (1, 0))
    msp = new_doc.modelspace()
    msp.add_blockref("B1", (10, 10))
    path = tmp_path / "with_block.dxf"
    new_doc.saveas(path)

    result = read_dxf(str(path), Config())
    assert len(result.contours) == 1
    assert result.contours[0].segments[0].start == (10.0, 10.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reader.py -v -k read_dxf`
Expected: FAIL with `ImportError: cannot import name 'read_dxf'`

- [ ] **Step 3: Implement in `dxf_cleaner/reader.py`**

```python
from dataclasses import dataclass
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.model import Contour, Diagnostic, scale_contour
from dxf_cleaner.stages.explode import explode_and_filter
from dxf_cleaner.stages.flatten import flatten_entity


@dataclass
class ReadResult:
    contours: list[Contour]
    diagnostics: list[Diagnostic]
    unit_scale: float


def _convert_entity(entity, config: Config) -> tuple[Contour | None, Diagnostic | None]:
    dxftype = entity.dxftype()
    if dxftype in {"LINE", "ARC", "CIRCLE"}:
        return convert_simple_entity(entity), None
    if dxftype in {"LWPOLYLINE", "POLYLINE"}:
        return convert_polyline_entity(entity)
    if dxftype in {"SPLINE", "ELLIPSE"}:
        contour = flatten_entity(
            entity,
            chord_tolerance=config.flatten.chord_tolerance,
            detect_circular=config.flatten.detect_circular_splines,
        )
        return contour, None
    return None, None


def read_dxf(path: str, config: Config) -> ReadResult:
    doc = ezdxf.readfile(path)
    unit_scale, unit_diag = determine_unit_scale(doc, config)

    diagnostics: list[Diagnostic] = []
    if unit_diag is not None:
        diagnostics.append(unit_diag)

    kept_entities, explode_diags = explode_and_filter(doc.modelspace(), config)
    diagnostics.extend(explode_diags)

    contours: list[Contour] = []
    for entity in kept_entities:
        contour, diag = _convert_entity(entity, config)
        if diag is not None:
            diagnostics.append(diag)
        if contour is not None:
            contours.append(scale_contour(contour, unit_scale))

    return ReadResult(contours=contours, diagnostics=diagnostics, unit_scale=unit_scale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reader.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/reader.py tests/test_reader.py
git commit -m "feat: add read_dxf() orchestration tying together unit scale, explode, and convert"
```

---

### Task 13: `writer.py` — write Contours back to a DXF file

**Files:**
- Create: `dxf_cleaner/writer.py`
- Test: `tests/test_writer.py`

**Interfaces:**
- Consumes: `Contour`, `contour_as_full_circle` from `dxf_cleaner.model`; `Config` (`output.dxf_version`, `output.layer_name`).
- Produces: `write_dxf(contours: list[Contour], path: str, config: Config) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_writer.py
import math
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.model import Segment, Contour
from dxf_cleaner.writer import write_dxf


def test_write_dxf_sets_header_and_layer(tmp_path):
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    assert doc.header["$INSUNITS"] == 4
    assert doc.dxfversion == "AC1015"
    assert "CUT" in doc.layers


def test_write_dxf_open_contour_becomes_lwpolyline(tmp_path):
    seg1 = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 0.0))
    seg2 = Segment(kind="line", start=(1.0, 0.0), end=(1.0, 1.0))
    contour = Contour(segments=[seg1, seg2], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    entities = list(doc.modelspace())
    assert len(entities) == 1
    pl = entities[0]
    assert pl.dxftype() == "LWPOLYLINE"
    assert pl.closed is False
    assert pl.dxf.layer == "CUT"
    points = pl.get_points("xyb")
    assert len(points) == 3


def test_write_dxf_arc_segment_gets_correct_bulge(tmp_path):
    seg = Segment(kind="arc", start=(1.0, 0.0), end=(0.0, 1.0), center=(0.0, 0.0), radius=1.0, ccw=True)
    seg2 = Segment(kind="line", start=(0.0, 1.0), end=(1.0, 0.0))
    contour = Contour(segments=[seg, seg2], is_closed=True, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    pl = list(doc.modelspace())[0]
    points = pl.get_points("xyb")
    bulge = points[0][2]
    assert bulge > 0  # CCW arc -> positive bulge
    assert math.isclose(bulge, math.tan(math.pi / 8), abs_tol=1e-6)  # 90deg sweep -> tan(theta/4)


def test_write_dxf_full_circle_contour_becomes_native_circle(tmp_path):
    center = (2.0, 3.0)
    radius = 5.0
    p_east = (center[0] + radius, center[1])
    p_west = (center[0] - radius, center[1])
    seg1 = Segment(kind="arc", start=p_east, end=p_west, center=center, radius=radius, ccw=True)
    seg2 = Segment(kind="arc", start=p_west, end=p_east, center=center, radius=radius, ccw=True)
    contour = Contour(segments=[seg1, seg2], is_closed=True, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    entities = list(doc.modelspace())
    assert len(entities) == 1
    circle = entities[0]
    assert circle.dxftype() == "CIRCLE"
    assert math.isclose(circle.dxf.radius, 5.0, abs_tol=1e-9)
    assert math.isclose(circle.dxf.center.x, 2.0, abs_tol=1e-9)


def test_write_dxf_all_points_have_z_zero(tmp_path):
    seg = Segment(kind="line", start=(0.0, 0.0), end=(1.0, 1.0))
    contour = Contour(segments=[seg], is_closed=False, source_layer="0", source_handle="1")
    path = tmp_path / "out.dxf"
    write_dxf([contour], str(path), Config())

    doc = ezdxf.readfile(str(path))
    pl = list(doc.modelspace())[0]
    for vertex in pl:
        assert vertex[2] == 0 or len(vertex) == 3  # xyb tuples carry no z; sanity check format
    assert doc.header["$EXTMIN"][2] == 0
    assert doc.header["$EXTMAX"][2] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dxf_cleaner.writer'`

- [ ] **Step 3: Implement `dxf_cleaner/writer.py`**

```python
import math
import ezdxf
from dxf_cleaner.config import Config
from dxf_cleaner.model import Contour, Segment, contour_as_full_circle, contour_bbox


def _arc_bulge(seg: Segment) -> float:
    cx, cy = seg.center
    a_start = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a_end = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    two_pi = 2 * math.pi
    if seg.ccw:
        theta = (a_end - a_start) % two_pi
    else:
        theta = (a_start - a_end) % two_pi
    magnitude = math.tan(theta / 4)
    return magnitude if seg.ccw else -magnitude


def _write_circle(msp, contour: Contour, layer: str) -> None:
    center, radius = contour_as_full_circle(contour)
    msp.add_circle(center=center, radius=radius, dxfattribs={"layer": layer})


def _write_lwpolyline(msp, contour: Contour, layer: str) -> None:
    vertices = []
    for seg in contour.segments:
        bulge = _arc_bulge(seg) if seg.kind == "arc" else 0.0
        vertices.append((seg.start[0], seg.start[1], 0, 0, bulge))
    if not contour.is_closed:
        last = contour.segments[-1]
        vertices.append((last.end[0], last.end[1], 0, 0, 0.0))
    msp.add_lwpolyline(vertices, format="xyseb", close=contour.is_closed, dxfattribs={"layer": layer})


def write_dxf(contours: list[Contour], path: str, config: Config) -> None:
    doc = ezdxf.new(config.output.dxf_version)
    doc.header["$INSUNITS"] = 4

    layer_name = config.output.layer_name
    if layer_name not in doc.layers:
        doc.layers.add(name=layer_name, color=7)

    msp = doc.modelspace()
    for contour in contours:
        if contour_as_full_circle(contour) is not None:
            _write_circle(msp, contour, layer_name)
        else:
            _write_lwpolyline(msp, contour, layer_name)

    if contours:
        minxs, minys, maxxs, maxys = zip(*(contour_bbox(c) for c in contours))
        doc.header["$EXTMIN"] = (min(minxs), min(minys), 0.0)
        doc.header["$EXTMAX"] = (max(maxxs), max(maxys), 0.0)

    doc.saveas(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_writer.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add dxf_cleaner/writer.py tests/test_writer.py
git commit -m "feat: add write_dxf() with bulge-encoded LWPOLYLINE and native CIRCLE output"
```

---

### Task 14: Round-trip size-regression test harness

**Files:**
- Create: `tests/test_roundtrip_regression.py`

**Interfaces:**
- Consumes: `read_dxf` (Task 12), `write_dxf` (Task 13), `contour_bbox`/`contour_signed_area` (`dxf_cleaner.model`).
- Produces: a pytest-parametrized regression suite. No new production code; this task is the safety net required by spec section 10 ("Test hồi quy về kích thước") and acceptance criteria #4/#5 in section 11.

This test builds each fixture DXF **in-memory with ezdxf** (no external sample files needed for Phase 1 — real Corel-exported samples are folded in during Phase 2/3 per the spec's test plan), reads it, writes it back out, reads the output again, and compares total area and bounding box between the first and second read. It also checks that arc-bearing shapes keep a low node count (acceptance criterion #5: node count must not grow more than 20%).

- [ ] **Step 1: Write the regression tests**

```python
# tests/test_roundtrip_regression.py
import math
import ezdxf
import pytest
from dxf_cleaner.config import Config
from dxf_cleaner.model import contour_bbox, contour_signed_area
from dxf_cleaner.reader import read_dxf
from dxf_cleaner.writer import write_dxf


def _total_area(contours) -> float:
    return sum(abs(contour_signed_area(c)) for c in contours if c.is_closed)


def _overall_bbox(contours):
    boxes = [contour_bbox(c) for c in contours]
    minxs, minys, maxxs, maxys = zip(*boxes)
    return (min(minxs), min(minys), max(maxxs), max(maxys))


def _roundtrip(doc, tmp_path, config: Config = Config()):
    input_path = tmp_path / "input.dxf"
    doc.saveas(input_path)

    first_read = read_dxf(str(input_path), config)

    output_path = tmp_path / "output.dxf"
    write_dxf(first_read.contours, str(output_path), config)

    second_read = read_dxf(str(output_path), config)
    return first_read, second_read


@pytest.mark.parametrize(
    "build_doc",
    [
        pytest.param(lambda doc: doc.modelspace().add_lwpolyline(
            [(0, 0), (10, 0), (10, 10), (0, 10)], format="xy", close=True
        ) and None, id="rectangle"),
        pytest.param(lambda doc: doc.modelspace().add_circle((5, 5), radius=3.0) and None, id="circle"),
        pytest.param(lambda doc: doc.modelspace().add_lwpolyline(
            [(0, 0, 0, 0, 1.0), (10, 0, 0, 0, 0.0), (10, 10, 0, 0, 0.0), (0, 10, 0, 0, 0.0)],
            format="xyseb", close=True,
        ) and None, id="polyline_with_bulge"),
        pytest.param(lambda doc: doc.modelspace().add_arc(
            center=(0, 0), radius=5.0, start_angle=0, end_angle=180
        ) and doc.modelspace().add_line((-5, 0), (5, 0)) and None, id="arc_plus_closing_line"),
    ],
)
def test_area_and_bbox_stable_across_roundtrip(build_doc, tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    build_doc(doc)

    first_read, second_read = _roundtrip(doc, tmp_path)

    first_area = _total_area(first_read.contours)
    second_area = _total_area(second_read.contours)
    if first_area > 0:
        assert abs(second_area - first_area) / first_area < 0.001  # <0.1%

    first_bbox = _overall_bbox(first_read.contours)
    second_bbox = _overall_bbox(second_read.contours)
    for a, b in zip(first_bbox, second_bbox):
        assert math.isclose(a, b, abs_tol=0.02)  # within chord tolerance


def test_inch_file_roundtrip_preserves_mm_dimensions(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 1  # inch
    doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], format="xy", close=True)

    first_read, second_read = _roundtrip(doc, tmp_path)

    assert first_read.unit_scale == 25.4
    first_bbox = _overall_bbox(first_read.contours)
    assert math.isclose(first_bbox[2], 25.4, abs_tol=1e-6)  # 1 inch square -> 25.4mm wide
    second_bbox = _overall_bbox(second_read.contours)
    for a, b in zip(first_bbox, second_bbox):
        assert math.isclose(a, b, abs_tol=0.02)


def test_circle_arc_count_does_not_explode_after_roundtrip(tmp_path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_circle((0, 0), radius=10.0)

    first_read, second_read = _roundtrip(doc, tmp_path)

    # A circle must round-trip as a 2-arc contour both times (acceptance criterion #5:
    # node count for arc-bearing contours must not grow >20% — here it must not grow at all).
    assert len(first_read.contours[0].segments) == 2
    assert len(second_read.contours[0].segments) == 2
    assert all(seg.kind == "arc" for seg in second_read.contours[0].segments)
```

- [ ] **Step 2: Run test to verify it fails (before this task, `write_dxf`/`read_dxf` already exist from Tasks 12-13, so this should mostly pass immediately — run it to confirm)**

Run: `pytest tests/test_roundtrip_regression.py -v`
Expected: All tests should PASS given Tasks 1-13 are complete. If any fails, that is a real Phase-1 bug to fix now — do not weaken the assertions to make it pass.

- [ ] **Step 3: If everything passes, run the full test suite to confirm no regressions**

Run: `pytest -v`
Expected: PASS (all tests across all modules)

- [ ] **Step 4: Commit**

```bash
git add tests/test_roundtrip_regression.py
git commit -m "test: add round-trip size-regression harness (area/bbox stability, arc preservation)"
```

---

## Self-Review Notes

- **Spec coverage:** Task 2-4 cover model.py (§5.1). Task 6 covers explode.py (§6.1 INSERT/invalid entity rows). Tasks 7-8 cover flatten.py (§6.2, including the circular-arc detection "tối ưu bổ sung"). Tasks 9-12 cover reader.py (§6.1: all entity rows except HATCH-boundary-extraction and 3D POLYLINE warning, both included; unit normalization). Task 13 covers writer.py (§6.10: AC1015, single layer, bulge, native CIRCLE, $INSUNITS=4, Z=0, $EXTMIN/$EXTMAX). Task 14 covers §10's size-regression requirement and acceptance criteria #4/#5 from §11.
- **Explicitly out of scope for Phase 1** (deferred to Phase 2/3 per §12, not silently dropped): HATCH boundary extraction (`include_hatch_boundary` config exists in the full spec but is not implemented here — HATCH is always dropped silently in Phase 1), CLI (`cli.py`), `pipeline.py`, `report.py`, `watcher.py`, and all of `snap/dedupe/despeckle/weld/hierarchy/simplify/validate`.
- **Type consistency check:** `Segment`, `Contour`, `Part`, `Diagnostic` signatures introduced in Task 2 are used identically in Tasks 6-13 (`Diagnostic(code, message, handle)`, `Contour(segments, is_closed, source_layer, source_handle)`). `ReadResult(contours, diagnostics, unit_scale)` from Task 12 matches its use in Task 14.
- **No placeholders:** every step above has complete, runnable code — no TBD/TODO markers.

---

Plan complete and saved to `docs/superpowers/plans/2026-09-09-dxf-cleaner-phase1.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
