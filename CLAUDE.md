# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python 3.13 project for AI-driven 3D generation, focused on jewelry processing pipelines. The entry point is `main.py` (currently empty — implementation is in progress).

## Environment Setup

```powershell
# Activate virtual environment
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Running the Project

```powershell
python main.py
```

## Running Tests

```powershell
python -m pytest test_pipeline.py -v
# Single test:
python -m pytest test_pipeline.py::TestClassName::test_method_name -v
```

## Code Architecture

- `main.py` — top-level entry point; orchestrates the pipeline
- `jewelry_transform.py` — core transformation logic for jewelry 3D model processing
- `test_pipeline.py` — test suite for the pipeline

The project targets Python 3.13 and uses a `.venv` virtualenv. Sole dependency: `pip install rhino3dm`.

### Dataset layout

```
3dm/
  {family}/          # one subfolder per ring design (AD_1, ER1_Halo, …)
    *.3dm            # one file per stone shape variant
shape_library/
  {family}/
    classification.json   # static_hashes, mutable_hashes for this family
    shape_index.json      # per-shape mutable object indices + source file paths
```

Each family is processed independently. The diff (static vs mutable) is scoped within a single family.

---

# Jewelry AI — Stone Shape Transformation Pipeline

## Project Goal

Transform a source `.3dm` jewelry file into a new stone shape variant by replacing only the center stone assembly, while preserving all shared ring geometry exactly.

**Example:**
```
Input:  ER1_Halo_Round.3dm  +  instruction: "Change center stone to Pear"
Output: ER1_Halo_Pear.3dm
```

---

## Ring Structure (from reference file)

The ring consists of two distinct geometry groups:

### Static geometry — never changes across variants
| Component | Description |
|---|---|
| Shank / band | The ring finger tube, tapered or split-style |
| Shank decorative elements | Any engraving, milgrain, or profile curves on the band |
| Side stone channels | Rows of accent stones flanking the center setting |
| Pave / melee accents | Small stones on the shank shoulders |

### Mutable geometry — changes per stone shape
| Component | Description |
|---|---|
| Center stone | The main gem (Brep, shape-specific dimensions) |
| Prongs | 4–6 corner/tip holders, positions depend on stone outline |
| Basket / seat | The mounting cup directly under the stone |
| Halo frame | The metal setting ring surrounding the stone |
| Halo melee stones | The small round stones embedded in the halo frame |

> **Note:** In the reference file, green = metal geometry, blue = stone geometry.
> Both colors/materials appear in both static and mutable groups.

---

## Reference Dimensions (from Rhino viewport)

| Measurement | Value |
|---|---|
| Ring size (inner diameter) | 6.5 (US size 6.5) |
| Stone height above finger plane | 6.97 mm |
| Shank width | 1.50 mm |
| World origin | Center of ring, at finger plane (Z = 0) |
| Ring axis | Z-axis (finger passes along X-axis) |

---

## Supported Stone Shapes — Phase 1

| Shape | Key geometric characteristic |
|---|---|
| `Round` | Circular outline, dome profile |
| `Oval` | Elliptical outline, elongated along Y |
| `Pear` | Teardrop outline, pointed end toward wearer |
| `Emerald` | Rectangular with cut corners (octagonal plan), step-cut pavilion |
| `Princess` | Square outline, sharp corners |
| `Marquise` | Biconcave oval outline, two pointed ends |

Each shape has a unique bounding box, footprint, and prong layout. No two shapes share mutable geometry.

---

## File Naming Convention

```
ER1_Halo_{Shape}.3dm
```

- `ER1` = Engagement Ring design 1
- `Halo` = halo setting style
- `{Shape}` = one of: Round, Oval, Pear, Emerald, Princess, Marquise

---

## Technical Constraints

### File format
- Rhino `.3dm`, version 7
- All geometry is **NURBS surfaces / polysurfaces (Breps)**
- No meshes in the model
- No consistent layer names — objects are not reliably named or layered

### Coordinate system
- All 6 variants share the **same world origin and orientation**
- The ring center sits at world origin (0, 0, 0)
- The finger hole opens along the X-axis
- Z is up (stone sits above Z = 0)
- This consistency is critical — it makes spatial fingerprinting reliable

### Object identity
Because there are no layer names, objects are identified by **geometric fingerprint**:
```
fingerprint = (centroid_xyz, bounding_box_size, n_surfaces, surface_degree)
sig_hash    = MD5(fingerprint)  # 12-char hex, stable across files
```
Objects with the same `sig_hash` in all 6 files → **static**.
Objects with a `sig_hash` unique to one file → **mutable**.

---

## Pipeline Architecture

```
build-library (run once)
─────────────────────────────────────────────────────
  6× .3dm files
       │
       ▼
  Parse all files → fingerprint every NURBS object
       │
       ▼
  Cross-file diff → classify static vs mutable
       │
       ▼
  shape_library/
    ├── classification.json   # static_hashes, mutable_hashes
    └── shape_index.json      # per-shape mutable object positions


