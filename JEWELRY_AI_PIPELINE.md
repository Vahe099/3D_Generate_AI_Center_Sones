# Jewelry AI — Stone Shape Transformation Pipeline

## Project Goal

Transform a source `.3dm` jewelry file into a new stone shape variant by replacing only the center stone assembly, while preserving all shared ring geometry exactly.

**Example:**
```
Input:  3dm/AD_1/AD2_ArtDeco RD P.3dm  +  instruction: "Change center stone to Pear"
Output: AD2_ArtDeco PE P.3dm
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

## Supported Stone Shapes

| Abbreviation | Full Name | Key geometric characteristic |
|---|---|---|
| `RD` | Round | Circular outline, dome profile |
| `OV` | Oval | Elliptical outline, elongated along Y |
| `PE` | Pear | Teardrop outline, pointed end toward wearer |
| `EM` | Emerald | Rectangular with cut corners, step-cut pavilion |
| `PR` | Princess | Square outline, sharp corners |
| `MQ` | Marquise | Biconcave oval outline, two pointed ends |
| `AS` | Asscher | Square with deep cut corners, step-cut (like Emerald but square) |
| `CU` | Cushion | Rounded square/rectangle, pillow shape |
| `ELCU` | Elongated Cushion | Cushion with elongated rectangular footprint |
| `RA` | Radiant | Rectangular with cut corners, brilliant facets |

Each shape has a unique bounding box, footprint, and prong layout. No two shapes share mutable geometry.

---

## Dataset Structure

```
3dm/
  AD_1/
    AD2_ArtDeco RD P.3dm
    AD2_ArtDeco OV P.3dm
    AD2_ArtDeco PE P.3dm
    AD2_ArtDeco MQ P.3dm
    ...
  AD_2/
    ...
  ER1_Halo/
    ...
  ER2_Halo/
    ...
```

Each subdirectory of `3dm/` is a **ring family** — all `.3dm` files inside share the same shank, basket, and setting style, differing only in center stone shape.

The number of variants per family is not fixed. A family can have as few as 2 or as many as 10+ shapes.

## File Naming Convention

Filenames contain a **stone code** as a space-separated or underscore-separated token:

| Code | Full Name |
|---|---|
| `RD` | Round |
| `OV` | Oval |
| `PE` | Pear |
| `EM` | Emerald |
| `PR` | Princess |
| `MQ` | Marquise |
| `AS` | Asscher |
| `CU` | Cushion |
| `ELCU` | Elongated Cushion |
| `RA` | Radiant |

**Examples:**
```
AD2_ArtDeco RD P.3dm    → Round
AD2_ArtDeco ELCU P.3dm  → Elongated Cushion
ER1_Halo_PE.3dm         → Pear
ER1_Halo_MQ.3dm         → Marquise
```

The pipeline tokenises the filename stem by both underscores and spaces, then scans for a known stone code. No fixed filename template is assumed. Adding a new shape only requires dropping a new `.3dm` file in the family folder and re-running `build-library`.

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
fingerprint = (centroid_xyz, bounding_box_size, volume, diagonal,
               n_surfaces, surface_degree, control_point_count,
               first_control_point_xyz)
sig_hash    = MD5(fingerprint)  # 12-char hex, stable across files
```
Objects whose `sig_hash` appears in **every** file in the family → **static**.
Objects whose `sig_hash` appears in only some files → **mutable**.

Diff is always scoped within a single family — objects are never compared across families.

---

## Pipeline Architecture

