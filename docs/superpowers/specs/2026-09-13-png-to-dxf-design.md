# PNG → DXF raster input support — design spec

Date: 2026-09-13

## 1. Problem

`dxfclean` only accepts DXF input. Customers sometimes send raster images
(PNG/JPG) instead of vector files. A standalone script, `png2dxf.py`
(reference copy at `/Users/dung_tt/Desktop/Dragoncorp/files/png2dxf.py`,
operating doc at `.../QUY-TRINH-PNG-SANG-DXF.md`), already solves this
end-to-end and has been validated against a real shop logo (IoU 96.35% vs.
source, node reduction 3589→145 at 0.17% area deviation). It duplicates
logic that `dxf_cleaner` already has in its stage pipeline (hierarchy
detection, simplify with area-deviation guard, geometric validation).

Goal: bring raster input into `dxf_cleaner` so a PNG/JPG goes through the
*same* stage pipeline as a DXF (dedupe, despeckle, weld, hierarchy,
simplify, validate), instead of maintaining a second, separate
implementation of those checks. Keep every operationally load-bearing
behavior from `png2dxf.py` (the `_KIEMTRA.png` preview image, the
thin-feature/thickness check, the invert/DPI warnings) — the shop
checklist in the operating doc depends on these.

## 2. Non-goals

- Nesting, kerf compensation, lead-in/out, cut ordering — unchanged,
  still done in CypCut (per the operating doc, section 1).
- Color/grayscale image segmentation — input is assumed binarizable
  line-art (per prior clarification), same assumption `png2dxf.py` makes.
- Replacing or removing `png2dxf.py` / its `.bat` wrappers — out of scope;
  this is a separate, integrated code path inside `dxf_cleaner`. Whether
  the shop keeps using the standalone script or switches to `dxfclean` is
  an operational decision, not part of this change.

## 3. Architecture

### 3.1 New module: `dxf_cleaner/raster.py`

Ported from `png2dxf.py`, largely unchanged (this part is already
validated — no algorithmic rewrite):

- `load_binary(path, threshold, invert) -> (mask, threshold_used, (w_px, h_px))`
  — alpha compositing onto white, grayscale, Otsu or fixed threshold,
  optional invert.
- `trace_mask(mask, min_area_px, smooth_sigma) -> list[Ring]` — sub-pixel
  marching-squares contour extraction (`skimage.measure.find_contours`),
  returning a **flat list of pixel-space rings** (no hole/exterior
  classification here — see 3.2, this is the one deliberate behavior
  change from `png2dxf.py`).
- `measure_min_width(mask, prune_mm_px) -> (min_width_px, n_parts)` —
  skeletonize + distance-transform, unchanged from `png2dxf.py`.
- `render_preview(mask, rings, thin_mask, out_path)` — writes the
  `_KIEMTRA.png` diagnostic image (gray = kept metal, green = outer
  boundary, blue = hole boundary, red = feature thinner than 2×
  thickness). Unchanged rendering, same color coding the shop already
  reads.

### 3.2 `read_raster(path, config) -> ReadResult`

Plays the same role as `reader.read_dxf`, so `run_pipeline` can dispatch
on file extension without changing anything downstream:

1. `load_binary` → mask, threshold, pixel size.
2. Invert-suspect heuristic (fill ratio / border ratio, same thresholds as
   `png2dxf.py`) → `Diagnostic(code="RASTER_POSSIBLE_INVERTED")` if
   triggered.
3. `trace_mask` → flat pixel-space rings.
4. Scale px→mm using `config.raster.pixels_per_mm`, or a target
   `width_mm`/`height_mm` supplied via CLI (see 3.5), with the same
   y-flip `png2dxf.py` uses so DXF Y grows upward.
5. Convert each ring to a `Contour` (`is_closed=True`, every `Segment`
   `kind="line"`, `source_layer="CUT"`, `source_handle` synthesized as
   `f"raster-{i}"`).
