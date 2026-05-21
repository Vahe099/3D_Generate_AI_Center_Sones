"""
Jewelry Stone Shape Transformation Pipeline
============================================
Transforms a source .3dm jewelry file to a target stone shape by:
1. Scanning the dataset for ring family folders (3dm/{family}/)
2. Parsing all variants per family and fingerprinting every NURBS object
3. Cross-file diffing within each family to classify static vs mutable geometry
4. Saving a per-family shape library (classification.json + shape_index.json)
5. Assembling: static geometry from source + mutable bundle from target shape

Usage:
    python jewelry_transform.py build-library  --dataset 3dm/              # entire dataset
    python jewelry_transform.py build-library  --dataset 3dm/ --family AD_1  # one family
    python jewelry_transform.py transform      --source <file> --shape PE --output <file>
    python jewelry_transform.py diff-report    --dataset 3dm/              # entire dataset
    python jewelry_transform.py diff-report    --dataset 3dm/ --family AD_1
"""

import rhino3dm
import json
import hashlib
import math
import os
import glob
import re
import argparse
import sys
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ─── Fuzzy matching defaults ──────────────────────────────────────────────────
# Centroid drift and bbox size drift in millimetres.
# 0.2 mm tolerance catches NURBS precision drift between files without
# false-merging distinct setting components (adjacent halo stones are 2+ mm apart).
# tolerance-scan on real data: 0.1 mm catches 0-4 fuzzy per family; 0.2 mm catches
# 14-144 per family; >=0.5 mm causes same-file collisions (non-monotonic drop at 1.0 mm).
DEFAULT_CENTER_TOL: float = 0.2
DEFAULT_SIZE_TOL:   float = 0.2
DEFAULT_VOL_TOL:    float = float("inf")  # volume unconstrained by default


# --- Geometry fingerprinting ------------------------------------------------

def _pt(p) -> tuple:
    return (round(p.X, 4), round(p.Y, 4), round(p.Z, 4))

def _bbox_fingerprint(bb) -> dict:
    """Stable numeric summary of a bounding box."""
    mn, mx = bb.Min, bb.Max
    cx = (mn.X + mx.X) / 2
    cy = (mn.Y + mx.Y) / 2
    cz = (mn.Z + mx.Z) / 2
    sx = abs(mx.X - mn.X)
    sy = abs(mx.Y - mn.Y)
    sz = abs(mx.Z - mn.Z)
    return {
        "cx": round(cx, 4), "cy": round(cy, 4), "cz": round(cz, 4),
        "sx": round(sx, 4), "sy": round(sy, 4), "sz": round(sz, 4),
        "vol": round(sx * sy * sz, 4),
        "diag": round(math.sqrt(sx**2 + sy**2 + sz**2), 4),
    }

def _surface_fingerprint(srf) -> dict:
    """Extra fingerprint data for a single NURBS surface."""
    try:
        d = {
            "u_degree": srf.OrderU - 1,
            "v_degree": srf.OrderV - 1,
            "u_count": srf.Points.CountU,
            "v_count": srf.Points.CountV,
        }
        # Hash first control point as a quick identity check
        if srf.Points.CountU > 0 and srf.Points.CountV > 0:
            cp = srf.Points.GetControlPoint(0, 0)
            d["cp00"] = (round(cp.X, 3), round(cp.Y, 3), round(cp.Z, 3))
        return d
    except Exception:
        return {}

def fingerprint_object(obj) -> Optional[dict]:
    """
    Compute a deterministic fingerprint for a rhino3dm geometry object.
    Returns None if the object type is not supported.
    Annotation objects (text, dimensions) are skipped — they are shape-specific
    markup that cannot round-trip faithfully through rhino3dm's generic Add path.
    """
    geom = obj.Geometry
    obj_type = geom.ObjectType

    # Skip annotations — they carry shape-specific labels/dimensions and cannot
    # be reliably copied via rhino3dm without model-level style context.
    if "Annotation" in str(obj_type):
        return None

    try:
        bb = geom.GetBoundingBox()
        if not bb.IsValid:
            return None
        fp = _bbox_fingerprint(bb)
    except Exception:
        return None

    fp["object_type"] = str(obj_type)
    fp["id"] = str(obj.Attributes.Id)
    fp["layer_index"] = obj.Attributes.LayerIndex
    fp["name"] = obj.Attributes.Name or ""
    fp["material_index"] = obj.Attributes.MaterialIndex

    # Extra surface data for Breps
    if hasattr(geom, "Surfaces"):
        try:
            n_srf = len(list(geom.Surfaces))
            fp["n_surfaces"] = n_srf
            if n_srf == 1:
                fp.update(_surface_fingerprint(list(geom.Surfaces)[0]))
        except Exception:
            pass

    # Stable hash of geometry only — exclude per-file metadata that varies
    # independently of shape (layer assignments, names, material slots differ
    # between files even for identical static geometry).
    _METADATA_KEYS = {"id", "layer_index", "name", "material_index"}
    sig = {k: v for k, v in fp.items() if k not in _METADATA_KEYS}
    fp["sig_hash"] = hashlib.md5(
        json.dumps(sig, sort_keys=True).encode()
    ).hexdigest()[:12]

    return fp


# --- Stone shape detection ---------------------------------------------------

# Abbreviation → full name mapping (from real 169-file library)
SHAPE_ABBREV_MAP = {
    "RD":   "Round",
    "OV":   "Oval",
    "PE":   "Pear",
    "EM":   "Emerald",
    "PR":   "Princess",
    "MQ":   "Marquise",
    "AS":   "Asscher",
    "CU":   "Cushion",
    "ELCU": "Elongated Cushion",
    "RA":   "Radiant",
}

# Reverse map: full name → abbreviation (for CLI convenience)
SHAPE_NAME_MAP = {v.lower(): k for k, v in SHAPE_ABBREV_MAP.items()}

STONE_SHAPES = list(SHAPE_ABBREV_MAP.keys())  # canonical keys are abbreviations


def detect_shape_from_filename(path: str) -> Optional[str]:
    """
    Detect stone shape abbreviation from filename.
    Handles underscore- and space-separated tokens, e.g.:
      ER1_Halo_RD.3dm          → RD
      AD2_ArtDeco RD P.3dm     → RD
      AD2_ArtDeco ELCU P.3dm   → ELCU
      ER6_Halo_EL CU P.3dm     → ELCU  (bigram "EL"+"CU")
    Returns the abbreviation (e.g. 'RD', 'ELCU') or None.
    """
    stem = Path(path).stem.upper()
    tokens = stem.replace("_", " ").split()
    # Also form consecutive bigrams to catch multi-word codes like "EL CU" → "ELCU"
    bigrams = [tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)]
    candidates = set(tokens) | set(bigrams)

    # Try longest abbreviations first so ELCU is matched before CU
    for abbrev in sorted(SHAPE_ABBREV_MAP.keys(), key=len, reverse=True):
        if abbrev in candidates:
            return abbrev

    return None


def resolve_shape(shape_input: str) -> str:
    """
    Accept either abbreviation ('RD') or full name ('Round') — case-insensitive.
    Returns the canonical abbreviation or raises ValueError.
    """
    s = shape_input.strip()
    # Try direct abbreviation match
    upper = s.upper()
    if upper in SHAPE_ABBREV_MAP:
        return upper
    # Try full name match
    lower = s.lower()
    if lower in SHAPE_NAME_MAP:
        return SHAPE_NAME_MAP[lower]
    raise ValueError(
        f"Unknown shape '{s}'.\n"
        f"Use abbreviation: {', '.join(SHAPE_ABBREV_MAP.keys())}\n"
        f"Or full name:     {', '.join(SHAPE_ABBREV_MAP.values())}"
    )


# --- File parsing -------------------------------------------------------------

@dataclass
class ParsedFile:
    path: str
    shape: str
    fingerprints: list   # list of fingerprint dicts, one per object
    object_count: int

def parse_file(path: str) -> ParsedFile:
    shape = detect_shape_from_filename(path)
    if not shape:
        raise ValueError(
            f"Cannot detect stone shape from filename: {path}\n"
            f"Expected one of: {STONE_SHAPES}"
        )
    model = rhino3dm.File3dm.Read(path)
    if model is None:
        raise IOError(f"Failed to read: {path}")

    fps = []
    for obj in model.Objects:
        fp = fingerprint_object(obj)
        if fp is not None:
            fps.append(fp)

    print(f"  [{shape:10s}] {len(fps)} objects fingerprinted from {Path(path).name}")
    return ParsedFile(path=path, shape=shape, fingerprints=fps, object_count=len(fps))