transform (per request)
─────────────────────────────────────────────────────
  source.3dm  +  target shape name
       │
       ▼
  Load classification.json + shape_index.json
       │
       ▼
  From source: keep only static objects
  From reference[target]: take only mutable objects
       │
       ▼
  Merge → write output.3dm
```

---

## File Structure

```
project/
├── jewelry_transform.py       # Main pipeline (CLI + importable API)
├── test_pipeline.py           # Full test suite with synthetic .3dm files
├── shape_library/             # Generated by build-library (gitignore optional)
│   ├── classification.json
│   └── shape_index.json
└── JEWELRY_AI_PIPELINE.md     # This file
```

---

## CLI Usage

### Step 1 — Build the library (one-time)
```bash
python jewelry_transform.py build-library --files "ER1_Halo_*.3dm"
```
Parses all 6 files, diffs them, writes `shape_library/`.

### Step 2 — Transform
```bash
python jewelry_transform.py transform \
  --source ER1_Halo_Round.3dm \
  --shape Pear \
  --output ER1_Halo_Pear.3dm
```

### Diagnostic — diff report
```bash
python jewelry_transform.py diff-report --files "ER1_Halo_*.3dm"
```
Prints what was classified as static vs mutable, with bounding box positions.
**Run this first** when working with new files to verify the diff is correct.

### Custom library directory
```bash
python jewelry_transform.py build-library --files "*.3dm" --library-dir my_lib/
python jewelry_transform.py transform --source ... --library-dir my_lib/
```

---

## Python API (importable)

```python
from jewelry_transform import build_library, transform, diff_report
from pathlib import Path

# Build
build_library(["ER1_Halo_Round.3dm", "ER1_Halo_Pear.3dm", ...])

# Transform
transform(
    source_path="ER1_Halo_Round.3dm",
    target_shape="Pear",          # case-insensitive
    output_path="ER1_Halo_Pear.3dm",
    library_dir=Path("shape_library")
)
```

---

## Dependencies

```bash
pip install rhino3dm
```

| Package | Version tested | Purpose |
|---|---|---|
| `rhino3dm` | 8.17.0 | Read/write `.3dm`, NURBS geometry access |
| Python | 3.10+ | f-strings, `match`, `dataclass` |

No other dependencies. All geometry math uses the `rhino3dm` API directly.

---

## Troubleshooting

### "Too many objects classified as mutable"
The fingerprint tolerance may be too tight. Some static objects may have sub-millimeter positional drift across files. Fix: loosen the `round(..., 4)` precision in `_bbox_fingerprint()` to `round(..., 2)`.

### "Shape X not found in library"
The source file for that shape was not included when running `build-library`. Re-run with all 6 files.

### Output file has wrong object count
Run the diff report and check `mutable_object_count` per shape in `shape_index.json`. If counts differ between shapes, some shapes may have a different number of setting components (e.g. Marquise uses 2 tip prongs + 2 side prongs vs 4 corner prongs for Princess).

### Objects appear in wrong position
Check that all source files share the same world origin. If one file was modeled in a different position, normalize it in Rhino before running the pipeline.

---

## Phase 2 — Future Extensions

| Feature | Approach |
|---|---|
| New ring styles (solitaire, three-stone) | Add style prefix to naming convention; separate library per style |
| Parametric sizing (resize ring diameter) | Add `--ring-size` argument; apply radial scale transform to shank |
| Natural language input | Add thin Claude API call: `"change stone to pear"` → `shape="Pear"` |
| New stone shapes | Add reference `.3dm`, re-run `build-library` |
| Validation in Rhino | Use `rhino:capture_viewport` MCP tool to render output and verify visually |

---

## Test Suite

```bash
python test_pipeline.py
```

Runs 7 tests using synthetic `.3dm` files (no real files needed):
1. Synthetic file creation — 6 files, 11 objects each
2. Parsing — correct object count per file
3. Classification — 4 static, 7 mutable per shape
4. Library build — JSON files written correctly
5. Transform Round → Pear — output file created
6. Geometry verification — static preserved, Pear mutable inserted
7. Round-trip all 6 shapes — all produce valid output

All 7 tests pass on `rhino3dm==8.17.0`.