6. DPI check: if effective DPI < 300 →
   `Diagnostic(code="RASTER_LOW_DPI", ...)` (warning); this mirrors the
   operating doc's resolution table (section 2).
7. `measure_min_width` → store the scaled-to-mm value in
   `ReadResult`-adjacent stats (see 3.4) rather than as a Diagnostic,
   because it needs `config.validate.material_thickness` to be judged,
   and `read_raster` does not currently receive `ValidateConfig`
   semantics beyond what it needs to scale — the comparison itself
   happens in `validate()` (3.4), keeping severity policy in one place.

Return `ReadResult(contours=<flat list from step 5>, diagnostics=<2,6>,
unit_scale=1.0, flattened_handles=<every raster contour's source_handle>)`.
`unit_scale=1.0` because coordinates are already in mm after step 4 —
`run_pipeline` must not rescale raster output. `flattened_handles` must
contain every raster contour's handle (not the empty set): raster contours
are discretized/flattened approximations of an underlying shape, exactly
like the SPLINE/ELLIPSE contours that already populate `flattened_handles`
for DXF input, and `simplify_contours` (3.2's downstream consumer, wired in
`run_pipeline`) only simplifies a contour whose `source_handle` is in
`touched_handles = flattened_handles | welded_handles` — leaving
`flattened_handles` empty for raster input would mean simplify never runs
on any raster contour, defeating the point of node-reducing sub-pixel
traced geometry.

**Why a flat contour list, deliberately dropping `png2dxf.py`'s own
hole/exterior depth computation:** `dxf_cleaner.stages.hierarchy.build_hierarchy`
already does exactly this (containment-depth classification into
`Part.exterior` / `Part.interiors`) for DXF input. Reusing it for raster
input removes a second implementation of the same rule and guarantees a
PNG and an equivalent DXF go through identical hole detection.

### 3.3 Pipeline wiring (`pipeline.py`, `cli.py`)

`run_pipeline` currently calls `read_dxf` unconditionally. Change:
dispatch on `Path(input_path).suffix.lower()` — `.dxf` → `read_dxf`,
`{.png, .jpg, .jpeg}` → `read_raster`. Everything after the read step
(`snap_and_chain`, `dedupe_contours`, `despeckle_contours`,
`weld_contours`, `build_hierarchy`, `simplify_contours`, `validate`) is
unchanged code, run unchanged. Expected effective behavior on raster
input:
- `snap_and_chain`: near no-op (rings are already closed/contiguous from
  the tracer) but harmless to run — keeps one code path.
- `dedupe_contours` / `weld_contours`: near no-op for the same reason;
  leave enabled at defaults rather than special-casing raster input, so
  raster and DXF share one config surface.
- `despeckle_contours`: replaces `png2dxf.py`'s own `--min-area-px` filter.
- `build_hierarchy`, `simplify_contours`, `validate`: as in 3.2's
  rationale and 3.4.

`cli.py`: extend the directory-glob (`input_path.glob("*.dxf")`) to also
match `*.png`, `*.jpg`, `*.jpeg` in directory mode. Single-file mode
already dispatches purely on the resolved path, so no change needed
there beyond what `run_pipeline` does internally.

### 3.4 `validate.py` addition: thin-feature check

New, because nothing in the current pipeline checks feature width against
material thickness (only hole *diameter* is checked today). Mirrors the
operating doc's mandatory rule (section 5: thinnest feature must exceed
2× material thickness):

- `validate()` gains an optional `min_feature_width_mm: float | None` field
  on the `stats` dict it already receives (populated only for raster
  input, from 3.2 step 7; absent/`None` for DXF input — DXF has no
  raster-derived width measurement, so the check is skipped for it,
  matching current behavior for vector input).