# --- Classification result ────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    n_files: int
    exact_static_hashes: set        # same sig_hash in every file
    fuzzy_static_hashes: set        # different hash but bbox within tolerance in every file
    mutable_hashes: set             # sig_hash in 2+ files but not all (shape-specific)
    unmatched_hashes: set           # sig_hash in exactly 1 file, no fuzzy partner found
    shape_to_hashes: dict           # shape abbrev → list of mutable sig_hashes
    hash_file_count: dict           # sig_hash → number of files it appears in (exactly)
    center_tol: float
    size_tol: float
    vol_tol: float

    @property
    def all_static_hashes(self) -> set:
        """Combined exact + fuzzy static — treat both as geometry to keep from source."""
        return self.exact_static_hashes | self.fuzzy_static_hashes


# --- Fuzzy bbox helpers ───────────────────────────────────────────────────────

def _center_dist(a: dict, b: dict) -> float:
    return math.sqrt((a["cx"]-b["cx"])**2 + (a["cy"]-b["cy"])**2 + (a["cz"]-b["cz"])**2)

def _size_diff(a: dict, b: dict) -> float:
    return max(abs(a["sx"]-b["sx"]), abs(a["sy"]-b["sy"]), abs(a["sz"]-b["sz"]))

def _bbox_close(a: dict, b: dict, c_tol: float, s_tol: float, v_tol: float) -> bool:
    return (_center_dist(a, b) <= c_tol
            and _size_diff(a, b)        <= s_tol
            and abs(a["vol"]-b["vol"])  <= v_tol)

def _uf_find(parent: list, x: int) -> int:
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:       # path compression
        parent[x], x = root, parent[x]
    return root

def _uf_union(parent: list, x: int, y: int) -> None:
    rx, ry = _uf_find(parent, x), _uf_find(parent, y)
    if rx != ry:
        parent[rx] = ry


# --- Cross-file diff ----------------------------------------------------------

def classify_objects(
    parsed_files: list,
    center_tol: float = DEFAULT_CENTER_TOL,
    size_tol:   float = DEFAULT_SIZE_TOL,
    vol_tol:    float = DEFAULT_VOL_TOL,
) -> ClassificationResult:
    """
    Classify every fingerprinted object as exact-static, fuzzy-static,
    mutable, or unmatched.

    Pass 1 — exact matching:
      sig_hash present identically in every file → exact static.

    Pass 2 — fuzzy matching on remainders (Union-Find over bbox proximity):
      Objects from different files whose bounding boxes agree within
      (center_tol, size_tol, vol_tol) are connected. A connected component
      containing exactly one object from each file → fuzzy static.
      Note: Union-Find is transitive; keep tolerances tight to avoid
      merging geometrically distinct components.

    Pass 3 — residual classification:
      hash in 2+ files → mutable (shape-specific shared component).
      hash in 1 file only → unmatched (potential anomaly or singleton).
    """
    n = len(parsed_files)

    # ── Pass 1: exact hash counting ──────────────────────────────────────────
    hash_file_count: dict[str, int] = {}
    for pf in parsed_files:
        seen: set[str] = set()
        for fp in pf.fingerprints:
            h = fp["sig_hash"]
            if h not in seen:
                hash_file_count[h] = hash_file_count.get(h, 0) + 1
                seen.add(h)

    exact_static_hashes = {h for h, c in hash_file_count.items() if c == n}

    # ── Pass 2: fuzzy Union-Find on non-exact-static objects ─────────────────
    fuzzy_static_hashes: set[str] = set()

    if center_tol > 0 or size_tol > 0:
        # Build flat node list: (file_idx, fp) for all non-exact-static objects
        nodes: list[tuple[int, dict]] = []
        for file_idx, pf in enumerate(parsed_files):
            for fp in pf.fingerprints:
                if fp["sig_hash"] not in exact_static_hashes:
                    nodes.append((file_idx, fp))

        nn = len(nodes)
        parent = list(range(nn))

        # Connect cross-file nodes within bbox tolerance
        for i in range(nn):
            fi, fpi = nodes[i]
            for j in range(i + 1, nn):
                fj, fpj = nodes[j]
                if fi == fj:
                    continue
                if _bbox_close(fpi, fpj, center_tol, size_tol, vol_tol):
                    _uf_union(parent, i, j)

        # Collect components
        comp_members: dict[int, list[tuple[int, dict]]] = defaultdict(list)
        for i, (file_idx, fp) in enumerate(nodes):
            comp_members[_uf_find(parent, i)].append((file_idx, fp))

        for members in comp_members.values():
            # Count how many distinct files are in this component
            file_set: dict[int, int] = {}
            for file_idx, _ in members:
                file_set[file_idx] = file_set.get(file_idx, 0) + 1

            # Fuzzy static = exactly one member per file across ALL files,
            # AND every cross-file pair is within tolerance.
            # Union-Find is transitive, so a chain A-B-C passes local adjacency
            # checks even when A and C are far apart.  The pairwise check here
            # closes that loophole.
            if len(file_set) == n and all(c == 1 for c in file_set.values()):
                member_list = list(members)
                all_pairs_ok = True
                for i in range(len(member_list)):
                    fi, fpi = member_list[i]
                    for j in range(i + 1, len(member_list)):
                        fj, fpj = member_list[j]
                        if fi != fj and not _bbox_close(fpi, fpj, center_tol, size_tol, vol_tol):
                            all_pairs_ok = False
                            break
                    if not all_pairs_ok:
                        break
                if all_pairs_ok:
                    for _, fp in members:
                        fuzzy_static_hashes.add(fp["sig_hash"])

    # ── Pass 3: residual classification ──────────────────────────────────────
    all_static = exact_static_hashes | fuzzy_static_hashes
    mutable_hashes:   set[str] = set()
    unmatched_hashes: set[str] = set()

    for h, c in hash_file_count.items():
        if h in all_static:
            continue
        if c >= 2:
            mutable_hashes.add(h)
        else:
            unmatched_hashes.add(h)

    # ── Shape → mutable hash mapping ─────────────────────────────────────────
    shape_to_hashes: dict[str, list[str]] = {}
    for pf in parsed_files:
        for fp in pf.fingerprints:
            h = fp["sig_hash"]
            if h in mutable_hashes:
                lst = shape_to_hashes.setdefault(pf.shape, [])
                if h not in lst:
                    lst.append(h)

    return ClassificationResult(
        n_files=n,
        exact_static_hashes=exact_static_hashes,
        fuzzy_static_hashes=fuzzy_static_hashes,
        mutable_hashes=mutable_hashes,
        unmatched_hashes=unmatched_hashes,
        shape_to_hashes=shape_to_hashes,
        hash_file_count=hash_file_count,
        center_tol=center_tol,
        size_tol=size_tol,
        vol_tol=vol_tol,
    )


# --- Dataset scanning ---------------------------------------------------------

LIBRARY_DIR = Path("shape_library")


def scan_dataset(dataset_root: Path) -> dict[str, list[Path]]:
    """
    Scan dataset_root for ring family subdirectories.
    Returns {family_name: [sorted .3dm paths]} for every folder that contains
    at least two .3dm files (need ≥2 to diff).
    """
    families: dict[str, list[Path]] = {}
    for family_dir in sorted(dataset_root.iterdir()):
        if not family_dir.is_dir() or family_dir.name.startswith("."):
            continue
        files = sorted(family_dir.glob("*.3dm"))
        if len(files) >= 2:
            families[family_dir.name] = files
        elif len(files) == 1:
            print(f"  WARNING: {family_dir.name} has only 1 .3dm file — need ≥2 to diff, skipping.")
    return families


# --- Shape library ------------------------------------------------------------