```
build-library (run once per dataset, or per new family)
─────────────────────────────────────────────────────────────
  3dm/
    AD_1/  ──→  parse N variants  ──→  cross-file diff  ──→  shape_library/AD_1/
    AD_2/  ──→  parse N variants  ──→  cross-file diff  ──→  shape_library/AD_2/
    ER1_Halo/ → parse N variants  ──→  cross-file diff  ──→  shape_library/ER1_Halo/
    ...
                                                               ├── classification.json
                                                               └── shape_index.json

  Each family is diffed independently.
  Static hashes = objects present in ALL files of that family.
  Mutable hashes = objects unique to some files (one per shape).


transform (per request)
─────────────────────────────────────────────────────────────
  source.3dm  (e.g. 3dm/AD_1/AD2_ArtDeco RD P.3dm)
       │
       ▼
  Auto-detect family from parent folder name → load shape_library/AD_1/
       │
       ▼
  From source: keep only objects whose sig_hash is in static_hashes
  From reference[target shape]: take only objects in mutable_object_indices
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
├── JEWELRY_AI_PIPELINE.md     # This file
├── 3dm/                       # Input dataset
│   ├── AD_1/
│   │   ├── AD2_ArtDeco RD P.3dm
│   │   ├── AD2_ArtDeco OV P.3dm
│   │   └── ...
│   ├── ER1_Halo/
│   │   └── ...
│   └── ...
└── shape_library/             # Generated by build-library (one subfolder per family)
    ├── AD_1/
    │   ├── classification.json
    │   └── shape_index.json
    ├── ER1_Halo/
    │   ├── classification.json
    │   └── shape_index.json
    └── ...
```

---

## CLI Usage

### Step 1 — Build the library (one-time, or when new files are added)
```bash
# Build all families
python jewelry_transform.py build-library --dataset 3dm/

# Build a single family
python jewelry_transform.py build-library --dataset 3dm/ --family AD_1
```
Scans `3dm/` for family subfolders, diffs each family independently, writes `shape_library/{family}/`.

### Step 2 — Transform
```bash
python jewelry_transform.py transform \
  --source "3dm/AD_1/AD2_ArtDeco RD P.3dm" \
  --shape PE \
  --output "AD2_ArtDeco PE P.3dm"
```
Family is auto-detected from the parent directory of `--source`. Override with `--family` if the source is not inside the dataset folder.

Accepts stone code (`PE`) or full name (`Pear`) for `--shape`.

### Diagnostic — diff report
```bash
# Entire dataset
python jewelry_transform.py diff-report --dataset 3dm/

# Single family
python jewelry_transform.py diff-report --dataset 3dm/ --family AD_1
```
**Run this first** when adding new files to verify the diff is correct.

### Custom library directory
```bash
python jewelry_transform.py build-library --dataset 3dm/ --library-dir my_lib/
python jewelry_transform.py transform --source ... --shape PE --output ... --library-dir my_lib/
```

---

## Python API (importable)

```python
from jewelry_transform import build_library, transform, diff_report, scan_dataset
from pathlib import Path

# Scan to see what families exist
families = scan_dataset(Path("3dm"))  # {"AD_1": [...], "ER1_Halo": [...], ...}

# Build entire dataset
build_library(dataset_root=Path("3dm"), library_dir=Path("shape_library"))

# Build one family
build_library(dataset_root=Path("3dm"), library_dir=Path("shape_library"), family_filter="AD_1")

# Transform (family auto-detected from source path)
transform(
    source_path="3dm/AD_1/AD2_ArtDeco RD P.3dm",
    target_shape="PE",          # code or full name, case-insensitive
    output_path="AD2_ArtDeco PE P.3dm",
    library_dir=Path("shape_library"),
)

# Diff report
diff_report(dataset_root=Path("3dm"), family_filter="AD_1")
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

### "No ring family folders with ≥2 .3dm files found"
The `--dataset` path is wrong, or the family subfolders don't exist yet. Confirm that `3dm/{family}/*.3dm` files are in place before running `build-library`.

### "Too many objects classified as mutable"
Fingerprint tolerance may be too tight — some static objects may have sub-millimeter positional drift between files. Fix: loosen `round(..., 4)` to `round(..., 2)` in `_bbox_fingerprint()`.

### "Shape X not found in library"
No `.3dm` file for that stone code was present when `build-library` ran. Add the missing file to the family folder and re-run `build-library --family {family}`.

### Family auto-detection picks the wrong family
If your source file is not inside `3dm/{family}/`, pass `--family {name}` explicitly to `transform`.

### Output file has wrong object count
Run `diff-report --family {name}` and check `mutable_object_count` per shape in `shape_index.json`. Counts differ legitimately when stone shapes use different numbers of prongs (e.g. Marquise has 2 tip prongs + 2 side prongs vs 4 corner prongs for Princess).

### Objects appear in wrong position
All files in a family must share the same world origin. If one file was modeled at a different position, normalize it in Rhino before running the pipeline.

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