- If present: `< material_thickness` → critical ("cannot cut: thinnest
  feature Xmm < thickness Ymm"); `< 2 * material_thickness` → warning
  ("risky: thinnest feature Xmm < 2x thickness Ymm"). Same wording style
  as existing hole-diameter warnings in `validate.py`.

### 3.5 Config additions (`config.py`)

```python
class RasterConfig(BaseModel):
    pixels_per_mm: float | None = None   # used only if width/height not given on CLI
    threshold: int | None = None          # None = Otsu auto
    invert: bool = False
    min_area_px: float = 20.0
    smooth_sigma: float = 1.0
    prune_mm: float = 5.0                 # skeleton spur-pruning distance for measure_min_width
```

Added to `Config` as `raster: RasterConfig = RasterConfig()`.

### 3.6 CLI additions (`cli.py`)

New options, only meaningful for raster input (validated/ignored for
`.dxf`):

- `-w/--width-mm FLOAT` and `-H/--height-mm FLOAT` (mutually exclusive,
  `click.option` with a manual check — `click` doesn't have a built-in
  mutually-exclusive group) — target size in mm; overrides
  `raster.pixels_per_mm` for that run.
- `--px-per-mm FLOAT` — direct override of `config.raster.pixels_per_mm`.
- `--invert` — flag, overrides `config.raster.invert`.
- `--raster-threshold INT` — overrides `config.raster.threshold`.

`_process_one` additionally calls `render_preview(...)` right after a
successful raster read (writing `<stem>_KIEMTRA.png` next to the output
DXF) — unconditionally for raster input, never for DXF input. This is
the one piece of shop-facing behavior from `png2dxf.py` that isn't a
pipeline stage and stays as a direct CLI-level call.

Existing `report.info` (already an arbitrary `dict[str, int|float]`,
already auto-printed by `cli.py`) gets the raster-specific keys added
into `stats` in `read_raster`/`run_pipeline`: `width_mm`, `height_mm`,
`dpi`, `min_feature_width_mm`, `n_parts`, `threshold`, `mm_per_px` — no
changes needed to the printing code in `cli.py`, it already iterates
`report.info`.

### 3.7 Dependencies (`pyproject.toml`)

Add: `Pillow`, `opencv-python-headless`, `scikit-image`, `scipy` (all
already used by the reference `png2dxf.py`, versions to be pinned
loosely the same way existing deps are, e.g. `opencv-python-headless>=4.9`).

## 4. Error handling

- No contours found after tracing → same as an empty-parts DXF result
  today: `validate()` already emits `"Output is empty..."` as critical.
  No new code path needed.
- Corrupt/unreadable image file → let `PIL.UnidentifiedImageError` /
  `OSError` propagate; `cli.py`'s existing top-level `except Exception`
  already converts this to `System error: ...` / exit code 3, same as any
  other unexpected read failure.

## 5. Testing

- `tests/` gets a new `test_raster.py`:
  - Synthetic PNG built in-test with Pillow: a filled square with a
    circular hole, plus a deliberately thin connecting bar.
  - `read_raster` → assert one `Part` after `build_hierarchy` with one
    interior hole (hierarchy reuse working).
  - Full `run_pipeline` on that synthetic file → assert `report.level`
    reflects the thin bar (warning or critical depending on configured
    `material_thickness` in the test) and that `simplify` reduced node
    count while `dev_pct`-equivalent stayed under the configured
    `max_area_deviation_pct`.
  - Assert `RASTER_POSSIBLE_INVERTED` fires on an inverted synthetic image
    and not on a normal one.
  - Assert `render_preview` writes a file and it's a valid PNG (size
    matches input mask).
- No test asserts exact pixel-for-pixel output of the preview image
  (that would be brittle) — only that it exists, is valid, and its size
  matches the source mask.

## 6. Open questions resolved during brainstorming (for reference)

- Input assumption: binarizable black/white line-art, not photos or
  multi-color gradients.
- Simplify target: minimal node count within the existing
  `max_area_deviation_pct` guard — no new tolerance model needed, reuse
  `SimplifyConfig` as-is.
- CLI UX: auto-detect by file extension, no separate subcommand.