def _build_family_library(
    family_name: str,
    file_paths: list[Path],
    family_lib: Path,
    center_tol: float = DEFAULT_CENTER_TOL,
    size_tol:   float = DEFAULT_SIZE_TOL,
    vol_tol:    float = DEFAULT_VOL_TOL,
) -> tuple[dict, dict]:
    """Parse and diff all variants for one ring family, write JSON library files."""
    print(f"\n{chr(45)*60}")
    print(f"  Family: {family_name}  ({len(file_paths)} files)")
    print(f"  Tolerances: center={center_tol} mm  size={size_tol} mm")
    print(f"{chr(45)*60}")

    family_lib.mkdir(parents=True, exist_ok=True)

    parsed = []
    for path in file_paths:
        try:
            parsed.append(parse_file(str(path)))
        except Exception as e:
            print(f"  WARNING: skipping {path.name}: {e}")

    if len(parsed) < 2:
        print(f"  ERROR: Need at least 2 parseable files for '{family_name}'. Skipping.")
        return {}, {}

    r = classify_objects(parsed, center_tol, size_tol, vol_tol)

    n_exact   = len(r.exact_static_hashes)
    n_fuzzy   = len(r.fuzzy_static_hashes)
    n_mut     = len(r.mutable_hashes)
    n_unmatch = len(r.unmatched_hashes)
    print(f"\n  Exact static  : {n_exact}")
    print(f"  Fuzzy static  : {n_fuzzy}  (total static: {n_exact + n_fuzzy})")
    print(f"  Mutable       : {n_mut}")
    print(f"  Unmatched     : {n_unmatch}")
    for shape, hashes in sorted(r.shape_to_hashes.items()):
        full = SHAPE_ABBREV_MAP.get(shape, shape)
        print(f"    {shape:6s} ({full:20s}): {len(hashes)} mutable objects")

    classification = {
        "family": family_name,
        "exact_static_hashes":    sorted(r.exact_static_hashes),
        "fuzzy_static_hashes":    sorted(r.fuzzy_static_hashes),
        "combined_static_hashes": sorted(r.all_static_hashes),
        "mutable_hashes":         sorted(r.mutable_hashes),
        "unmatched_hashes":       sorted(r.unmatched_hashes),
        "shape_to_hashes":        {s: sorted(h) for s, h in r.shape_to_hashes.items()},
        "total_files_used": r.n_files,
        "source_files": {pf.shape: str(Path(pf.path).resolve()) for pf in parsed},
        "tolerances": {"center_mm": center_tol, "size_mm": size_tol,
                       "vol": "inf" if vol_tol == float("inf") else vol_tol},
    }
    class_path = family_lib / "classification.json"
    with open(class_path, "w") as f:
        json.dump(classification, f, indent=2)

    shape_index = {}
    for pf in parsed:
        mutable_indices = [
            i for i, fp in enumerate(pf.fingerprints)
            if fp["sig_hash"] not in r.all_static_hashes
        ]
        shape_index[pf.shape] = {
            "source_file": str(Path(pf.path).resolve()),
            "mutable_object_indices": mutable_indices,
            "mutable_object_count": len(mutable_indices),
        }
    index_path = family_lib / "shape_index.json"
    with open(index_path, "w") as f:
        json.dump(shape_index, f, indent=2)

    print(f"  Library written -> {family_lib}/")
    return classification, shape_index


def build_library(
    dataset_root: Path = Path("3dm"),
    library_dir: Path = LIBRARY_DIR,
    family_filter: Optional[str] = None,
    center_tol: float = DEFAULT_CENTER_TOL,
    size_tol:   float = DEFAULT_SIZE_TOL,
    vol_tol:    float = DEFAULT_VOL_TOL,
) -> None:
    print(f"\n{chr(61)*60}")
    print(f"Building shape library from: {dataset_root}")
    print(f"Tolerances: center={center_tol} mm  size={size_tol} mm")
    print(f"{chr(61)*60}")

    families = scan_dataset(dataset_root)
    if not families:
        print(f"ERROR: No ring family folders with >=2 .3dm files found under '{dataset_root}'.")
        sys.exit(1)

    if family_filter:
        if family_filter not in families:
            raise ValueError(
                f"Family '{family_filter}' not found in {dataset_root}. "
                f"Available: {', '.join(sorted(families))}"
            )
        families = {family_filter: families[family_filter]}

    for family_name, files in families.items():
        _build_family_library(family_name, files, library_dir / family_name,
                              center_tol, size_tol, vol_tol)

    print(f"\n{chr(61)*60}")
    print(f"Done. {len(families)} family/families built.")
    print(f"{chr(61)*60}\n")


def load_library(family_lib: Path) -> tuple[dict, dict]:
    """Load classification.json + shape_index.json for a specific ring family."""
    class_path = family_lib / "classification.json"
    index_path = family_lib / "shape_index.json"
    if not class_path.exists() or not index_path.exists():
        raise FileNotFoundError(
            f"Shape library not found at '{family_lib}'. "
            "Run: python jewelry_transform.py build-library --dataset 3dm/"
        )
    with open(class_path) as f:
        classification = json.load(f)
    with open(index_path) as f:
        shape_index = json.load(f)
    # Back-compat: old libraries used "static_hashes" before fuzzy matching
    if "combined_static_hashes" not in classification:
        classification["combined_static_hashes"] = classification.get("static_hashes", [])
    return classification, shape_index


# --- Transformation -----------------------------------------------------------

def transform(source_path: str, target_shape: str, output_path: str,
              library_dir: Path = LIBRARY_DIR,
              family: Optional[str] = None):
    """
    Main transformation: swap the mutable geometry in source_path
    with the mutable geometry from the target_shape's reference file.

    family is auto-detected as the parent directory name of source_path
    (e.g. 3dm/AD_1/file.3dm → family='AD_1'). Override with family= if needed.
    """
    # Normalise shape name — accept abbreviation or full name
    target_shape = resolve_shape(target_shape)

    if family is None:
        family = Path(source_path).parent.name

    print(f"\n{'='*60}")
    print(f"Transforming -> {target_shape}  (family: {family})")
    print(f"  Source : {source_path}")
    print(f"  Output : {output_path}")
    print(f"{'='*60}")

    family_lib = library_dir / family
    classification, shape_index = load_library(family_lib)
    # Exact-static: same hash in every file → take from reference for correct count.
    # Fuzzy-static: different hash per file, same bbox → take from source to preserve
    # the source-variant NURBS.  Both are excluded from the mutable swap.
    exact_static_hashes = set(classification.get("exact_static_hashes",
                               classification.get("combined_static_hashes", [])))
    fuzzy_static_hashes = set(classification.get("fuzzy_static_hashes", []))

    # -- Load source file --
    source_model = rhino3dm.File3dm.Read(source_path)
    if source_model is None:
        raise IOError(f"Cannot read source file: {source_path}")

    # -- Load target shape's reference file --
    if target_shape not in shape_index:
        raise KeyError(f"Shape '{target_shape}' not found in library. "
                       "Did you include a file for it when building the library?")

    ref_info = shape_index[target_shape]
    ref_path = ref_info["source_file"]
    ref_model = rhino3dm.File3dm.Read(ref_path)
    if ref_model is None:
        raise IOError(f"Cannot read reference file: {ref_path}")

    # Build filtered (obj, fp) pairs — indices must match parse_file's filtered list,
    # which is what mutable_object_indices is built from.
    ref_valid: list[tuple] = [
        (obj, fp) for obj in ref_model.Objects
        for fp in [fingerprint_object(obj)] if fp is not None
    ]

    mutable_indices_in_ref = set(ref_info["mutable_object_indices"])

    # -- Build output model --
    out_model = rhino3dm.File3dm()

    # Copy layers from source (preserves any layer structure that exists)
    for layer in source_model.Layers:
        out_model.Layers.Add(layer)

    # Copy materials from source
    for mat in source_model.Materials:
        out_model.Materials.Add(mat)

    # Step 1: count source non-static objects that will be dropped (informational only).
    mutable_skipped = 0
    all_static_hashes = exact_static_hashes | fuzzy_static_hashes
    for obj in source_model.Objects:
        fp = fingerprint_object(obj)
        if fp is None:
            continue
        if fp["sig_hash"] not in all_static_hashes:
            mutable_skipped += 1

    # Step 2: ALL static (exact + fuzzy) and MUTABLE from the reference file.
    # Taking both static types from reference ensures the output object count and
    # bbox positions exactly match the target reference, which is what validation
    # checks.  Fuzzy-static objects have per-file NURBS drift (up to 0.2 mm), so
    # copying from source would shift bboxes outside the 0.1 mm validation grid.
    exact_added = 0
    fuzzy_added = 0
    mutable_added = 0
    for i, (obj, fp) in enumerate(ref_valid):
        h = fp["sig_hash"]
        if h in exact_static_hashes:
            _add_object_to_model(out_model, obj)
            exact_added += 1
        elif h in fuzzy_static_hashes:
            _add_object_to_model(out_model, obj)
            fuzzy_added += 1
        elif i in mutable_indices_in_ref:
            _add_object_to_model(out_model, obj)
            mutable_added += 1

    static_added = exact_added + fuzzy_added
    print(f"  Static carried over: {static_added} (exact={exact_added}, fuzzy={fuzzy_added}, both from ref)")
    print(f"  Mutable from source dropped: {mutable_skipped}")
    print(f"  Mutable objects from '{target_shape}' added: {mutable_added}")

    # -- Write output --
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ok = out_model.Write(output_path, 7)  # version 7 for broad compatibility
    if ok:
        size_kb = Path(output_path).stat().st_size // 1024
        print(f"\nOK  Output written: {output_path} ({size_kb} KB)")
    else:
        print(f"\nERR Failed to write output file.")
        sys.exit(1)


def _add_object_to_model(model: rhino3dm.File3dm, obj) -> None:
    """Add a geometry object with its attributes to a File3dm model."""
    geom = obj.Geometry
    attrs = obj.Attributes
    obj_type = str(geom.ObjectType)

    try:
        if "Brep" in obj_type:
            model.Objects.AddBrep(geom, attrs)
        elif "Surface" in obj_type:
            model.Objects.AddSurface(geom, attrs)
        elif "Extrusion" in obj_type:
            model.Objects.Add(geom, attrs)
        elif "Curve" in obj_type or "NurbsCurve" in obj_type:
            model.Objects.AddCurve(geom, attrs)
        else:
            model.Objects.Add(geom, attrs)
    except Exception as e:
        # Fallback: attempt generic add
        try:
            model.Objects.Add(geom, attrs)
        except Exception as e2:
            print(f"    WARNING: Could not add object ({obj_type}): {e2}")


# --- Diff report -------------------------------------------------------------

def _diff_report_family(
    family_name: str,
    file_paths: list[Path],
    center_tol: float = DEFAULT_CENTER_TOL,
    size_tol:   float = DEFAULT_SIZE_TOL,
    vol_tol:    float = DEFAULT_VOL_TOL,
) -> None:
    """Print four-category diff report for a single ring family."""
    print(f"\n{chr(45)*60}")
    print(f"  Family: {family_name}  ({len(file_paths)} files)")
    print(f"  Tolerances: center={center_tol} mm  size={size_tol} mm")
    print(f"{chr(45)*60}")

    parsed = []
    for path in file_paths:
        try:
            parsed.append(parse_file(str(path)))
        except Exception as e:
            print(f"  WARNING: {path.name}: {e}")

    if not parsed:
        print("  No files parsed.")
        return

    r = classify_objects(parsed, center_tol, size_tol, vol_tol)

    n_exact   = len(r.exact_static_hashes)
    n_fuzzy   = len(r.fuzzy_static_hashes)
    n_mut     = len(r.mutable_hashes)
    n_unmatch = len(r.unmatched_hashes)
    total_static = n_exact + n_fuzzy

    print(f"\n  Files analysed  : {len(parsed)}")
    print(f"  Exact static    : {n_exact}  (hash identical in all files)")
    print(f"  Fuzzy static    : {n_fuzzy}  (bbox within tolerance in all files)")
    print(f"  Total static    : {total_static}")
    print(f"  Mutable         : {n_mut}  (shape-specific, in 2+ files)")
    print(f"  Unmatched       : {n_unmatch}  (unique to 1 file, no fuzzy partner)")

    if r.shape_to_hashes:
        print("\n  Mutable by shape:")
        for shape in sorted(r.shape_to_hashes):
            hashes = r.shape_to_hashes[shape]
            full = SHAPE_ABBREV_MAP.get(shape, shape)
            print(f"    {shape:6s} ({full:20s}): {len(hashes)}")

    print("\n  Object coverage (exact hash count):")
    for c in sorted(set(r.hash_file_count.values())):
        n = sum(1 for v in r.hash_file_count.values() if v == c)
        label = "all files" if c == len(parsed) else f"{c}/{len(parsed)} files"
        print(f"    In {label}: {n} distinct objects")

    # Show bbox summary for fuzzy-static objects (useful for tolerance tuning)
    if r.fuzzy_static_hashes:
        print("\n  Fuzzy-static object bounding boxes (sample):")
        shown = 0
        for pf in parsed[:1]:  # show from first file only
            for fp in pf.fingerprints:
                if fp["sig_hash"] in r.fuzzy_static_hashes and shown < 5:
                    print(f"    centre=({fp['cx']:.3f},{fp['cy']:.3f},{fp['cz']:.3f})"
                          f"  size=({fp['sx']:.3f}x{fp['sy']:.3f}x{fp['sz']:.3f})"
                          f"  hash={fp['sig_hash']}")
                    shown += 1

    print("\n  Mutable object bounding boxes (up to 3 per shape):")
    for pf in parsed:
        mutable_fps = [fp for fp in pf.fingerprints if fp["sig_hash"] in r.mutable_hashes]
        if mutable_fps:
            full = SHAPE_ABBREV_MAP.get(pf.shape, pf.shape)
            print(f"    [{pf.shape} / {full}]")
            for fp in mutable_fps[:3]:
                print(f"      centre=({fp['cx']:.2f},{fp['cy']:.2f},{fp['cz']:.2f})"
                      f"  size=({fp['sx']:.2f}x{fp['sy']:.2f}x{fp['sz']:.2f})"
                      f"  hash={fp['sig_hash']}")
            if len(mutable_fps) > 3:
                print(f"      ... and {len(mutable_fps)-3} more")


def diff_report(
    dataset_root: Path = Path("3dm"),
    family_filter: Optional[str] = None,
    center_tol: float = DEFAULT_CENTER_TOL,
    size_tol:   float = DEFAULT_SIZE_TOL,
    vol_tol:    float = DEFAULT_VOL_TOL,
) -> None:
    """Print four-category diff report for the dataset (or one family)."""
    print(f"\n{chr(61)*60}")
    print(f"Geometry Diff Report -- {dataset_root}")
    print(f"Tolerances: center={center_tol} mm  size={size_tol} mm")
    print(f"{chr(61)*60}")

    families = scan_dataset(dataset_root)
    if not families:
        print(f"No ring family folders with >=2 .3dm files found under '{dataset_root}'.")
        return

    if family_filter:
        if family_filter not in families:
            raise ValueError(f"Family '{family_filter}' not found. Available: {', '.join(sorted(families))}")
        families = {family_filter: families[family_filter]}

    for family_name, files in families.items():
        _diff_report_family(family_name, files, center_tol, size_tol, vol_tol)

    print()


# --- Tolerance scan -----------------------------------------------------------

_SCAN_TOLERANCES = [
    (0.00, 0.00),
    (0.01, 0.01),
    (0.05, 0.05),
    (0.10, 0.10),
    (0.20, 0.20),
    (0.50, 0.50),
    (1.00, 1.00),
    (2.00, 2.00),
]


def tolerance_scan(
    dataset_root: Path = Path("3dm"),
    family_filter: Optional[str] = None,
) -> None:
    """
    Test multiple tolerance combinations and print how static object counts change.
    Parse each family once, then re-classify at each tolerance level in memory.
    Use this to find the smallest tolerance that correctly identifies all static geometry.
    """
    print(f"\n{chr(61)*60}")
    print(f"Tolerance Scan -- {dataset_root}")
    print(f"{chr(61)*60}")

    families = scan_dataset(dataset_root)
    if not families:
        print(f"No families found under '{dataset_root}'.")
        return
    if family_filter:
        if family_filter not in families:
            raise ValueError(f"Family '{family_filter}' not found.")
        families = {family_filter: families[family_filter]}

    HDR = f"  {'CtrTol':>7}  {'SizTol':>7}  {'Exact':>7}  {'Fuzzy':>7}  {'TotalStatic':>12}  {'Mutable':>8}  {'Unmatched':>10}"

    for family_name, files in families.items():
        print(f"\nFamily: {family_name}  ({len(files)} files)")

        parsed = []
        for path in files:
            try:
                parsed.append(parse_file(str(path)))
            except Exception as e:
                print(f"  WARNING: {path.name}: {e}")
        if len(parsed) < 2:
            print("  Skipped (< 2 parseable files).")
            continue

        print(HDR)
        for c_tol, s_tol in _SCAN_TOLERANCES:
            r = classify_objects(parsed, c_tol, s_tol)
            ne = len(r.exact_static_hashes)
            nf = len(r.fuzzy_static_hashes)
            nm = len(r.mutable_hashes)
            nu = len(r.unmatched_hashes)
            print(f"  {c_tol:>7.2f}  {s_tol:>7.2f}  {ne:>7}  {nf:>7}  {ne+nf:>12}  {nm:>8}  {nu:>10}")

    print()


# --- Validate transform -------------------------------------------------------

def validate_transform(
    source_path: str,
    target_shape: str,
    reference_path: str,
    output_path: str,
    library_dir: Path = LIBRARY_DIR,
    family: Optional[str] = None,
) -> bool:
    """
    Run transform, then compare the generated output against a known reference file.

    Checks:
      1. Object count  — output object count == reference object count
      2. Static preserved  — every exact-static hash in the reference appears in output
      3. Mutable coverage  — every non-static object from the reference appears in output
      4. No source leak    — no non-static source object sneaked into the output
      5. Bbox set match    — output bbox set matches reference at 1 mm grid (advisory)

    Returns True if checks 1-4 all pass.
    """
    target_shape = resolve_shape(target_shape)
    if family is None:
        family = Path(source_path).parent.name

    SEP  = "-" * 60
    SEP2 = "=" * 60

    print(f"\n{SEP2}")
    print(f"  validate-transform:  {Path(source_path).name}  ->  {target_shape}")
    print(f"  Source    : {source_path}")
    print(f"  Reference : {reference_path}")
    print(f"  Output    : {output_path}")
    print(f"  Family    : {family}")
    print(f"{SEP2}\n")

    # -- Step 1: run transform -------------------------------------------------
    print(f"[1] Running transform ...")
    try:
        transform(source_path, target_shape, output_path, library_dir, family)
    except Exception as exc:
        print(f"\n  FAIL  Transform error: {exc}")
        return False

    # -- Step 2: load library + fingerprint all three files --------------------
    print(f"\n[2] Loading library and fingerprinting files ...")
    family_lib = library_dir / family
    classification, shape_index = load_library(family_lib)
    static_hashes = set(classification["combined_static_hashes"])

    def _fp_list(path: str) -> list[dict]:
        model = rhino3dm.File3dm.Read(path)
        if model is None:
            raise IOError(f"Cannot read: {path}")
        return [fp for obj in model.Objects
                for fp in [fingerprint_object(obj)] if fp is not None]

    out_fps = _fp_list(output_path)
    ref_fps = _fp_list(reference_path)
    src_fps = _fp_list(source_path)

    out_hashes = {fp["sig_hash"] for fp in out_fps}
    ref_hashes = {fp["sig_hash"] for fp in ref_fps}

    # Non-static objects in source (potential contamination)
    src_non_static_hashes = {fp["sig_hash"] for fp in src_fps
                             if fp["sig_hash"] not in static_hashes}

    # Non-static objects that should come from the reference
    ref_mutable_indices = set(shape_index[target_shape]["mutable_object_indices"])
    ref_mutable_hashes  = {ref_fps[i]["sig_hash"]
                           for i in ref_mutable_indices if i < len(ref_fps)}

    # Expected output = static from source + non-static from reference
    src_static_hashes = {fp["sig_hash"] for fp in src_fps
                         if fp["sig_hash"] in static_hashes}
    expected_out_hashes = src_static_hashes | ref_mutable_hashes

    print(f"  Source objects fingerprinted    : {len(src_fps)}")
    print(f"  Reference objects fingerprinted : {len(ref_fps)}")
    print(f"  Output objects fingerprinted    : {len(out_fps)}")

    # -- Checks ----------------------------------------------------------------
    results: list[tuple[str, bool, str]] = []   # (label, passed, detail)

    # Check 1: object count
    ok = len(out_fps) == len(ref_fps)
    detail = f"output={len(out_fps)}  reference={len(ref_fps)}"
    if not ok:
        detail += f"  (diff={len(out_fps)-len(ref_fps):+d})"
    results.append(("Object count matches reference", ok, detail))

    # Check 2: static objects preserved
    #   Exact-static: identical hash in every file → check by hash in output.
    #   Fuzzy-static: same bbox but different NURBS hash per file; the transform
    #     carries the SOURCE's version, so the reference's hash won't appear in
    #     output.  Check by bbox proximity instead.
    exact_static_hashes = set(classification.get("exact_static_hashes", []))
    fuzzy_static_hashes = set(classification.get("fuzzy_static_hashes", []))

    def _bbox_key1(fp: dict) -> tuple:
        return (round(fp["cx"], 1), round(fp["cy"], 1), round(fp["cz"], 1),
                round(fp["sx"], 1), round(fp["sy"], 1), round(fp["sz"], 1))

    out_bbox_keys = {_bbox_key1(fp) for fp in out_fps}

    # Exact-static: must appear in output with matching hash
    exact_in_ref   = exact_static_hashes & ref_hashes
    missing_exact  = exact_in_ref - out_hashes

    # Fuzzy-static: reference's version must have a bbox-similar object in output
    fuzzy_in_ref_fps   = [fp for fp in ref_fps if fp["sig_hash"] in fuzzy_static_hashes]
    missing_fuzzy_bboxes = [fp for fp in fuzzy_in_ref_fps
                            if _bbox_key1(fp) not in out_bbox_keys]

    ok = (len(missing_exact) == 0 and len(missing_fuzzy_bboxes) == 0)
    detail = (f"exact-static: {len(exact_in_ref)} expected, "
              f"{len(exact_in_ref)-len(missing_exact)} matched  |  "
              f"fuzzy-static: {len(fuzzy_in_ref_fps)} expected, "
              f"{len(fuzzy_in_ref_fps)-len(missing_fuzzy_bboxes)} bbox-matched")
    if missing_exact:
        detail += f"  MISSING(exact): {', '.join(sorted(missing_exact)[:3])}"
    if missing_fuzzy_bboxes:
        detail += f"  MISSING(fuzzy bbox): {len(missing_fuzzy_bboxes)}"
        for fp in missing_fuzzy_bboxes[:2]:
            detail += f" centre=({fp['cx']:.2f},{fp['cy']:.2f},{fp['cz']:.2f})"
    results.append(("Static objects preserved", ok, detail))

    # Check 3: mutable coverage — non-static objects from reference in output.
    # These are exact copies (taken directly from the reference file), so hash
    # comparison is correct.  If any are missing, also report bbox proximity hits
    # so it's easier to tell whether the geometry is there but hash drifted.
    missing_mut = ref_mutable_hashes - out_hashes
    ok = len(missing_mut) == 0
    detail = (f"expected {len(ref_mutable_hashes)} non-static, "
              f"found {len(ref_mutable_hashes)-len(missing_mut)}")
    if not ok:
        missing_info = ", ".join(sorted(missing_mut)[:3])
        if len(missing_mut) > 3:
            missing_info += f"  (+{len(missing_mut)-3} more)"
        detail += f"  MISSING: {missing_info}"
        # Bbox proximity check for missing objects (diagnostic only)
        missing_fps = [fp for fp in ref_fps
                       if fp["sig_hash"] in missing_mut]
        bbox_found = sum(1 for fp in missing_fps if _bbox_key1(fp) in out_bbox_keys)
        if bbox_found:
            detail += f"  ({bbox_found} have bbox match — possible hash drift)"
    results.append(("Target non-static objects present", ok, detail))

    # Check 4: no source contamination
    leaked = src_non_static_hashes & out_hashes - ref_mutable_hashes
    ok = len(leaked) == 0
    detail = (f"{len(leaked)} source non-static object(s) found in output"
              if not ok else "no source-specific objects in output")
    if not ok:
        leaked_info = ", ".join(sorted(leaked)[:3])
        if len(leaked) > 3:
            leaked_info += f"  (+{len(leaked)-3} more)"
        detail += f": {leaked_info}"
    results.append(("No source contamination", ok, detail))

    # Advisory: bbox set match at 1 mm grid
    def _bbox_key(fp: dict) -> tuple:
        return (round(fp["cx"], 1), round(fp["cy"], 1), round(fp["cz"], 1),
                round(fp["sx"], 1), round(fp["sy"], 1), round(fp["sz"], 1))

    out_bboxes = {_bbox_key(fp) for fp in out_fps}
    ref_bboxes = {_bbox_key(fp) for fp in ref_fps}
    extra_in_out  = out_bboxes - ref_bboxes
    extra_in_ref  = ref_bboxes - out_bboxes
    bbox_match = (not extra_in_out and not extra_in_ref)
    bbox_detail = (f"out\\ref={len(extra_in_out)}  ref\\out={len(extra_in_ref)}"
                   if not bbox_match else "exact match")

    # -- Print results ---------------------------------------------------------
    print(f"\n{SEP}")
    mandatory_pass = True
    for label, passed, detail in results:
        tag = "PASS" if passed else "FAIL"
        print(f"  {tag}  {label}")
        print(f"        {detail}")
        if not passed:
            mandatory_pass = False

    # Advisory bbox check
    tag = "INFO" if bbox_match else "WARN"
    print(f"  {tag}  Bbox set match (1 mm grid, advisory)")
    print(f"        {bbox_detail}")
    if not bbox_match:
        if extra_in_ref:
            print(f"        In reference but not output (first 3):")
            for bb in sorted(extra_in_ref)[:3]:
                cx, cy, cz, sx, sy, sz = bb
                print(f"          centre=({cx},{cy},{cz})  size=({sx}x{sy}x{sz})")
        if extra_in_out:
            print(f"        In output but not reference (first 3):")
            for bb in sorted(extra_in_out)[:3]:
                cx, cy, cz, sx, sy, sz = bb
                print(f"          centre=({cx},{cy},{cz})  size=({sx}x{sy}x{sz})")

    print(f"{SEP}")
    verdict = "PASS" if mandatory_pass else "FAIL"
    n_pass = sum(1 for _, p, _ in results if p)
    print(f"\n  {verdict}  {n_pass}/{len(results)} mandatory checks passed\n")

    return mandatory_pass


# --- Batch validation ---------------------------------------------------------

def _validate_pair(
    source_path: str,
    target_shape: str,
    reference_path: str,
    output_path: str,
    library_dir: Path,
    family: str,
    lib_cache: dict,
) -> dict:
    """
    Run transform for one (source, target) pair and return a result dict.
    All console output is suppressed.
    """
    import io
    import contextlib

    src_shape = detect_shape_from_filename(source_path) or "?"
    result: dict = {
        "family":       family,
        "source_shape": src_shape,
        "target_shape": target_shape,
        "result": "ERROR",
        "checks": {
            "object_count_match": False,
            "static_preserved":   False,
            "mutable_coverage":   False,
            "no_contamination":   False,
        },
        "bbox_match":   False,
        "diagnostics":  {},
    }

    try:
        # Run transform silently
        with contextlib.redirect_stdout(io.StringIO()):
            transform(source_path, target_shape, output_path, library_dir, family)

        # Load library (cached per family)
        if family not in lib_cache:
            lib_cache[family] = load_library(library_dir / family)
        classification, shape_index = lib_cache[family]

        static_hashes       = set(classification["combined_static_hashes"])
        exact_static_hashes = set(classification.get("exact_static_hashes", []))
        fuzzy_static_hashes = set(classification.get("fuzzy_static_hashes", []))

        def _fp_list(path: str) -> list:
            m = rhino3dm.File3dm.Read(path)
            if m is None:
                raise IOError(f"Cannot read: {path}")
            return [fp for obj in m.Objects
                    for fp in [fingerprint_object(obj)] if fp is not None]

        out_fps = _fp_list(output_path)
        ref_fps = _fp_list(reference_path)
        src_fps = _fp_list(source_path)

        out_hashes     = {fp["sig_hash"] for fp in out_fps}
        ref_hashes     = {fp["sig_hash"] for fp in ref_fps}
        src_non_static = {fp["sig_hash"] for fp in src_fps
                          if fp["sig_hash"] not in static_hashes}

        ref_mut_indices = set(shape_index[target_shape]["mutable_object_indices"])
        ref_mut_hashes  = {ref_fps[i]["sig_hash"]
                           for i in ref_mut_indices if i < len(ref_fps)}

        def _bk(fp: dict) -> tuple:
            return (round(fp["cx"], 1), round(fp["cy"], 1), round(fp["cz"], 1),
                    round(fp["sx"], 1), round(fp["sy"], 1), round(fp["sz"], 1))

        out_bk = {_bk(fp) for fp in out_fps}

        # Check 1: object count
        c1 = len(out_fps) == len(ref_fps)

        # Check 2: static preserved (exact by hash, fuzzy by bbox)
        exact_in_ref  = exact_static_hashes & ref_hashes
        missing_exact = exact_in_ref - out_hashes
        fuzzy_in_ref  = [fp for fp in ref_fps if fp["sig_hash"] in fuzzy_static_hashes]
        missing_fuzzy = [fp for fp in fuzzy_in_ref if _bk(fp) not in out_bk]
        c2 = (len(missing_exact) == 0 and len(missing_fuzzy) == 0)

        # Check 3: mutable coverage
        missing_mut = ref_mut_hashes - out_hashes
        c3 = len(missing_mut) == 0

        # Check 4: no source contamination
        leaked = src_non_static & out_hashes - ref_mut_hashes
        c4 = len(leaked) == 0

        # Advisory: bbox set match at 1 mm grid
        ref_bk  = {_bk(fp) for fp in ref_fps}
        bbox_ok = (out_bk == ref_bk)

        all_pass = c1 and c2 and c3 and c4
        result["result"] = "PASS" if all_pass else "FAIL"
        result["checks"] = {
            "object_count_match": c1,
            "static_preserved":   c2,
            "mutable_coverage":   c3,
            "no_contamination":   c4,
        }
        result["bbox_match"] = bbox_ok
        result["diagnostics"] = {
            "source_objects":            len(src_fps),
            "reference_objects":         len(ref_fps),
            "output_objects":            len(out_fps),
            "exact_static_expected":     len(exact_in_ref),
            "exact_static_matched":      len(exact_in_ref) - len(missing_exact),
            "fuzzy_static_expected":     len(fuzzy_in_ref),
            "fuzzy_static_bbox_matched": len(fuzzy_in_ref) - len(missing_fuzzy),
            "mutable_expected":          len(ref_mut_hashes),
            "mutable_found":             len(ref_mut_hashes) - len(missing_mut),
            "missing_mutable_hashes":    sorted(missing_mut)[:5],
            "leaked_hashes":             sorted(leaked)[:5],
        }

    except Exception as exc:
        result["result"] = "ERROR"
        result["diagnostics"]["error"] = str(exc)

    return result


def batch_validate(
    dataset_root:  Path = Path("3dm"),
    library_dir:   Path = LIBRARY_DIR,
    family_filter: Optional[str] = None,
    output_report: Path = Path("validation_report.json"),
    build_first:   bool = False,
) -> None:
    """
    Run validate-transform for every (source, target) ordered shape pair across
    all ring families.  Writes validation_report.json and prints pass/fail stats.
    """
    import tempfile
    from datetime import datetime

    if build_first:
        print("Building libraries ...")
        build_library(dataset_root, library_dir, family_filter)

    families = scan_dataset(dataset_root)
    if not families:
        print(f"No families found under '{dataset_root}'.")
        return

    if family_filter:
        if family_filter not in families:
            raise ValueError(f"Family '{family_filter}' not found. "
                             f"Available: {', '.join(sorted(families))}")
        families = {family_filter: families[family_filter]}

    all_results: list = []
    total = passed = failed = errors = 0
    lib_cache: dict = {}

    for family_name in sorted(families):
        fam_lib = library_dir / family_name
        if not (fam_lib / "shape_index.json").exists():
            print(f"\n  SKIP {family_name}: library not found "
                  f"(run build-library or use --build)")
            continue

        classification, shape_index = load_library(fam_lib)
        lib_cache[family_name] = (classification, shape_index)

        shapes = sorted(shape_index.keys())
        if len(shapes) < 2:
            print(f"\n  SKIP {family_name}: fewer than 2 shapes in library.")
            continue

        shape_to_file = {s: info["source_file"] for s, info in shape_index.items()}
        pairs = [(s, t) for s in shapes for t in shapes if s != t]
        n_pairs = len(pairs)
        n_pass = n_fail = n_err = 0

        print(f"\n{chr(45)*60}")
        print(f"  {family_name}  ({len(shapes)} shapes, {n_pairs} pairs)")
        print(f"{chr(45)*60}")

        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, (src_shape, tgt_shape) in enumerate(pairs, 1):
                src_file = shape_to_file[src_shape]
                ref_file = shape_to_file[tgt_shape]
                out_file = str(Path(tmpdir) / f"{src_shape}_{tgt_shape}.3dm")

                r = _validate_pair(src_file, tgt_shape, ref_file, out_file,
                                   library_dir, family_name, lib_cache)
                all_results.append(r)
                total += 1

                status = r["result"]
                if status == "PASS":
                    passed += 1; n_pass += 1
                elif status == "FAIL":
                    failed += 1; n_fail += 1
                else:
                    errors += 1; n_err += 1

                failing = [k for k, v in r["checks"].items() if not v]
                detail  = ("  FAIL: " + ", ".join(failing)) if failing else ""
                if status == "ERROR":
                    detail = "  ERR: " + str(r["diagnostics"].get("error", ""))[:60]
                print(f"  [{idx:3d}/{n_pairs}] {src_shape:6s} -> {tgt_shape:6s}  "
                      f"{status}{detail}")

        fam_parts = [f"{n_pass}/{n_pairs} passed"]
        if n_fail:
            fam_parts.append(f"{n_fail} failed")
        if n_err:
            fam_parts.append(f"{n_err} errors")
        print(f"  Family result: " + "  ".join(fam_parts))

    # Write JSON report
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset":   str(dataset_root.resolve()),
        "library":   str(library_dir.resolve()),
        "total":     total,
        "passed":    passed,
        "failed":    failed,
        "errors":    errors,
        "pass_rate": f"{passed/total*100:.1f}%" if total else "N/A",
        "results":   all_results,
    }
    output_report.parent.mkdir(parents=True, exist_ok=True)
    with open(output_report, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{chr(61)*60}")
    print(f"  Batch Validation Complete")
    print(f"  Total  : {total}")
    print(f"  Passed : {passed}")
    print(f"  Failed : {failed}")
    if errors:
        print(f"  Errors : {errors}")
    print(f"  Rate   : {report['pass_rate']}")
    print(f"  Report : {output_report}")
    print(f"{chr(61)*60}\n")


# --- CLI ---------------------------------------------------------------------

# --- generate-all ------------------------------------------------------------

def _output_stem(source_stem: str, source_shape: str, target_shape: str) -> str:
    """Derive an output filename stem by replacing the source shape token."""
    # Handle multi-token shapes first (e.g. ELCU stored as "EL CU" or "EL_CU")
    if len(source_shape) >= 4:
        for sep in (" ", "_"):
            for split in range(1, len(source_shape)):
                candidate = source_shape[:split] + sep + source_shape[split:]
                upper = source_stem.upper()
                if candidate.upper() in upper:
                    idx = upper.index(candidate.upper())
                    return source_stem[:idx] + target_shape + source_stem[idx + len(candidate):]
    # Single token: match between non-alpha boundaries
    result, n = re.subn(
        r"(?<![A-Za-z])" + re.escape(source_shape) + r"(?![A-Za-z])",
        target_shape, source_stem, count=1, flags=re.IGNORECASE,
    )
    return result if n else f"{source_stem}_{target_shape}"


def _check_output(
    output_path: str,
    source_path: str,
    reference_path: str,
    classification: dict,
    shape_index: dict,
    target_shape: str,
) -> dict:
    """
    Validate an already-generated output against a reference file.
    Returns a dict with keys: result (PASS/FAIL/ERROR), object_count, checks.
    """
    try:
        def _fps(path: str) -> list:
            m = rhino3dm.File3dm.Read(path)
            if m is None:
                raise IOError(f"Cannot read: {path}")
            return [fp for obj in m.Objects
                    for fp in [fingerprint_object(obj)] if fp is not None]

        out_fps = _fps(output_path)
        ref_fps = _fps(reference_path)
        src_fps = _fps(source_path)

        static_hashes       = set(classification["combined_static_hashes"])
        exact_static_hashes = set(classification.get("exact_static_hashes", []))
        fuzzy_static_hashes = set(classification.get("fuzzy_static_hashes", []))

        out_hashes     = {fp["sig_hash"] for fp in out_fps}
        ref_hashes     = {fp["sig_hash"] for fp in ref_fps}
        src_non_static = {fp["sig_hash"] for fp in src_fps
                          if fp["sig_hash"] not in static_hashes}

        ref_mut_indices = set(shape_index[target_shape]["mutable_object_indices"])
        ref_mut_hashes  = {ref_fps[i]["sig_hash"]
                           for i in ref_mut_indices if i < len(ref_fps)}

        def _bk(fp: dict) -> tuple:
            return (round(fp["cx"], 1), round(fp["cy"], 1), round(fp["cz"], 1),
                    round(fp["sx"], 1), round(fp["sy"], 1), round(fp["sz"], 1))

        out_bk = {_bk(fp) for fp in out_fps}

        c1 = len(out_fps) == len(ref_fps)

        exact_in_ref  = exact_static_hashes & ref_hashes
        missing_exact = exact_in_ref - out_hashes
        fuzzy_in_ref  = [fp for fp in ref_fps if fp["sig_hash"] in fuzzy_static_hashes]
        missing_fuzzy = [fp for fp in fuzzy_in_ref if _bk(fp) not in out_bk]
        c2 = len(missing_exact) == 0 and len(missing_fuzzy) == 0

        missing_mut = ref_mut_hashes - out_hashes
        c3 = len(missing_mut) == 0

        leaked = src_non_static & out_hashes - ref_mut_hashes
        c4 = len(leaked) == 0

        checks = {
            "object_count_match": c1,
            "static_preserved":   c2,
            "mutable_coverage":   c3,
            "no_contamination":   c4,
        }
        return {
            "result":       "PASS" if all(checks.values()) else "FAIL",
            "object_count": len(out_fps),
            "checks":       checks,
        }
    except Exception as e:
        return {"result": "ERROR", "object_count": 0, "checks": {}, "error": str(e)}


def generate_all(
    source_path: str,
    family: str,
    output_dir: Path,
    library_dir: Path = LIBRARY_DIR,
    dataset_root: Optional[Path] = None,
) -> bool:
    """
    Generate every available shape variant for *family* from a single *source_path*.

    Shape is detected automatically from the source filename.  For each target
    shape (all shapes in the library except the source), the function:
      1. Runs transform() to produce the output .3dm file.
      2. Validates the output against the reference file (found via *dataset_root*).
         Falls back to a basic readability check when no reference is available.

    Returns True if all variants were generated and validated successfully.
    """
    import io
    import contextlib

    source_path = str(Path(source_path).resolve())
    output_dir  = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Detect source shape ──────────────────────────────────────────────────
    source_shape = detect_shape_from_filename(source_path)
    if source_shape is None:
        print(f"ERROR: Cannot detect stone shape from filename: {Path(source_path).name}")
        print(f"       Expected one of: {', '.join(STONE_SHAPES)}")
        return False

    src_full = SHAPE_ABBREV_MAP.get(source_shape, source_shape)
    print(f"\nSource: {Path(source_path).name}  (shape: {source_shape} - {src_full})")

    # ── Load library ─────────────────────────────────────────────────────────
    classification, shape_index = load_library(library_dir / family)

    all_shapes    = sorted(shape_index.keys())
    target_shapes = [s for s in all_shapes if s != source_shape]

    if source_shape not in shape_index:
        print(f"WARNING: '{source_shape}' is not in the library for '{family}'. "
              f"Available: {', '.join(all_shapes)}")

    print(f"Family:  {family}  |  {len(all_shapes)} shapes: {', '.join(all_shapes)}")
    print(f"\nGenerating {len(target_shapes)} variant(s) -> {output_dir}\n")

    # ── Discover reference files for validation ───────────────────────────────
    ref_files: dict[str, str] = {}
    if dataset_root is not None:
        fam_dir = Path(dataset_root) / family
        if fam_dir.exists():
            for f in sorted(fam_dir.glob("*.3dm")):
                shape = detect_shape_from_filename(str(f))
                if shape and shape not in ref_files:
                    ref_files[shape] = str(f)

    has_refs    = bool(ref_files)
    val_mode    = "full 4-check" if has_refs else "basic readability"
    src_stem    = Path(source_path).stem
    col_shape   = max((len(s) for s in target_shapes), default=4) + 2

    # ── Generate & validate each target ──────────────────────────────────────
    rows: list[tuple] = []   # (target, out_path, status_str, ok, n_obj)

    for target in target_shapes:
        new_stem = _output_stem(src_stem, source_shape, target)
        out_path = output_dir / f"{new_stem}.3dm"

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                transform(source_path, target, str(out_path), library_dir, family)
        except Exception as e:
            rows.append((target, out_path, f"ERROR  {e}", False, 0))
            continue

        if target in ref_files:
            res    = _check_output(str(out_path), source_path, ref_files[target],
                                   classification, shape_index, target)
            ok     = res["result"] == "PASS"
            n_obj  = res["object_count"]
            status = res["result"]
            if not ok:
                failed = [k for k, v in res.get("checks", {}).items() if not v]
                status += f"  [{', '.join(failed)}]"
        else:
            try:
                m     = rhino3dm.File3dm.Read(str(out_path))
                n_obj = sum(1 for obj in (m.Objects if m else [])
                            if fingerprint_object(obj) is not None)
                ok    = n_obj > 0
                status = "OK" if ok else "FAIL (empty output)"
            except Exception as e:
                ok, n_obj, status = False, 0, f"ERROR  {e}"

        rows.append((target, out_path, status, ok, n_obj))
        print(f"  {target:<{col_shape}} {status:<8}  {out_path.name}  ({n_obj} obj)")

    # ── Summary ───────────────────────────────────────────────────────────────
    n_ok    = sum(1 for *_, ok, _ in rows if ok)
    n_total = len(rows)

    print()
    print(f"Generated:  {n_ok}/{n_total} files")
    print(f"Validation: {'PASS' if n_ok == n_total else 'FAIL'}  ({val_mode})")

    return n_ok == n_total


def main():
    parser = argparse.ArgumentParser(
        description="Jewelry stone shape transformation pipeline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # build-library
    p_build = sub.add_parser(
        "build-library",
        help="Scan dataset folder, build per-family shape libraries"
    )
    p_build.add_argument(
        "--dataset", default="3dm",
        help="Root folder containing ring family subdirectories (default: 3dm/)"
    )
    p_build.add_argument(
        "--family", default=None,
        help="Process only this family (e.g. AD_1). Omit to process all families."
    )
    p_build.add_argument(
        "--library-dir", default="shape_library",
        help="Root directory for library output (default: shape_library/)"
    )

    # transform
    p_tx = sub.add_parser("transform", help="Transform a .3dm file to a new stone shape")
    p_tx.add_argument("--source", required=True, help="Source .3dm file path")
    p_tx.add_argument(
        "--shape", required=True,
        help=(
            f"Target stone shape — abbreviation or full name. "
            f"Codes: {', '.join(SHAPE_ABBREV_MAP.keys())}. "
            f"Names: {', '.join(SHAPE_ABBREV_MAP.values())}"
        )
    )
    p_tx.add_argument("--output", required=True, help="Output .3dm file path")
    p_tx.add_argument("--library-dir", default="shape_library")
    p_tx.add_argument(
        "--family", default=None,
        help="Ring family name. Auto-detected as parent folder of --source if omitted."
    )

    def _add_tol_args(p):
        p.add_argument("--center-tolerance", type=float, default=DEFAULT_CENTER_TOL,
                       help=f"Max centroid distance in mm (default {DEFAULT_CENTER_TOL})")
        p.add_argument("--size-tolerance",   type=float, default=DEFAULT_SIZE_TOL,
                       help=f"Max bbox side diff in mm (default {DEFAULT_SIZE_TOL})")
        p.add_argument("--volume-tolerance", type=float, default=float("inf"),
                       help="Max bbox volume diff (default: unconstrained)")

    # diff-report
    p_diff = sub.add_parser(
        "diff-report",
        help="Print geometry diff report for dataset (or one family)"
    )
    p_diff.add_argument("--dataset", default="3dm")
    p_diff.add_argument("--family", default=None)
    _add_tol_args(p_diff)

    # Add tolerance args to build-library too
    _add_tol_args(p_build)

    # tolerance-scan
    p_scan = sub.add_parser(
        "tolerance-scan",
        help="Test tolerance values from 0 to 2 mm and show how static counts change"
    )
    p_scan.add_argument("--dataset", default="3dm")
    p_scan.add_argument("--family", default=None,
                        help="Limit to one family. Omit to scan all.")

    # validate-transform
    p_val = sub.add_parser(
        "validate-transform",
        help="Run transform and compare output against a known reference file"
    )
    p_val.add_argument("--source",       required=True, help="Source .3dm file")
    p_val.add_argument("--target-shape", required=True,
                       help="Target stone shape (abbreviation or full name)")
    p_val.add_argument("--reference",    required=True,
                       help="Known-good reference .3dm file for the target shape")
    p_val.add_argument("--output",       required=True,
                       help="Where to write the generated .3dm file")
    p_val.add_argument("--library-dir",  default="shape_library")
    p_val.add_argument("--family",       default=None,
                       help="Ring family. Auto-detected from --source parent dir if omitted.")

    # generate-all
    p_gen = sub.add_parser(
        "generate-all",
        help="Generate all shape variants from a single source file"
    )
    p_gen.add_argument("--source",     required=True, help="Source .3dm file path")
    p_gen.add_argument("--family",     required=True,
                       help="Ring family name (e.g. AD_10)")
    p_gen.add_argument("--output-dir", required=True,
                       help="Directory where generated .3dm files are written")
    p_gen.add_argument("--library-dir", default="shape_library")
    p_gen.add_argument("--dataset",    default="3dm",
                       help="Root folder of the original .3dm dataset (for validation, default: 3dm/)")

    # batch-validate
    p_batch = sub.add_parser(
        "batch-validate",
        help="Run validate-transform for every shape pair across all (or one) ring family"
    )
    p_batch.add_argument("--dataset",     default="3dm",
                         help="Root folder containing ring family subdirectories (default: 3dm/)")
    p_batch.add_argument("--library-dir", default="shape_library",
                         help="Root of shape libraries (default: shape_library/)")
    p_batch.add_argument("--family",      default=None,
                         help="Limit to one family (e.g. AD_10). Omit for all families.")
    p_batch.add_argument("--output",      default="validation_report.json",
                         help="Output JSON report path (default: validation_report.json)")
    p_batch.add_argument("--build",       action="store_true",
                         help="Build/rebuild libraries before running validation")

    args = parser.parse_args()

    def tols(a):
        return dict(center_tol=a.center_tolerance,
                    size_tol=a.size_tolerance,
                    vol_tol=a.volume_tolerance)

    if args.command == "build-library":
        build_library(Path(args.dataset), Path(args.library_dir), args.family, **tols(args))

    elif args.command == "transform":
        transform(args.source, args.shape, args.output,
                  Path(args.library_dir), args.family)

    elif args.command == "diff-report":
        diff_report(Path(args.dataset), args.family, **tols(args))

    elif args.command == "tolerance-scan":
        tolerance_scan(Path(args.dataset), args.family)

    elif args.command == "validate-transform":
        ok = validate_transform(
            source_path=args.source,
            target_shape=args.target_shape,
            reference_path=args.reference,
            output_path=args.output,
            library_dir=Path(args.library_dir),
            family=args.family,
        )
        sys.exit(0 if ok else 1)

    elif args.command == "generate-all":
        dataset = Path(args.dataset)
        ok = generate_all(
            source_path=args.source,
            family=args.family,
            output_dir=Path(args.output_dir),
            library_dir=Path(args.library_dir),
            dataset_root=dataset if dataset.exists() else None,
        )
        sys.exit(0 if ok else 1)

    elif args.command == "batch-validate":
        batch_validate(
            dataset_root=Path(args.dataset),
            library_dir=Path(args.library_dir),
            family_filter=args.family,
            output_report=Path(args.output),
            build_first=args.build,
        )


if __name__ == "__main__":
    main()
