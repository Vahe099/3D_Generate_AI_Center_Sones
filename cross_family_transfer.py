"""
cross_family_transfer.py  --  diagnostic / planning tool

Usage:
    python cross_family_transfer.py analyze <family>
    python cross_family_transfer.py analyze <family> --out transfer_plan.json
    python cross_family_transfer.py analyze <family> --bridge CU
    python cross_family_transfer.py analyze <family> --rebuild-cache

Reads shape libraries and source .3dm files.
Produces transfer_plan.json describing donor candidates for every missing shape.
Does NOT generate any .3dm output files.
"""

import sys, json, os, subprocess, math, time
from collections import Counter
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ─────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────

LIBRARY_DIR        = Path("shape_library")
CACHE_FILE         = Path("_cf_frame_cache.json")
WORKER_FILE          = Path("_cf_frame_worker.py")
SYNTH_WORKER_FILE    = Path("_cf_synth_worker.py")
COMPARE_WORKER_FILE  = Path("_cf_compare_worker.py")
VALIDATE_WORKER_FILE = Path(f"_cf_validate_worker_{os.getpid()}.py")
BLACKLIST_FILE       = Path("_donor_blacklist.json")   # strike-based donor blacklist
STRIKE_WARN          = 1   # 1 FAIL → warned, still eligible on next run
STRIKE_TEMP          = 2   # 2 FAILs → temporary blacklist (skip until cleared)
STRIKE_PERM          = 3   # 3 FAILs → permanent blacklist (never retry)
ELEVATED_BASKET_THRESHOLD = 7.0   # mm; target families below this are elevated-setting
ELEVATED_MIN_ATTEMPTS     = 10    # minimum fallback attempts for elevated-setting targets
ELEVATED_COUNT_RATIO      = 3.0   # max donor/target count ratio for elevated pre-rejection
MAX_WORKERS      = 8      # parallel subprocess slots
TOP_N            = 40     # candidates to report per missing shape
PRESCORE_KEEP    = 40     # donors to load target frames for (>= TOP_N)

ALL_SHAPES = ["AS", "CU", "ELCU", "EM", "MQ", "OV", "PE", "PR", "RA", "RD"]

# Expected stone aspect ratio ranges per shape (long/short)
STONE_AR = {
    "RD":   (0.92, 1.08),
    "OV":   (1.25, 1.75),
    "EM":   (1.25, 1.75),
    "MQ":   (1.75, 2.60),
    "AS":   (0.92, 1.10),
    "PR":   (0.92, 1.10),
    "PE":   (1.35, 2.10),
    "RA":   (1.25, 1.80),
    "CU":   (0.90, 1.18),
    "ELCU": (1.18, 1.65),
}

# ─────────────────────────────────────────────────────────────
#  Embedded worker: extract mutable frame from a .3dm file
# ─────────────────────────────────────────────────────────────

FRAME_WORKER_SRC = """\
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import rhino3dm
from jewelry_transform import fingerprint_object, is_contaminant

path            = sys.argv[1]
mutable_indices = set(json.loads(sys.argv[2]))

model = rhino3dm.File3dm.Read(path)
if not model:
    print(json.dumps({"error": "cannot_read"}))
    sys.exit(0)

fps, idx = [], 0
for obj in model.Objects:
    if is_contaminant(obj, model):
        continue
    fp = fingerprint_object(obj)
    if fp is None:
        continue
    if idx in mutable_indices:
        fps.append(fp)
    idx += 1

if not fps:
    print(json.dumps({"error": "no_mutables", "count": 0}))
    sys.exit(0)

cxs  = [f["cx"] for f in fps]
cys  = [f["cy"] for f in fps]
czs  = [f["cz"] for f in fps]
sxs  = [f["sx"] for f in fps]
sys_ = [f["sy"] for f in fps]
szs  = [f["sz"] for f in fps]
nsrf = [f.get("n_surfaces") or 0 for f in fps]

n   = len(fps)
zmx = max(czs)

# Stone detection: H7 combined rank
# Prefer objects above z=0; use weighted rank across XY area, volume, cz, n_surfaces.
# This avoids selecting basket/seat components that have large XY footprints below z=0.
import math as _math

def _rank(vals, ascending=True):
    order = sorted(range(len(vals)), key=lambda i: vals[i], reverse=not ascending)
    r = {}
    for rank, idx in enumerate(order):
        r[idx] = rank
    return r

areas   = [sxs[i] * sys_[i] for i in range(n)]
volumes = [sxs[i] * sys_[i] * szs[i] for i in range(n)]
dists   = [_math.sqrt(cxs[i]**2 + cys[i]**2) for i in range(n)]
dr      = [szs[i] / max(max(sxs[i], sys_[i]), 0.001) for i in range(n)]

r_xy  = _rank(areas,   ascending=False)
r_vol = _rank(volumes, ascending=False)
r_nsf = _rank(nsrf,    ascending=False)
r_cz  = _rank(czs,     ascending=False)

scores = []
for i in range(n):
    cent_bonus = -n * 0.10 * _math.exp(-(dists[i]**2) / (2 * 1.5**2))
    flat_pen   = n * 0.10 if dr[i] < 0.15 else 0.0
    s = (0.25 * r_xy[i] + 0.25 * r_vol[i] + 0.15 * r_nsf[i] +
         0.25 * r_cz[i] + cent_bonus + flat_pen)
    scores.append(s)

st_idx   = scores.index(min(scores))
stone_sx = sxs[st_idx]
stone_sy = sys_[st_idx]
stone_ar = max(stone_sx, stone_sy) / max(min(stone_sx, stone_sy), 0.001)

# Prong estimate: high-Z objects with small XY footprint vs stone
prong_est = sum(
    1 for i in range(n)
    if czs[i] > zmx * 0.85 and max(sxs[i], sys_[i]) < stone_sx * 0.6
)

# Melee estimate: objects near stone girdle level with small footprint (halo/pave stones)
melee_est = sum(
    1 for i in range(n)
    if i != st_idx
    and abs(czs[i] - czs[st_idx]) < 2.0
    and max(sxs[i], sys_[i]) < stone_sx * 0.60
    and czs[i] > 3.0
)

# Strata count: distinct 1mm Z-bins occupied by mutable objects
strata_count = len(set(int(cz // 1.0) for cz in czs))

# Basket depth: how far below stone centroid the assembly extends
basket_depth = round(czs[st_idx] - min(czs), 4)

print(json.dumps({
    "count":           n,
    "cx_mean":         round(sum(cxs)/n, 4),
    "cy_mean":         round(sum(cys)/n, 4),
    "cz_mean":         round(sum(czs)/n, 4),
    "sx_mean":         round(sum(sxs)/n, 4),
    "sy_mean":         round(sum(sys_)/n, 4),
    "sz_mean":         round(sum(szs)/n, 4),
    "footprint_xy":    round(max(max(sxs), max(sys_)), 4),
    "z_top":           round(zmx, 4),
    "z_bottom":        round(min(czs), 4),
    "z_range":         round(zmx - min(czs), 4),
    "stone_sx":        round(stone_sx, 4),
    "stone_sy":        round(stone_sy, 4),
    "stone_cz":        round(czs[st_idx], 4),
    "stone_ar":        round(stone_ar, 4),
    "prong_count_est": prong_est,
    "melee_count_est": melee_est,
    "strata_count":    strata_count,
    "basket_depth":    basket_depth,
    "nsrf_mean":       round(sum(nsrf)/max(n,1), 2),
}))
"""

# ─────────────────────────────────────────────────────────────
#  Embedded worker: per-object mutable detail for stone debug
# ─────────────────────────────────────────────────────────────

DEBUG_WORKER_SRC = """\
import sys, json, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import rhino3dm
from jewelry_transform import fingerprint_object

path            = sys.argv[1]
mutable_indices = set(json.loads(sys.argv[2]))

model = rhino3dm.File3dm.Read(path)
if not model:
    print(json.dumps({"error": "cannot_read"}))
    sys.exit(0)

objs = []
fp_idx = 0
for obj in model.Objects:
    fp = fingerprint_object(obj)
    if fp is None:
        continue
    if fp_idx in mutable_indices:
        cx, cy, cz = fp["cx"], fp["cy"], fp["cz"]
        sx, sy, sz = fp["sx"], fp["sy"], fp["sz"]
        nsrf = fp.get("n_surfaces") or 0
        objs.append({
            "fp_idx":      fp_idx,
            "sig_hash":    fp["sig_hash"][:8],
            "object_type": fp.get("object_type", "?"),
            "cx": round(cx,4), "cy": round(cy,4), "cz": round(cz,4),
            "sx": round(sx,4), "sy": round(sy,4), "sz": round(sz,4),
            "n_surfaces":  nsrf,
            "xy_area":     round(sx*sy, 4),
            "volume":      round(sx*sy*sz, 4),
            "ar_xy":       round(max(sx,sy)/max(min(sx,sy),0.001), 4),
            "depth_ratio": round(sz/max(max(sx,sy),0.001), 4),
            "dist_center": round(math.sqrt(cx**2+cy**2), 4),
            "above_z0":    cz > 0,
        })
    fp_idx += 1

print(json.dumps({"mutable_objects": objs, "count": len(objs)}))
"""

# ─────────────────────────────────────────────────────────────
#  Embedded worker: cross-family synthesis (Architecture A / B)
# ─────────────────────────────────────────────────────────────

SYNTH_WORKER_SRC = """\
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import rhino3dm
from jewelry_transform import fingerprint_object, is_contaminant

args              = json.loads(sys.argv[1])
static_src        = args["static_src"]
donor_src         = args["donor_src"]
mutable_indices   = set(args["mutable_indices"])
static_hashes     = set(args["static_hashes"])
output_path       = args["output_path"]
affine            = args.get("affine")
z_filter          = args.get("z_filter")        # float threshold or None
min_z_guard       = args.get("min_z_guard")     # float or None — drop if bbox.Min.Z < threshold
z_offset_corr     = args.get("z_offset_correction")  # float or None — arch A z-shift
xy_offset_corr    = args.get("xy_offset_correction")  # {"x": float, "y": float} or None

static_model = rhino3dm.File3dm.Read(static_src)
donor_model  = rhino3dm.File3dm.Read(donor_src)
if not static_model or not donor_model:
    print(json.dumps({"error": "cannot_read", "ok": False}))
    sys.exit(0)

out = rhino3dm.File3dm()
for layer in static_model.Layers:
    out.Layers.Add(layer)
for mat in static_model.Materials:
    out.Materials.Add(mat)

def _add(model, obj):
    geom  = obj.Geometry
    attrs = obj.Attributes
    t = str(geom.ObjectType)
    try:
        if "Brep" in t:
            model.Objects.AddBrep(geom, attrs)
        elif "Curve" in t or "NurbsCurve" in t:
            model.Objects.AddCurve(geom, attrs)
        else:
            model.Objects.Add(geom, attrs)
    except Exception:
        try:
            model.Objects.Add(geom, attrs)
        except Exception:
            pass

# Build affine transform (Architecture B only)
affine_xf = None
if affine:
    sxy = float(affine.get("scale_xy", 1.0))
    sz  = float(affine.get("scale_z",  1.0))
    dx  = float(affine.get("translate_x", 0.0))
    dy  = float(affine.get("translate_y", 0.0))
    dz  = float(affine.get("translate_z", 0.0))
    try:
        plane0   = rhino3dm.Plane(
            rhino3dm.Point3d(0, 0, 0),
            rhino3dm.Vector3d(1, 0, 0),
            rhino3dm.Vector3d(0, 1, 0)
        )
        scale_xf = rhino3dm.Transform.Scale(plane0, sxy, sxy, sz)
        trans_xf = rhino3dm.Transform.Translation(
            rhino3dm.Vector3d(dx, dy, dz)
        )
        affine_xf = rhino3dm.Transform.Multiply(trans_xf, scale_xf)
    except Exception as e:
        print(json.dumps({"error": "affine_build_failed: " + str(e), "ok": False}))
        sys.exit(0)

# Build arch-A z-offset transform (pure Z translation; only when affine is absent)
z_offset_xf = None
if z_offset_corr is not None and affine_xf is None:
    try:
        z_offset_xf = rhino3dm.Transform.Translation(
            rhino3dm.Vector3d(0.0, 0.0, float(z_offset_corr))
        )
    except Exception:
        z_offset_xf = None

# Build arch-A XY-offset transform (pure XY translation; only when affine is absent)
xy_offset_xf = None
if xy_offset_corr is not None and affine_xf is None:
    try:
        xy_offset_xf = rhino3dm.Transform.Translation(
            rhino3dm.Vector3d(float(xy_offset_corr.get("x", 0.0)),
                              float(xy_offset_corr.get("y", 0.0)),
                              0.0)
        )
    except Exception:
        xy_offset_xf = None

# Add static objects from HM source file
static_added = 0
for obj in static_model.Objects:
    if is_contaminant(obj, static_model):
        continue
    fp = fingerprint_object(obj)
    if fp is None:
        continue
    if fp["sig_hash"] in static_hashes:
        _add(out, obj)
        static_added += 1

# Add mutable objects from donor file
mutable_added = 0
mutable_filtered = 0
fp_idx = 0
for obj in donor_model.Objects:
    if is_contaminant(obj, donor_model):
        continue
    fp = fingerprint_object(obj)
    if fp is None:
        continue
    if fp_idx in mutable_indices:
        if affine_xf is not None:
            try:
                obj.Geometry.Transform(affine_xf)
            except Exception:
                pass
        if z_filter is not None:
            try:
                bb = obj.Geometry.GetBoundingBox()
                cz = (bb.Min.Z + bb.Max.Z) / 2
                if cz < z_filter or (min_z_guard is not None and bb.Min.Z < min_z_guard):
                    mutable_filtered += 1
                    fp_idx += 1
                    continue
            except Exception:
                pass
        if z_offset_xf is not None:
            try:
                obj.Geometry.Transform(z_offset_xf)
            except Exception:
                pass
        if xy_offset_xf is not None:
            try:
                obj.Geometry.Transform(xy_offset_xf)
            except Exception:
                pass
        _add(out, obj)
        mutable_added += 1
    fp_idx += 1

os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
ok = out.Write(output_path, 7)
print(json.dumps({
    "ok":               ok,
    "static_added":     static_added,
    "mutable_added":    mutable_added,
    "mutable_filtered": mutable_filtered,
    "total":            static_added + mutable_added,
}))
"""

# ─────────────────────────────────────────────────────────────
#  Subprocess runner
# ─────────────────────────────────────────────────────────────

def _run_frame(src: Path, mutable_indices: list) -> dict | None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(
            [sys.executable, str(WORKER_FILE), str(src), json.dumps(mutable_indices)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, env=env,
        )
    except Exception:
        return None
    if not r.stdout.strip():
        return None
    try:
        d = json.loads(r.stdout.strip())
        return None if "error" in d else d
    except Exception:
        return None


def _run_parallel(tasks: list[tuple]) -> dict:
    """
    tasks: [(key, src_path, mutable_indices), ...]
    Returns {key: frame_dict_or_None}
    """
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_run_frame, src, idx): key
            for key, src, idx in tasks
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = None
    return results


# ─────────────────────────────────────────────────────────────
#  Library loader
# ─────────────────────────────────────────────────────────────

def load_all_libraries() -> dict:
    """Return {family: {cls, idx, shapes, static_count}} for every library."""
    libs = {}
    for cls_f in sorted(LIBRARY_DIR.glob("*/classification.json")):
        fam   = cls_f.parent.name
        idx_f = cls_f.parent / "shape_index.json"
        if not idx_f.exists():
            continue
        try:
            cls = json.loads(cls_f.read_text(encoding="utf-8"))
            idx = json.loads(idx_f.read_text(encoding="utf-8"))
            libs[fam] = {
                "cls":          cls,
                "idx":          idx,
                "shapes":       list(idx.keys()),
                "static_count": len(cls.get("exact_static_hashes", [])),
            }
        except Exception:
            pass
    return libs


# ─────────────────────────────────────────────────────────────
#  Scoring
# ─────────────────────────────────────────────────────────────

def _gauss(delta: float, sigma: float) -> float:
    return math.exp(-(delta ** 2) / (2 * sigma ** 2))


def score_donor(
    hm_bf:  dict,        # HM bridge-shape frame
    dn_bf:  dict,        # donor bridge-shape frame
    dn_tgt_count: int,   # donor mutable count for target shape
    hm_count_mean: float,
    hm_known: list,
    dn_shapes: list,
    hm_target_ar: float | None = None,  # expected stone AR for target shape (STONE_AR midpoint)
    dn_target_ar: float | None = None,  # donor's actual stone AR for target shape (from frame cache)
) -> tuple[float, dict]:
    """Composite score in [0,1] plus per-criterion breakdown.

    C1-C4 use bridge-shape frames (always available).
    C5 (stone AR similarity) uses target-shape frames; omitted with renormalization when absent.
    """

    # C1 — stone-centric frame similarity (all relative/ratio comparisons).
    #
    # stone_cz (sigma=0.20, tight): primary structural alignment — where the stone sits.
    #   cz_mean is unreliable because deep basket elements shift the mean without changing
    #   stone placement (e.g. SP6 cz_mean=7.7mm but stone_cz=12.1mm ≈ HM 12.3mm).
    #
    # footprint_xy (sigma=0.80, loose): affine scale_xy compensates size differences.
    #   Allow ±80% relative size before heavy penalty. SP6 (47% smaller) gets 0.84.
    #
    # basket_depth / z_range (sigma=0.50, moderate): how far the assembly extends below stone.
    #   Key discriminator: HM/SP6 deep basket (~12–14mm) vs Warren/GS_62 shallow (~3–5mm).
    #   Falls back to z_range when basket_depth unavailable (old cache entries).
    def _rel(a: float, b: float) -> float:
        return a / max(b, 0.001) - 1.0

    _dn_cz  = dn_bf.get("stone_cz") or dn_bf["cz_mean"]
    _hm_cz  = hm_bf.get("stone_cz") or hm_bf["cz_mean"]
    _dn_dep = dn_bf.get("basket_depth") or dn_bf.get("z_range", 1.0)
    _hm_dep = hm_bf.get("basket_depth") or hm_bf.get("z_range", 1.0)

    c1 = (
        _gauss(_rel(_dn_cz,              _hm_cz),              0.20) *
        _gauss(_rel(dn_bf["footprint_xy"], hm_bf["footprint_xy"]), 0.80) *
        _gauss(_rel(_dn_dep,              _hm_dep),              0.50)
    )

    # C2 — target shape object count vs HM expected
    c2 = max(0.0, 1.0 - abs(dn_tgt_count - hm_count_mean) / max(hm_count_mean, 1))

    # C3 — footprint/z_top ratio (ring-size normalised setting style).
    # Relative comparison: same style rings have similar ratios regardless of absolute size.
    r_dn = dn_bf["footprint_xy"] / max(dn_bf["z_top"], 0.01)
    r_hm = hm_bf["footprint_xy"] / max(hm_bf["z_top"], 0.01)
    c3 = _gauss(r_dn / max(r_hm, 0.001) - 1.0, 0.50)

    # C4 — shared known shapes with HM
    c4 = len(set(dn_shapes) & set(hm_known)) / max(len(hm_known), 1)

    breakdown = {
        "frame_similarity": round(c1, 4),
        "count_sim":        round(c2, 4),
        "spatial_ratio":    round(c3, 4),
        "shared_shapes":    round(c4, 4),
    }

    if hm_target_ar and dn_target_ar and hm_target_ar > 0 and dn_target_ar > 0:
        # C5 — stone aspect-ratio similarity (target-shape level)
        c5 = round(min(hm_target_ar, dn_target_ar) / max(hm_target_ar, dn_target_ar), 4)
        composite = round(0.35*c1 + 0.18*c2 + 0.18*c3 + 0.17*c4 + 0.12*c5, 4)
        breakdown["stone_ar_sim"] = c5
    else:
        composite = round(0.40*c1 + 0.20*c2 + 0.20*c3 + 0.20*c4, 4)

    return composite, breakdown


# ─────────────────────────────────────────────────────────────
#  Affine parameter estimate
# ─────────────────────────────────────────────────────────────

def estimate_affine(dn_bf: dict, hm_bf: dict) -> dict:
    """
    Estimate the scale+translate that maps donor geometry
    into High_Mira's coordinate frame, derived from bridge-shape comparison.
    """
    sxy = hm_bf["footprint_xy"] / max(dn_bf["footprint_xy"], 0.001)
    sz  = hm_bf["z_range"]      / max(dn_bf["z_range"],      0.001)
    dx  = hm_bf["cx_mean"] - sxy * dn_bf["cx_mean"]
    dy  = hm_bf["cy_mean"] - sxy * dn_bf["cy_mean"]
    dz  = hm_bf["cz_mean"] - sz  * dn_bf["cz_mean"]
    return {
        "scale_xy":    round(sxy, 4),
        "scale_z":     round(sz,  4),
        "translate_x": round(dx,  4),
        "translate_y": round(dy,  4),
        "translate_z": round(dz,  4),
    }


# ─────────────────────────────────────────────────────────────
#  Style classification + risk identification
# ─────────────────────────────────────────────────────────────

def _style_class(melee_count_est: int, prong_count_est: int) -> str:
    """Derive setting style from structural counts.

    HALO     — many small stones surrounding stone at girdle level
    PAVE     — modest melee count (shoulder pave, partial halo)
    SOLITAIRE — prong-only setting, no melee
    BARE     — minimal geometry (basket/bezel, GS_62-style)
    """
    if melee_count_est > 8:   return "HALO"
    if melee_count_est > 2:   return "PAVE"
    if prong_count_est >= 4:  return "SOLITAIRE"
    return "BARE"


def identify_risks(
    affine:        dict,
    dn_tgt_frame:  dict | None,
    dn_tgt_count:  int,
    hm_count_mean: float,
    score:         float,
    target_shape:  str,
    donor_family:  str,
    hm_tgt_frame:  dict | None = None,  # target family's bridge frame (style reference)
) -> list[dict]:
    risks = []

    sxy = affine["scale_xy"]
    if sxy > 1.50:
        risks.append({"factor": "SCALE_XY_HIGH", "severity": "HIGH",
                      "detail": f"Donor needs {sxy:.2f}x scale-up -- geometry distortion likely"})
    elif sxy < 0.67:
        risks.append({"factor": "SCALE_XY_LOW", "severity": "HIGH",
                      "detail": f"Donor needs {sxy:.2f}x scale-down -- distortion likely"})
    elif sxy > 1.25 or sxy < 0.80:
        risks.append({"factor": "SCALE_XY_MODERATE", "severity": "MEDIUM",
                      "detail": f"XY scale factor {sxy:.2f} -- moderate distortion risk"})

    sz = affine["scale_z"]
    if sz > 1.50 or sz < 0.67:
        risks.append({"factor": "SCALE_Z_HIGH", "severity": "MEDIUM",
                      "detail": f"Z scale factor {sz:.2f} -- vertical geometry will be stretched"})

    dz = affine["translate_z"]
    if abs(dz) > 2.5:
        risks.append({"factor": "Z_OFFSET_HIGH", "severity": "HIGH",
                      "detail": f"Z translation {dz:+.2f}mm -- stone height mismatch"})
    elif abs(dz) > 1.0:
        risks.append({"factor": "Z_OFFSET_MODERATE", "severity": "MEDIUM",
                      "detail": f"Z translation {dz:+.2f}mm -- verify prong alignment"})

    cnt_diff = abs(dn_tgt_count - hm_count_mean)
    if cnt_diff > 8:
        risks.append({"factor": "COUNT_MISMATCH_HIGH", "severity": "HIGH",
                      "detail": f"Donor has {dn_tgt_count} mutable objects vs HM ~{hm_count_mean:.0f} (delta={cnt_diff:.0f})"})
    elif cnt_diff > 4:
        risks.append({"factor": "COUNT_MISMATCH_MODERATE", "severity": "MEDIUM",
                      "detail": f"Object count off by {cnt_diff:.0f} from HM average"})

    if dn_tgt_frame:
        ar     = dn_tgt_frame.get("stone_ar", 1.0)
        lo, hi = STONE_AR.get(target_shape, (0.9, 2.5))
        if not (lo <= ar <= hi):
            risks.append({"factor": "STONE_AR_MISMATCH", "severity": "MEDIUM",
                          "detail": f"Donor stone AR {ar:.2f} outside expected [{lo}, {hi}] for {target_shape}"})

    if score < 0.35:
        risks.append({"factor": "LOW_SIMILARITY", "severity": "HIGH",
                      "detail": f"Score {score:.3f} -- no geometrically close donor found"})
    elif score < 0.50:
        risks.append({"factor": "MODERATE_SIMILARITY", "severity": "MEDIUM",
                      "detail": f"Score {score:.3f} -- moderate match, review recommended"})

    if donor_family.startswith("TM"):
        risks.append({"factor": "STYLE_TWO_STONE", "severity": "MEDIUM",
                      "detail": "Toi Et Moi family -- dual-stone setting may not transfer cleanly"})
    elif donor_family.startswith("TS"):
        risks.append({"factor": "STYLE_THREE_STONE", "severity": "LOW",
                      "detail": "Three-stone family -- side settings may introduce extra objects"})

    # Phase 3 — geometry style checks (require new cache fields; silently skip if absent)
    if hm_tgt_frame and dn_tgt_frame:
        hm_style = _style_class(hm_tgt_frame.get("melee_count_est", 0),
                                 hm_tgt_frame.get("prong_count_est", 0))
        dn_style = _style_class(dn_tgt_frame.get("melee_count_est", 0),
                                 dn_tgt_frame.get("prong_count_est", 0))
        if hm_style not in ("BARE",) and dn_style not in ("BARE",) and hm_style != dn_style:
            sev = "HIGH" if {"HALO"} & {hm_style, dn_style} else "MEDIUM"
            risks.append({"factor": "STYLE_MISMATCH",
                          "severity": sev,
                          "detail": f"Target style {hm_style} vs donor style {dn_style}"})

        hm_melee = hm_tgt_frame.get("melee_count_est", 0)
        dn_melee = dn_tgt_frame.get("melee_count_est", 0)
        if hm_melee > 6 and dn_melee == 0:
            risks.append({"factor": "MELEE_COUNT_MISMATCH", "severity": "HIGH",
                          "detail": f"Target has {hm_melee} melee stones; donor has none -- halo frame will not transfer"})

    return risks


def expected_confidence(score: float, risks: list[dict]) -> str:
    high = sum(1 for r in risks if r["severity"] == "HIGH")
    med  = sum(1 for r in risks if r["severity"] == "MEDIUM")
    if high:
        return "LOW"
    if score >= 0.65 and med == 0:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────────
#  Automatic Donor Intelligence helpers
# ─────────────────────────────────────────────────────────────

def recommend_filters(
    donor_family: str,
    shape: str,
    affine_params: dict,
    cache: dict,
    target_family: str | None = None,
) -> dict:
    """
    Predict synthesis filters needed for this donor→shape pair.

    Compares the donor's post-transform z_bottom against the target family's
    known z_bottom minimum. Only recommends min_z_guard when the donor lands
    more than 2 mm below the deepest known target shape — a signal that
    sub-plane geometry is anomalously deep relative to the target ring style.
    Normal ring geometry that dips below Z=0 (prongs, basket) should NOT
    trigger a guard because the z_range weight in compare_vs_real is small
    (0.10) and removing those objects collapses z_range coverage.
    Returns {"min_z_guard": {shape: 0.0} | {}, "z_filter": None}
    """
    key = f"{donor_family}|{shape}"
    donor_frame = cache.get(key)
    if not donor_frame or not affine_params:
        return {"min_z_guard": {}, "z_filter": None}

    z_bottom_pre  = donor_frame.get("z_bottom", 0.0)
    sz            = affine_params.get("scale_z",     1.0)
    dz            = affine_params.get("translate_z", 0.0)
    post_z_bottom = z_bottom_pre * sz + dz

    # Derive the guard threshold from the target family's known shapes.
    # guard fires only when donor is > 2 mm below the target's deepest known shape.
    guard_threshold = -2.0  # conservative default when no target data available
    if target_family:
        known_zbs = [
            v.get("z_bottom", 0.0)
            for k, v in cache.items()
            if k.startswith(f"{target_family}|") and "z_bottom" in v
        ]
        if known_zbs:
            guard_threshold = min(known_zbs) - 2.0

    min_z_guard = {shape: 0.0} if post_z_bottom < guard_threshold else {}

    # Z-offset correction for arch A: shift all mutable objects by (target_cz - donor_cz)
    # so the stone assembly lands at the correct height without affine scaling.
    donor_stone_cz  = donor_frame.get("stone_cz")
    target_stone_cz = None
    if target_family:
        tf = cache.get(f"{target_family}|{shape}")
        if tf:
            target_stone_cz = tf.get("stone_cz")
        # Missing shape: fall back to mean of known target shapes' stone_cz
        if target_stone_cz is None:
            known_czs = [
                v["stone_cz"] for k, v in cache.items()
                if k.startswith(f"{target_family}|") and v.get("stone_cz")
            ]
            if known_czs:
                target_stone_cz = sum(known_czs) / len(known_czs)
    if donor_stone_cz and target_stone_cz:
        z_offset_correction = round(target_stone_cz - donor_stone_cz, 4)
    else:
        z_offset_correction = None

    return {"min_z_guard": min_z_guard, "z_filter": None,
            "z_offset_correction": z_offset_correction}


def auto_select_arch(
    shape: str,
    donor_score: float,
    affine_params: dict,
    risks: list[dict],
    ar_sim: float | None = None,
    donor_z_range: float | None = None,
) -> list[str]:
    """
    Choose synthesis architecture(s) from donor quality and scale risk.

    Returns ["B"] when affine is reliable, ["A","B"] when uncertain,
    ["A"] when confidence is too low for affine to add value or when
    stone AR mismatch / shallow donor z_range makes affine XY distortion likely.
    z_offset_correction (from recommend_filters) handles Z height for arch A,
    so Z_OFFSET_HIGH as the sole HIGH risk does not force arch A here.
    architecture_strategy.json overrides this for explicitly mapped shapes.
    """
    high_scale = any(
        r["factor"] in {"SCALE_XY_HIGH", "SCALE_XY_LOW", "SCALE_Z_HIGH"}
        for r in risks
    )
    # Z_OFFSET_HIGH intrinsic to elevated-setting families; handled by z_offset_correction —
    # exclude it from the arch-selection high-risk check.
    high_risks_ex_zoffset = [
        r for r in risks
        if r["severity"] == "HIGH" and r["factor"] != "Z_OFFSET_HIGH"
    ]
    ar_mismatch      = ar_sim is not None and ar_sim < 0.80
    z_range_shallow  = donor_z_range is not None and donor_z_range < 3.0

    if ar_mismatch or z_range_shallow:
        # Affine XY distortion likely or scale_z unreliable: arch A + z_offset_correction
        if donor_score >= 0.45:
            return ["A"]
        else:
            return ["A"]
    elif donor_score >= 0.65 and not high_scale and not high_risks_ex_zoffset:
        return ["B"]
    elif donor_score >= 0.45:
        return ["A", "B"]
    else:
        return ["A"]


def production_decision(
    meta: dict,
    shape_result: dict | None,
) -> dict:
    """
    Rule-based accept / review / reject for a synthesized shape.

    meta        : synthesis *_meta.json dict
    shape_result: shape entry from compare-vs-real report, or None
    Returns {"decision": str, "reasons": list, "score": float|None,
             "ranking": str|None, "confidence": str, "filter_rate": float|None}
    """
    confidence   = meta.get("expected_confidence", "LOW")
    risk_factors = meta.get("risk_factors", [])
    high_risks   = [r for r in risk_factors if r.get("severity") == "HIGH"]
    style_risks  = [r for r in risk_factors if r.get("factor", "").startswith("STYLE_")]
    result       = meta.get("result", {})
    mut_added    = result.get("mutable_added",   0)
    mut_filtered = result.get("mutable_filtered", 0)
    mut_total    = mut_added + mut_filtered
    filter_rate  = (mut_filtered / mut_total) if mut_total > 0 else 0.0

    decision = "REVIEW_NEEDED"
    reasons: list[str] = []
    score_out:   float | None = None
    ranking_out: str   | None = None

    if high_risks:
        decision = "REJECT"
        reasons.append("HIGH risks: " + ", ".join(r["factor"] for r in high_risks))
    elif shape_result is not None:
        scoring   = shape_result.get("scoring", {})
        overall   = scoring.get("overall", 0.0)
        ranking   = shape_result.get("ranking", "POOR")
        static_ok = shape_result.get("static_integrity", {}).get("match", False)
        score_out, ranking_out = overall, ranking

        if not static_ok:
            decision = "REJECT"
            reasons.append("static_integrity=False")
        elif ranking == "POOR":
            decision = "REJECT"
            reasons.append(f"score={overall:.3f} POOR < 0.50 threshold")
        elif ranking == "EXCELLENT" and confidence == "HIGH":
            decision = "AUTO_ACCEPT"
            reasons.append(f"score={overall:.3f} EXCELLENT, confidence=HIGH, no HIGH risks")
        else:
            decision = "REVIEW_NEEDED"
            if ranking == "GOOD":
                reasons.append(f"score={overall:.3f} GOOD < 0.75 EXCELLENT threshold")
            if confidence == "MEDIUM":
                reasons.append("confidence=MEDIUM")
    else:
        decision = "REVIEW_NEEDED"
        reasons.append("compare-vs-real not available; metadata-only decision")

    if style_risks and decision == "AUTO_ACCEPT":
        decision = "REVIEW_NEEDED"
        reasons.append("style mismatch: " + style_risks[0]["factor"])

    if filter_rate > 0.15:
        if decision == "AUTO_ACCEPT":
            decision = "REVIEW_NEEDED"
        reasons.append(f"filter_rate={filter_rate:.0%} exceeds 15% threshold")

    return {
        "decision":    decision,
        "reasons":     reasons,
        "score":       score_out,
        "ranking":     ranking_out,
        "confidence":  confidence,
        "filter_rate": round(filter_rate, 3) if mut_total > 0 else None,
    }


# ─────────────────────────────────────────────────────────────
#  Cache helpers
# ─────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
#  Stone detection heuristics
# ─────────────────────────────────────────────────────────────

# Short labels and descriptions for the 7 heuristics
_H_NOTES = {
    "H1_max_xy_area":      "CURRENT -- largest XY footprint (broken: picks flat base components)",
    "H2_max_volume":       "largest 3D bounding-box volume",
    "H3_max_nsurfaces":    "most NURBS surfaces (faceted gem has many)",
    "H4_highest_cz":       "highest centroid Z (stone sits at top of setting)",
    "H5_max_xy_above_z0":  "largest XY area restricted to objects above Z=0",
    "H6_max_vol_not_flat": "largest volume excluding flat objects (depth_ratio <= 0.15)",
    "H7_combined":         "RECOMMENDED -- weighted rank: XY(0.25)+vol(0.25)+nsrf(0.15)+cz(0.25)+centrality(0.10)+flat-penalty",
}


def apply_heuristics(objs: list, shape: str) -> dict:
    """Apply 7 stone-detection heuristics and return ranked candidates + analysis."""
    n = len(objs)
    if n == 0:
        return {"error": "no_mutable_objects"}

    all_i     = list(range(n))
    above     = [i for i in all_i if objs[i]["above_z0"]]
    not_flat  = [i for i in all_i if objs[i]["depth_ratio"] > 0.15]

    s_xy   = sorted(all_i,  key=lambda i: -objs[i]["xy_area"])
    s_vol  = sorted(all_i,  key=lambda i: -objs[i]["volume"])
    s_nsrf = sorted(all_i,  key=lambda i: -objs[i]["n_surfaces"])
    s_cz   = sorted(all_i,  key=lambda i: -objs[i]["cz"])
    s_xy_ab = sorted(above,    key=lambda i: -objs[i]["xy_area"])
    s_vol_nf = sorted(not_flat, key=lambda i: -objs[i]["volume"])

    r_xy  = {idx: r for r, idx in enumerate(s_xy)}
    r_vol = {idx: r for r, idx in enumerate(s_vol)}
    r_nsf = {idx: r for r, idx in enumerate(s_nsrf)}
    r_cz  = {idx: r for r, idx in enumerate(s_cz)}

    # Combined rank score — lower = more likely to be the stone
    for i in all_i:
        dist  = objs[i]["dist_center"]
        depth = objs[i]["depth_ratio"]
        # Centrality reward: closer to ring axis (x=0,y=0) is better
        cent_bonus   = -n * 0.10 * math.exp(-(dist**2) / (2 * 1.5**2))
        # Flat penalty: depth_ratio < 0.15 → likely a prong-base plate, not a stone
        flat_penalty = n * 0.10 if depth < 0.15 else 0.0
        objs[i]["_comb"] = (
            0.25 * r_xy.get(i, n) +
            0.25 * r_vol.get(i, n) +
            0.15 * r_nsf.get(i, n) +
            0.25 * r_cz.get(i, n) +
            cent_bonus + flat_penalty
        )

    s_comb = sorted(all_i, key=lambda i: objs[i]["_comb"])

    def pick(lst): return lst[0] if lst else None

    picks = {
        "H1_max_xy_area":      pick(s_xy),
        "H2_max_volume":       pick(s_vol),
        "H3_max_nsurfaces":    pick(s_nsrf),
        "H4_highest_cz":       pick(s_cz),
        "H5_max_xy_above_z0":  pick(s_xy_ab),
        "H6_max_vol_not_flat": pick(s_vol_nf),
        "H7_combined":         pick(s_comb),
    }

    def fmt(i):
        if i is None:
            return None
        o = objs[i]
        return {
            "mutable_idx":  i,
            "sig_hash":     o["sig_hash"],
            "object_type":  o["object_type"],
            "cx":  o["cx"],  "cy": o["cy"],  "cz": o["cz"],
            "sx":  o["sx"],  "sy": o["sy"],  "sz": o["sz"],
            "n_surfaces":   o["n_surfaces"],
            "xy_area":      o["xy_area"],
            "volume":       o["volume"],
            "ar_xy":        o["ar_xy"],
            "depth_ratio":  o["depth_ratio"],
            "above_z0":     o["above_z0"],
            "dist_center":  o["dist_center"],
        }

    sel_vals  = [v for v in picks.values() if v is not None]
    dominant  = max(set(sel_vals), key=sel_vals.count) if sel_vals else None
    agreement = sel_vals.count(dominant) if dominant is not None else 0
    h1        = picks["H1_max_xy_area"]
    h7        = picks["H7_combined"]
    diverge   = h1 != h7

    # Suspicious: below z=0 AND large XY area — likely cause of broken H1
    suspicious = [
        {k: v for k, v in o.items() if not k.startswith("_")}
        for o in objs if not o["above_z0"] and o["xy_area"] > 20.0
    ]

    top5 = []
    for rank_i, obj_i in enumerate(s_comb[:5]):
        entry = fmt(obj_i)
        if entry:
            entry["rank"] = rank_i + 1
            entry["selected_by"] = [name for name, sel in picks.items() if sel == obj_i]
        top5.append(entry)

    return {
        "heuristic_results": {
            name: {
                "selected_idx": sel,
                "object":       fmt(sel),
                "note":         _H_NOTES.get(name, ""),
            }
            for name, sel in picks.items()
        },
        "consensus": {
            "dominant_idx":    dominant,
            "dominant_object": fmt(dominant),
            "agreement":       f"{agreement}/{len(picks)} heuristics",
            "h1_vs_h7_diverge": diverge,
            "diverge_reason":  (
                f"H1 picks idx={h1} cz={objs[h1]['cz']:.3f} depth={objs[h1]['depth_ratio']:.3f} above_z0={objs[h1]['above_z0']}"
                if diverge and h1 is not None else "H1 and H7 agree"
            ),
        },
        "top5_candidates": top5,
        "suspicious_objects_below_z0": suspicious,
        "stats": {
            "total_mutable":    n,
            "above_z0_count":   len(above),
            "not_flat_count":   len(not_flat),
            "cz_min":           min(o["cz"] for o in objs),
            "cz_max":           max(o["cz"] for o in objs),
            "xy_area_max":      max(o["xy_area"] for o in objs),
            "volume_max":       max(o["volume"] for o in objs),
            "n_surfaces_max":   max(o["n_surfaces"] for o in objs),
        },
    }


# ─────────────────────────────────────────────────────────────
#  Debug: stone detection command
# ─────────────────────────────────────────────────────────────

_DEBUG_FAMILIES = ["SP5", "SP6", "TS25", "ER1_Hidden", "ER2_Pave_H_Shank"]
_DEBUG_SHAPES   = {"AS", "OV", "PE", "PR", "RA", "RD", "EM"}  # EM = bridge for cross-check


def debug_stone_detection(out_path: Path) -> None:
    t0 = time.time()
    print("Loading libraries...")
    libs = load_all_libraries()

    # Build task list: (family, shape, src, mutable_indices)
    tasks = []
    for fam in _DEBUG_FAMILIES:
        if fam not in libs:
            print(f"  WARNING: {fam} not in libraries")
            continue
        fam_idx = libs[fam]["idx"]
        for shp in sorted(fam_idx):
            if shp in _DEBUG_SHAPES:
                src     = Path(fam_idx[shp]["source_file"])
                mut_idx = fam_idx[shp]["mutable_object_indices"]
                tasks.append((fam, shp, src, mut_idx))

    print(f"  {len(tasks)} (family, shape) files to inspect")

    WORKER_FILE.write_text(DEBUG_WORKER_SRC, encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def _run_debug(src, mut_idx):
        try:
            r = subprocess.run(
                [sys.executable, str(WORKER_FILE), str(src), json.dumps(mut_idx)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, env=env,
            )
        except Exception:
            return None
        if not r.stdout.strip():
            return None
        try:
            d = json.loads(r.stdout.strip())
            return None if "error" in d else d
        except Exception:
            return None

    raw: dict[tuple, dict | None] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_run_debug, src, mut_idx): (fam, shp)
            for fam, shp, src, mut_idx in tasks
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                raw[key] = fut.result()
            except Exception:
                raw[key] = None

    WORKER_FILE.unlink(missing_ok=True)
    print(f"  Loaded {sum(1 for v in raw.values() if v)} / {len(tasks)} files ok")

    # Apply heuristics per file
    families_out: dict[str, dict] = {}
    h1_h7_total, h1_h7_agree = 0, 0
    h1_below_z0_count = 0

    for fam, shp, src, _ in sorted(tasks, key=lambda t: (t[0], t[1])):
        result = raw.get((fam, shp))
        if not result:
            continue
        objs     = result["mutable_objects"]
        analysis = apply_heuristics(objs, shp)

        h1_h7_total += 1
        if not analysis.get("consensus", {}).get("h1_vs_h7_diverge", False):
            h1_h7_agree += 1
        h1_obj = analysis.get("heuristic_results", {}).get("H1_max_xy_area", {}).get("object")
        if h1_obj and not h1_obj.get("above_z0", True):
            h1_below_z0_count += 1

        if fam not in families_out:
            families_out[fam] = {}
        families_out[fam][shp] = {
            "source_file":    str(src),
            "mutable_count":  len(objs),
            "analysis":       analysis,
        }

    report = {
        "purpose":           "Center-stone detection heuristic diagnostic -- no synthesis",
        "analysis_date":     datetime.now().isoformat(timespec="seconds"),
        "families_analyzed": _DEBUG_FAMILIES,
        "shapes_checked":    sorted(_DEBUG_SHAPES),
        "aggregate": {
            "total_files":          h1_h7_total,
            "h1_h7_agree":          h1_h7_agree,
            "h1_h7_disagree":       h1_h7_total - h1_h7_agree,
            "h1_picks_below_z0":    h1_below_z0_count,
            "h1_below_z0_pct":      round(100 * h1_below_z0_count / max(h1_h7_total, 1), 1),
        },
        "heuristic_guide": _H_NOTES,
        "families": families_out,
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport -> {out_path}  ({round(time.time()-t0,1)}s)")

    _print_debug_summary(families_out, report["aggregate"])


def _print_debug_summary(fam_data: dict, agg: dict) -> None:
    print("\n" + "=" * 96)
    print("  Stone Detection Diagnostic")
    print("=" * 96)
    print(f"  Aggregate: {agg['total_files']} files  "
          f"H1==H7: {agg['h1_h7_agree']}/{agg['total_files']}  "
          f"H1 picks below-z0: {agg['h1_picks_below_z0']} ({agg['h1_below_z0_pct']}%)")
    print("")
    hdr = (f"  {'Family':<22} {'Shp':<5} {'N':>3}  "
           f"{'H1.cz':>7} {'H7.cz':>7}  {'H1==H7':>6}  "
           f"{'H1.ab':>5} {'H1.dep':>6} {'H1.nsrf':>7} {'H1.vol':>8}  "
           f"{'Susp':>4}  {'Top risk'}")
    print(hdr)
    print("  " + "-" * 94)

    for fam in sorted(fam_data):
        for shp in sorted(fam_data[fam]):
            d    = fam_data[fam][shp]
            a    = d["analysis"]
            hr   = a.get("heuristic_results", {})
            h1o  = hr.get("H1_max_xy_area",  {}).get("object")
            h7o  = hr.get("H7_combined",     {}).get("object")
            con  = a.get("consensus", {})
            sts  = a.get("stats", {})
            n    = d["mutable_count"]
            susp = len(a.get("suspicious_objects_below_z0", []))

            h1cz  = f"{h1o['cz']:+.3f}" if h1o else "    ---"
            h7cz  = f"{h7o['cz']:+.3f}" if h7o else "    ---"
            agree = "YES" if not con.get("h1_vs_h7_diverge", False) else "NO "
            h1ab  = ("Y" if h1o and h1o.get("above_z0") else "N") if h1o else "-"
            h1dep = f"{h1o['depth_ratio']:.3f}" if h1o else "  ---"
            h1nsr = str(h1o["n_surfaces"]) if h1o else "  ---"
            h1vol = f"{h1o['volume']:.1f}"  if h1o else "    ---"

            flag  = "  <-- H1 WRONG" if h1o and not h1o.get("above_z0") else ""
            print(f"  {fam:<22} {shp:<5} {n:>3}  "
                  f"{h1cz:>7} {h7cz:>7}  {agree:>6}  "
                  f"{h1ab:>5} {h1dep:>6} {h1nsr:>7} {h1vol:>8}  "
                  f"{susp:>4}{flag}")

    print("=" * 96)
    print("  Key: H1=max_xy_area(current)  H7=combined(recommended)")
    print("  H1.ab=above_z0  H1.dep=depth_ratio  H1.nsrf=n_surfaces  Susp=objects_below_z0_large_xy")


# ─────────────────────────────────────────────────────────────
#  Main analysis
# ─────────────────────────────────────────────────────────────

def analyze(target_family: str, out_path: Path, forced_bridge: str | None, rebuild_cache: bool) -> None:
    t0 = time.time()

    # ── 1. Load all libraries ──────────────────────────────────
    print("Loading libraries...")
    libs = load_all_libraries()
    if target_family not in libs:
        print(f"ERROR: '{target_family}' not found in {LIBRARY_DIR}/")
        sys.exit(1)

    hm       = libs[target_family]
    hm_known = hm["shapes"]
    hm_idx   = hm["idx"]

    missing = [s for s in ALL_SHAPES if s not in hm_known]
    print(f"  Target  : {target_family}")
    print(f"  Known   : {hm_known}")
    print(f"  Missing : {missing}")

    # ── 2. Choose bridge shape ─────────────────────────────────
    if forced_bridge:
        if forced_bridge not in hm_known:
            print(f"ERROR: bridge shape '{forced_bridge}' not in {target_family}'s known shapes {hm_known}")
            sys.exit(1)
        bridge = forced_bridge
    else:
        # Pick known shape with highest coverage across all libraries
        coverage = {}
        for s in hm_known:
            coverage[s] = sum(1 for f, fd in libs.items() if s in fd["shapes"])
        bridge = max(coverage, key=coverage.get)
        print(f"  Bridge  : {bridge} (coverage {coverage[bridge]}/{len(libs)} families)")

    # ── 3. Build donor pools per missing shape ─────────────────
    # Per-donor bridge selection: any shape common with HM (other than miss) is eligible.
    # This removes the global bridge constraint that excluded donors like SP6 whose bridge-
    # shape frame has an inflated footprint from pave-shank classification artifacts.
    pools: dict[str, list[str]] = {}
    for miss in missing:
        pool = [
            fam for fam, fd in libs.items()
            if fam != target_family
            and miss in fd["shapes"]
            and any(s in fd["shapes"] for s in hm_known if s != miss)
        ]
        pools[miss] = pool
        print(f"    {miss}: {len(pool)} donors (per-donor bridge)")

    # ── 4. Phase 1 — load bridge frames for all unique donors ──
    all_donor_fams = set(fam for pool in pools.values() for fam in pool)
    cache = {} if rebuild_cache else load_cache()

    # HM bridge + known shapes
    hm_frame_tasks = []
    for s in hm_known:
        key = f"{target_family}|{s}"
        if key not in cache:
            src  = Path(hm_idx[s]["source_file"])
            idxs = hm_idx[s]["mutable_object_indices"]
            hm_frame_tasks.append((key, src, idxs))

    # Donor bridge frames — load all (donor, hm_shape) pairs; per-donor selection happens later
    bridge_tasks = []
    for fam in all_donor_fams:
        for s in hm_known:
            if s not in libs[fam]["shapes"]:
                continue
            key = f"{fam}|{s}"
            if key not in cache:
                src  = Path(libs[fam]["idx"][s]["source_file"])
                idxs = libs[fam]["idx"][s]["mutable_object_indices"]
                bridge_tasks.append((key, src, idxs))

    total_p1 = len(hm_frame_tasks) + len(bridge_tasks)
    print(f"\nPhase 1: loading {total_p1} bridge frames ({len(hm_frame_tasks)} HM + {len(bridge_tasks)} donors)...")

    WORKER_FILE.write_text(FRAME_WORKER_SRC, encoding="utf-8")
    try:
        p1_results = _run_parallel(hm_frame_tasks + bridge_tasks)
    finally:
        WORKER_FILE.write_text(FRAME_WORKER_SRC, encoding="utf-8")  # keep for phase 2

    cache.update(p1_results)
    print(f"  Phase 1 done ({sum(1 for v in p1_results.values() if v)} / {total_p1} ok)")

    # ── 5. Pre-score all donors using bridge frames ────────────
    hm_bf = cache.get(f"{target_family}|{bridge}")
    if not hm_bf:
        print(f"ERROR: Could not load bridge frame for {target_family}|{bridge}")
        sys.exit(1)

    # Style reference frame for identify_risks(): use the majority (mode) style across all
    # known shapes, then pick the highest melee/prong within that majority group.
    # MAX would let a single outlier shape (e.g. Warren|MQ with 16 melee) force the whole
    # family to classify as HALO when 3/4 shapes are PAVE.
    _hm_style_frames = [
        (cache[f"{target_family}|{s}"].get("melee_count_est", 0),
         cache[f"{target_family}|{s}"].get("prong_count_est", 0))
        for s in hm_known
        if f"{target_family}|{s}" in cache
    ]
    if _hm_style_frames:
        _hm_styles_list = [_style_class(m, p) for m, p in _hm_style_frames]
        _hm_mode_style  = Counter(_hm_styles_list).most_common(1)[0][0]
        _hm_majority    = [
            (m, p)
            for (m, p), st in zip(_hm_style_frames, _hm_styles_list)
            if st == _hm_mode_style
        ]
        _hm_best = max(_hm_majority, key=lambda x: x[0])
        hm_style_ref = {"melee_count_est": _hm_best[0], "prong_count_est": _hm_best[1]}
    else:
        hm_style_ref = {"melee_count_est": 0, "prong_count_est": 0}

    hm_counts      = [hm_idx[s]["mutable_object_count"] for s in hm_known if s in hm_idx]
    hm_count_mean  = sum(hm_counts) / max(len(hm_counts), 1)

    prescored: dict[str, list] = {}
    for miss in missing:
        rows = []
        for fam in pools[miss]:
            # Per-donor best bridge: common shape with HM minimising footprint delta
            best_s      = None
            best_delta  = float("inf")
            for s in hm_known:
                if s == miss:
                    continue
                dn_frame_s = cache.get(f"{fam}|{s}")
                hm_frame_s = cache.get(f"{target_family}|{s}")
                if dn_frame_s and hm_frame_s:
                    d = abs(dn_frame_s["footprint_xy"] - hm_frame_s["footprint_xy"])
                    if d < best_delta:
                        best_delta = d
                        best_s     = s
            if not best_s:
                continue
            dn_bf_best   = cache[f"{fam}|{best_s}"]
            hm_bf_best   = cache[f"{target_family}|{best_s}"]
            dn_tgt_count = libs[fam]["idx"].get(miss, {}).get("mutable_object_count", 0)
            score, breakdown = score_donor(hm_bf_best, dn_bf_best, dn_tgt_count,
                                           hm_count_mean, hm_known, libs[fam]["shapes"])
            rows.append({
                "donor_family":       fam,
                "donor_score":        score,
                "score_breakdown":    breakdown,
                "donor_target_count": dn_tgt_count,
                "best_bridge_shape":  best_s,
            })
        rows.sort(key=lambda x: x["donor_score"], reverse=True)
        prescored[miss] = rows
        if rows:
            print(f"    {miss}: top scorer = {rows[0]['donor_family']} ({rows[0]['donor_score']:.4f}) bridge={rows[0]['best_bridge_shape']}")
        else:
            print(f"    {miss}: NO donors scored")

    # ── 6. Phase 2 — load target frames for top-N donors ──────
    target_frame_tasks = []
    for miss in missing:
        for row in prescored[miss][:PRESCORE_KEEP]:
            fam = row["donor_family"]
            key = f"{fam}|{miss}"
            if key not in cache:
                src  = Path(libs[fam]["idx"][miss]["source_file"])
                idxs = libs[fam]["idx"][miss]["mutable_object_indices"]
                target_frame_tasks.append((key, src, idxs))

    print(f"\nPhase 2: loading {len(target_frame_tasks)} target frames for top-{PRESCORE_KEEP} donors...")
    try:
        p2_results = _run_parallel(target_frame_tasks)
    finally:
        WORKER_FILE.unlink(missing_ok=True)

    cache.update(p2_results)
    save_cache(cache)
    print(f"  Phase 2 done ({sum(1 for v in p2_results.values() if v)} / {len(target_frame_tasks)} ok). Cache saved.")

    # ── 7. Build per-shape plans ───────────────────────────────
    hm_known_frames = {
        s: {
            "mutable_count": cache.get(f"{target_family}|{s}", {}).get("count"),
            "cz_mean":       cache.get(f"{target_family}|{s}", {}).get("cz_mean"),
            "footprint_xy":  cache.get(f"{target_family}|{s}", {}).get("footprint_xy"),
            "z_range":       cache.get(f"{target_family}|{s}", {}).get("z_range"),
            "stone_ar":      cache.get(f"{target_family}|{s}", {}).get("stone_ar"),
        }
        for s in hm_known
        if cache.get(f"{target_family}|{s}")
    }

    shape_plans = []
    for miss in missing:
        rows     = prescored[miss]
        top_rows = rows[:TOP_N]
        candidates = []

        # Expected stone AR for the missing shape (canonical midpoint from STONE_AR table)
        ar_lo, ar_hi   = STONE_AR.get(miss, (0.9, 2.5))
        hm_target_ar   = (ar_lo + ar_hi) / 2

        for rank, row in enumerate(top_rows, 1):
            fam           = row["donor_family"]
            score         = row["donor_score"]
            dn_tgt_count  = row["donor_target_count"]
            best_bridge   = row.get("best_bridge_shape", bridge)
            dn_bf         = cache.get(f"{fam}|{best_bridge}")
            hm_bf_row     = cache.get(f"{target_family}|{best_bridge}") or hm_bf
            dn_tf         = cache.get(f"{fam}|{miss}")

            affine  = estimate_affine(dn_bf, hm_bf_row) if dn_bf else {}
            risks   = identify_risks(affine, dn_tf, dn_tgt_count,
                                     hm_count_mean, score, miss, fam,
                                     hm_tgt_frame=hm_style_ref)

            # Refine score with target-shape criteria (applied after pre-scoring):
            #   C5 — stone AR similarity   (w=0.10 when available)
            #   C7 — melee count similarity (w=0.20 when available)
            #
            # Weights: base(C1-C4) = 1 - w_c5 - w_c7 so total always sums to 1.
            # C7 is the primary halo vs. non-halo discriminator — promotes HALO donors
            # when the target family is HALO, and correctly pairs PAVE/SOLITAIRE donors
            # with PAVE/SOLITAIRE targets.  When both melee counts are 0, c7=1.0 (match).
            dn_ar    = dn_tf.get("stone_ar")          if dn_tf else None
            dn_melee = dn_tf.get("melee_count_est", 0) if dn_tf else None
            hm_melee_ref = hm_style_ref.get("melee_count_est", 0)

            ar_sim = None
            if dn_ar and hm_target_ar > 0:
                ar_sim = round(min(hm_target_ar, dn_ar) / max(hm_target_ar, dn_ar), 4)

            c7 = None
            if dn_melee is not None:
                c7 = (1.0 if (hm_melee_ref == 0 and dn_melee == 0)
                      else min(hm_melee_ref, dn_melee) / max(max(hm_melee_ref, dn_melee), 1))
                c7 = round(c7, 4)

            w_c5   = 0.10 if ar_sim is not None else 0.0
            w_c7   = 0.20 if c7     is not None else 0.0
            w_base = round(1.0 - w_c5 - w_c7, 2)
            score  = round(
                w_base * score
                + (w_c5 * ar_sim if ar_sim is not None else 0.0)
                + (w_c7 * c7     if c7     is not None else 0.0),
                4,
            )

            conf    = expected_confidence(score, risks)
            rec_flt = recommend_filters(fam, miss, affine, cache, target_family)
            auto_arch_sel = auto_select_arch(
                miss, score, affine, risks,
                ar_sim=ar_sim,
                donor_z_range=dn_tf.get("z_range") if dn_tf else None,
            )

            candidates.append({
                "rank":                  rank,
                "donor_family":          fam,
                "donor_score":           score,
                "score_breakdown":       row["score_breakdown"],
                "stone_ar_sim":          ar_sim,
                "melee_sim":             c7,
                "bridge_shape_used":     best_bridge,
                "donor_target_count":    dn_tgt_count,
                "hm_expected_count":     round(hm_count_mean, 1),
                "affine_params":         affine,
                "donor_target_frame":    {
                    "stone_ar":         dn_tf.get("stone_ar"),
                    "stone_cz":         dn_tf.get("stone_cz"),
                    "footprint_xy":     dn_tf.get("footprint_xy"),
                    "z_range":          dn_tf.get("z_range"),
                    "prong_count_est":  dn_tf.get("prong_count_est"),
                    "melee_count_est":  dn_tf.get("melee_count_est"),
                    "strata_count":     dn_tf.get("strata_count"),
                    "basket_depth":     dn_tf.get("basket_depth"),
                    "count":            dn_tf.get("count"),
                } if dn_tf else None,
                "expected_confidence":   conf,
                "risk_factors":          risks,
                "risk_count":            {"HIGH":   sum(1 for r in risks if r["severity"]=="HIGH"),
                                          "MEDIUM": sum(1 for r in risks if r["severity"]=="MEDIUM"),
                                          "LOW":    sum(1 for r in risks if r["severity"]=="LOW")},
                "recommended_filters":   rec_flt,
                "auto_arch":             auto_arch_sel,
            })

        # Re-sort by final score (post C5+C7) and re-assign ranks.
        # Pre-scoring ordered by C1-C4; post-hoc criteria may change the order.
        candidates.sort(key=lambda d: d["donor_score"], reverse=True)
        for i, d in enumerate(candidates, 1):
            d["rank"] = i

        high_conf = sum(1 for d in candidates if d["expected_confidence"] == "HIGH")
        med_conf  = sum(1 for d in candidates if d["expected_confidence"] == "MEDIUM")

        shape_plans.append({
            "missing_shape":           miss,
            "donor_pool_size":         len(pools[miss]),
            "scored_donors":           len(rows),
            "best_score":              rows[0]["donor_score"] if rows else None,
            "high_confidence_in_top15": high_conf,
            "medium_confidence_in_top15": med_conf,
            "top_candidates":          candidates,
        })

    # ── 8. Assemble and write plan ─────────────────────────────
    plan = {
        "target_family":     target_family,
        "analysis_date":     datetime.now().isoformat(timespec="seconds"),
        "known_shapes":      hm_known,
        "missing_shapes":    missing,
        "bridge_shape":      bridge,
        "target_frame":      {
            "bridge_shape":   bridge,
            "cz_mean":        hm_bf.get("cz_mean"),
            "footprint_xy":   hm_bf.get("footprint_xy"),
            "z_range":        hm_bf.get("z_range"),
            "z_top":          hm_bf.get("z_top"),
            "stone_ar":       hm_bf.get("stone_ar"),
            "mutable_count":  hm_bf.get("count"),
            "mutable_count_mean_all_shapes": round(hm_count_mean, 2),
        },
        "known_shape_frames": hm_known_frames,
        "missing_shape_plans": shape_plans,
        "library_count":     len(libs),
        "elapsed_seconds":   round(time.time() - t0, 1),
    }

    out_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"\nReport written -> {out_path}")

    # ── 9. Console summary table ───────────────────────────────
    print("")
    print("=" * 80)
    print(f"  Transfer Plan Summary for: {target_family}")
    print("=" * 80)
    print(f"  Bridge shape : {bridge}")
    print(f"  HM frame     : cz={hm_bf.get('cz_mean'):.3f}  footprint={hm_bf.get('footprint_xy'):.3f}  z_range={hm_bf.get('z_range'):.3f}  count={hm_bf.get('count')}")
    print(f"  Expected obj count (mean across known shapes): {hm_count_mean:.1f}")
    print("")
    hdr = f"  {'Shape':<6}  {'Pool':>4}  {'Best Donor':<22}  {'Score':>5}  {'Conf':<4}  {'sXY':>5}  {'dZ':>6}  {'High/Med Risks'}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for sp in shape_plans:
        miss = sp["missing_shape"]
        pool = sp["donor_pool_size"]
        if sp["top_candidates"]:
            best  = sp["top_candidates"][0]
            donor = best["donor_family"][:22]
            score = f"{best['donor_score']:.3f}"
            conf  = best["expected_confidence"][:4]
            sxy   = f"{best['affine_params'].get('scale_xy', 0):.3f}" if best["affine_params"] else "  ---"
            dz    = f"{best['affine_params'].get('translate_z', 0):+.2f}" if best["affine_params"] else "  ---"
            hr    = best["risk_count"]["HIGH"]
            mr    = best["risk_count"]["MEDIUM"]
            risks_str = f"{hr} HIGH, {mr} MED"
        else:
            donor, score, conf, sxy, dz, risks_str = "NO DONORS FOUND", "  ---", "----", "  ---", "  ---", "---"
        print(f"  {miss:<6}  {pool:>4}  {donor:<22}  {score:>5}  {conf:<4}  {sxy:>5}  {dz:>6}  {risks_str}")
    print("=" * 80)
    print(f"  Elapsed: {round(time.time()-t0, 1)}s")


# ─────────────────────────────────────────────────────────────
#  Contamination check (read-only; never modifies source files)
# ─────────────────────────────────────────────────────────────

def contamination_check(family_dir: Path) -> dict:
    """Scan a family's .3dm files and report Gem-layer refs and GroundPlanes.

    Read-only: files are opened, inspected, and discarded.  Nothing is written.
    Returns {"gem_refs": {shape: count}, "groundplanes": {shape: count}, "clean": bool}.
    """
    import re
    import rhino3dm
    from jewelry_transform import is_contaminant

    SHAPES = ["ELCU", "SquareCU", "AS", "CU", "EM", "MQ", "OV", "PE", "PR", "RA", "RD"]
    gem_refs: dict[str, int] = {}
    groundplanes: dict[str, int] = {}

    for fpath in sorted(family_dir.glob("*.3dm")):
        stem = fpath.stem.upper()
        tokens = set(re.split(r"[_\s\-\(\)\.]+", stem))
        shape = next((s for s in SHAPES if s in tokens), fpath.stem)
        model = rhino3dm.File3dm.Read(str(fpath))
        if not model:
            continue
        n_gem = n_gnd = 0
        for obj in model.Objects:
            li = obj.Attributes.LayerIndex
            layer = model.Layers[li].Name if li < len(model.Layers) else ""
            if layer.strip() == "Gem":
                n_gem += 1
            else:
                try:
                    bb = obj.Geometry.GetBoundingBox()
                    if bb:
                        dx = bb.Max.X - bb.Min.X
                        dy = bb.Max.Y - bb.Min.Y
                        dz = bb.Max.Z - bb.Min.Z
                        if max(dx, dy) > 150 and dz < 2:
                            n_gnd += 1
                except Exception:
                    pass
        if n_gem:
            gem_refs[shape] = n_gem
        if n_gnd:
            groundplanes[shape] = n_gnd

    clean = not gem_refs and not groundplanes
    return {"gem_refs": gem_refs, "groundplanes": groundplanes, "clean": clean}


# ─────────────────────────────────────────────────────────────
#  Phase 1: build full frame database for all families x shapes
# ─────────────────────────────────────────────────────────────

def build_frame_db(rebuild: bool) -> None:
    t0 = time.time()
    print("=" * 60)
    print("  Building Full Frame Database")
    print("=" * 60)
    libs = load_all_libraries()

    # Warn about contamination in source files; filtering happens in-memory during
    # FRAME_WORKER execution, so source files are never modified.
    dataset_root = Path("3dm")
    for fam in sorted(libs):
        fam_dir = dataset_root / fam
        if fam_dir.is_dir():
            result = contamination_check(fam_dir)
            if not result["clean"]:
                print(f"  [WARN] {fam}: contaminants detected (will be filtered in-memory)")
                if result["gem_refs"]:
                    print(f"         gem_refs    : {result['gem_refs']}")
                if result["groundplanes"]:
                    print(f"         groundplanes: {result['groundplanes']}")

    cache = {} if rebuild else load_cache()

    tasks: list[tuple] = []
    already = 0
    for fam, lib in libs.items():
        for shp, shp_info in lib["idx"].items():
            key = f"{fam}|{shp}"
            if key in cache:
                already += 1
                continue
            src  = Path(shp_info["source_file"])
            idxs = shp_info["mutable_object_indices"]
            tasks.append((key, src, idxs))

    total_pairs = sum(len(lib["idx"]) for lib in libs.values())
    print(f"  {len(libs)} families  |  {total_pairs} (family,shape) pairs total")
    print(f"  In cache : {already}  |  To load: {len(tasks)}")

    if not tasks:
        print("  Cache is already complete. Use --rebuild-cache to force rebuild.")
        print(f"  Elapsed: {round(time.time()-t0, 1)}s")
        return

    WORKER_FILE.write_text(FRAME_WORKER_SRC, encoding="utf-8")
    try:
        results = _run_parallel(tasks)
    finally:
        WORKER_FILE.unlink(missing_ok=True)

    ok_count = sum(1 for v in results.values() if v is not None)
    cache.update(results)
    save_cache(cache)

    fail_keys = [k for k, v in results.items() if v is None]
    print(f"\n  Loaded : {ok_count}/{len(tasks)} ok  |  Failed: {len(fail_keys)}")
    if fail_keys:
        print(f"  Failed (first 10): {fail_keys[:10]}")
    print(f"  Total cache entries: {len(cache)}")
    print(f"  Elapsed: {round(time.time()-t0, 1)}s")


# ─────────────────────────────────────────────────────────────
#  Phase 2/3: synthesis (Architecture A = no-transform, B = affine)
# ─────────────────────────────────────────────────────────────

def _run_synth(args: dict) -> dict | None:
    """Run SYNTH_WORKER_SRC in a subprocess; return parsed JSON or None."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    SYNTH_WORKER_FILE.write_text(SYNTH_WORKER_SRC, encoding="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, str(SYNTH_WORKER_FILE), json.dumps(args)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, env=env,
        )
    except Exception:
        return None
    if not r.stdout.strip():
        return None
    try:
        d = json.loads(r.stdout.strip())
        return None if d.get("error") else d
    except Exception:
        return None


def _load_strategy(strategy_path: Path | None) -> dict | None:
    """Load architecture_strategy.json if it exists."""
    p = strategy_path or Path("architecture_strategy.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _archs_for_shape(shape: str, strategy: dict | None, default_arch: str) -> list[str]:
    """Return list of architectures to run for this shape per strategy (or [default_arch])."""
    if strategy:
        routing = strategy.get("shape_routing", {})
        if shape in routing:
            return routing[shape]  # may be [], ["A"], ["B"], or ["A","B"]
    return [default_arch]


# ─────────────────────────────────────────────────────────────
#  Phase 6: blacklist helpers + pre-reject + single-file validator
# ─────────────────────────────────────────────────────────────

def _load_blacklist() -> dict:
    if BLACKLIST_FILE.exists():
        try:
            return json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_blacklist(bl: dict) -> None:
    BLACKLIST_FILE.write_text(json.dumps(bl, indent=2), encoding="utf-8")


def _bl_key(family: str, shape: str, donor: str) -> str:
    return f"{family}|{shape}|{donor}"


def _bl_status(bl: dict, family: str, shape: str, donor: str) -> str:
    """Return 'clean' | 'warning' | 'temporary_blacklist' | 'permanent_blacklist'."""
    entry = bl.get(_bl_key(family, shape, donor))
    return entry.get("status", "clean") if entry else "clean"


def _record_strike(bl: dict, family: str, shape: str, donor: str, validation: dict) -> None:
    """Increment strike counter for (family, shape, donor); update status."""
    key   = _bl_key(family, shape, donor)
    entry = bl.get(key, {"strikes": 0, "status": "clean", "history": []})
    n     = entry["strikes"] + 1
    if n >= STRIKE_PERM:
        status = "permanent_blacklist"
    elif n >= STRIKE_TEMP:
        status = "temporary_blacklist"
    else:
        status = "warning"
    now    = datetime.now().isoformat(timespec="seconds")
    checks = validation.get("checks", {})
    failed = [k for k, v in checks.items() if v.get("verdict") == "FAIL"]
    details = {
        k: {kk: vv for kk, vv in v.items() if kk != "verdict"}
        for k, v in checks.items() if k in failed
    }
    entry.update({"strikes": n, "status": status, "last_strike": now})
    if "first_strike" not in entry:
        entry["first_strike"] = now
    entry["history"].append({
        "timestamp":         now,
        "rejection_point":   "post_synthesis",
        "validator_verdict": validation.get("verdict"),
        "checks_failed":     failed,
        "check_details":     details,
    })
    bl[key] = entry


def _pre_reject(
    candidate:     dict,
    hm_count_mean: float,
    hm_style_ref:  dict,
    is_elevated:   bool = False,
) -> tuple[bool, list[str]]:
    """Deterministic pre-synthesis rejection from frame cache data.

    Returns (reject: bool, reasons: list[str]).
    Reasons are ephemeral — re-computed each run from the plan.  No persistence.
    """
    reasons: list[str] = []
    dn_tf  = candidate.get("donor_target_frame") or {}
    dn_cnt = dn_tf.get("count", 0)
    dn_mel = dn_tf.get("melee_count_est", 0)

    # PR-1: predicted contaminant rate (melee-heavy donor → stone InstanceRefs dominate output)
    # Skip when target itself is halo/pave (melee >= 8) — melee stones are expected mutable assets.
    _hm_mel_ref = int(hm_style_ref.get("melee_count_est", 0))
    if _hm_mel_ref < 8 and dn_cnt > 0 and (dn_mel / dn_cnt) > 0.40:
        reasons.append(
            f"CONTAMINANT_RATE_PREDICTED ({dn_mel}/{dn_cnt}={dn_mel/dn_cnt:.0%})"
        )

    # PR-2: object count inflation or sparsity
    # Elevated-setting targets have structurally low counts (7–10); use wider ceiling so
    # standard donors (15–25 objects) survive to be tried — Z-filter trims the excess.
    if hm_count_mean > 0 and dn_cnt > 0:
        ratio = dn_cnt / hm_count_mean
        inflated_ceil = ELEVATED_COUNT_RATIO if is_elevated else 1.65
        if ratio > inflated_ceil:
            reasons.append(f"COUNT_INFLATED ({ratio:.2f}x)")
        elif ratio < 0.55:
            reasons.append(f"COUNT_SPARSE ({ratio:.2f}x)")

    # PR-3: STYLE_MISMATCH HIGH (already computed in identify_risks)
    for risk in candidate.get("risk_factors", []):
        if risk["factor"] == "STYLE_MISMATCH" and risk["severity"] == "HIGH":
            reasons.append("STYLE_MISMATCH_HIGH")
            break

    return bool(reasons), reasons


def _validate_output_file(
    out_file:         Path,
    arch:             str,
    affine_params:    dict | None,
    result_meta:      dict,
    static_hashes:    list[str],
    exp_stone_cz:     float,
    exp_basket_depth: float,
    exp_count:        float,
    shape:            str,
    elevated_mode:    bool = False,
) -> dict:
    """Run VALIDATE_WORKER_SRC on one synthesized file and compute all 8 checks.

    Assumes VALIDATE_WORKER_FILE is already written.
    Returns {"verdict", "n_warn", "n_fail", "checks", "frame"}.
    """
    worker_args = {"file": str(out_file), "static_hashes": static_hashes}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    frame = None
    for _vattempt in range(2):
        try:
            r = subprocess.run(
                [sys.executable, str(VALIDATE_WORKER_FILE), json.dumps(worker_args)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180, env=env,
            )
            if r.stdout.strip():
                frame = json.loads(r.stdout.strip())
                if frame.get("error"):
                    frame = None
                else:
                    break
            _err = (r.stderr or "").strip()
            if _err and _vattempt == 0:
                print(f"       [validate-worker stderr] {_err[:300]}")
        except Exception as _vexc:
            if _vattempt == 0:
                print(f"       [validate-worker exception] {_vexc}")
        if _vattempt == 0 and frame is None:
            import time as _vtime; _vtime.sleep(0.25)

    if frame is None:
        return {"verdict": "ERROR", "n_warn": 0, "n_fail": 1, "checks": {}, "frame": None}

    aff        = affine_params or {}
    sxy        = aff.get("scale_xy",    1.0)
    sz         = aff.get("scale_z",     1.0)
    dz         = aff.get("translate_z", 0.0)
    res        = result_meta or {}
    m_added    = res.get("mutable_added",    0)
    m_filtered = res.get("mutable_filtered", 0)
    m_total    = m_added + m_filtered
    loss_pct   = round(m_filtered / max(m_total, 1) * 100, 1)

    checks: dict = {}

    stone_cz = frame.get("stone_cz", 0.0)
    cz_delta = stone_cz - exp_stone_cz
    checks["stone_cz"] = {
        "actual": round(stone_cz, 3), "expected": round(exp_stone_cz, 3),
        "delta": round(cz_delta, 3),
        "verdict": "PASS" if abs(cz_delta) < 1.5 else ("WARN" if abs(cz_delta) < 3.0 else "FAIL"),
    }

    stone_ar     = frame.get("stone_ar", 1.0)
    ar_lo, ar_hi = STONE_AR.get(shape, (0.8, 3.0))
    if ar_lo <= stone_ar <= ar_hi:
        ar_v = "PASS"
    elif (ar_lo - 0.20) <= stone_ar <= (ar_hi + 0.20):
        ar_v = "WARN"
    else:
        ar_v = "FAIL"
    checks["stone_ar"] = {
        "actual": round(stone_ar, 3), "expected_range": [ar_lo, ar_hi], "verdict": ar_v,
    }

    out_count   = frame.get("mutable_count", 0)
    count_ratio = out_count / max(exp_count, 1)
    checks["object_count"] = {
        "actual": out_count, "expected": round(exp_count, 1), "ratio": round(count_ratio, 3),
        "verdict": _vcheck(count_ratio, 0.75, 1.40, 0.55, 1.65),
    }

    basket   = frame.get("basket_depth", 0.0)
    bd_ratio = basket / max(exp_basket_depth, 0.001)
    if exp_basket_depth < 0.5:
        # Near-zero expected (flat/bezel-set families): ratio check is meaningless.
        # Use absolute difference instead so donors with basket≈0 can pass.
        bd_abs    = abs(basket - exp_basket_depth)
        bd_verdict = "PASS" if bd_abs < 1.0 else ("WARN" if bd_abs < 3.0 else "FAIL")
    else:
        bd_verdict = _vcheck(bd_ratio, 0.75, 1.45, 0.55, 1.80)
    checks["basket_depth"] = {
        "actual": round(basket, 3), "expected": round(exp_basket_depth, 3),
        "ratio": round(bd_ratio, 3),
        "verdict": bd_verdict,
    }

    cx   = frame.get("stone_cx", 0.0)
    cy   = frame.get("stone_cy", 0.0)
    dist = math.sqrt(cx**2 + cy**2)
    checks["stone_centered"] = {
        "cx": round(cx, 3), "cy": round(cy, 3), "dist": round(dist, 3),
        "verdict": "PASS" if dist < 1.5 else ("WARN" if dist < 3.5 else "FAIL"),
    }

    checks["mutable_loss"] = {
        "filtered": m_filtered, "total": m_total, "loss_pct": loss_pct,
        "verdict": "PASS" if loss_pct < 10 else ("WARN" if loss_pct < 25 else "FAIL"),
    }

    if arch == "B" and aff:
        # Elevated-setting targets have structurally larger translate_z (stone_cz difference
        # between standard-height donor and elevated target can be 8-12mm). Relax bounds.
        dz_pass = 12.0 if elevated_mode else 3.5
        dz_warn = 15.0 if elevated_mode else 7.0
        if 0.80 <= sxy <= 1.25 and abs(dz) < dz_pass:
            aff_v = "PASS"
        elif 0.65 <= sxy <= 1.40 and abs(dz) < dz_warn:
            aff_v = "WARN"
        else:
            aff_v = "FAIL"
        checks["affine_sanity"] = {
            "scale_xy": round(sxy, 4), "scale_z": round(sz, 4),
            "translate_z": round(dz, 4), "verdict": aff_v,
        }

    raw_n     = frame.get("raw_object_count", frame.get("total_count", 1))
    cont_n    = frame.get("contaminant_count", 0)
    cont_rate = round(cont_n / max(raw_n, 1) * 100, 1)
    checks["contaminant_rate"] = {
        "raw_objects": raw_n, "contaminants": cont_n, "rate_pct": cont_rate,
        "verdict": "PASS" if cont_rate < 5 else ("WARN" if cont_rate < 40 else "FAIL"),
    }

    verdicts = [c["verdict"] for c in checks.values()]
    n_fail   = verdicts.count("FAIL")
    n_warn   = verdicts.count("WARN")
    overall  = "FAIL" if n_fail > 0 else ("WARN" if n_warn > 0 else "PASS")
    return {"verdict": overall, "n_warn": n_warn, "n_fail": n_fail, "checks": checks, "frame": frame}


def synthesize(
    family: str,
    plan_path: Path,
    out_dir: Path,
    default_arch: str,
    target_shapes: list | None,
    strategy_path: Path | None = None,
    donor_rank: int = 1,
    z_filter: float | None = None,
    min_z_guards: dict | None = None,
    z_offsets: dict | None = None,  # shape → z_offset_correction float (arch A only)
    auto_fallback: bool = False,
    max_fallback_attempts: int = 5,
) -> None:
    t0 = time.time()

    strategy = _load_strategy(strategy_path)
    strategy_note = f" (strategy: {strategy_path or 'architecture_strategy.json'})" if strategy else ""
    print("=" * 60)
    print(f"  Cross-Family Synthesis{strategy_note}")
    print("=" * 60)

    if not plan_path.exists():
        print(f"ERROR: Plan file not found: {plan_path}")
        print("  Run: python cross_family_transfer.py analyze <family> first.")
        sys.exit(1)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan["target_family"] != family:
        print(f"WARNING: Plan is for '{plan['target_family']}', not '{family}'")

    libs = load_all_libraries()
    if family not in libs:
        print(f"ERROR: '{family}' not in libraries.")
        sys.exit(1)

    fam_lib = libs[family]
    fam_cls = fam_lib["cls"]
    fam_idx = fam_lib["idx"]

    static_hashes = (
        set(fam_cls.get("exact_static_hashes", [])) |
        set(fam_cls.get("fuzzy_static_hashes",  []))
    )

    known_shapes = plan["known_shapes"]
    if not known_shapes:
        print("ERROR: No known shapes in plan.")
        sys.exit(1)
    static_src = str(Path(fam_idx[known_shapes[0]]["source_file"]))

    # Compute z_filter threshold for SYNTH_WORKER
    # None  → auto from known-shape z_bottom envelope (default)
    # -1e9  → disabled (--no-z-filter)
    # float → explicit threshold
    if z_filter is None:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        zbs = [cache[f"{family}|{s}"]["z_bottom"]
               for s in known_shapes if f"{family}|{s}" in cache and "z_bottom" in cache[f"{family}|{s}"]]
        _z_filter: float | None = (round(min(zbs) - 0.5, 4)) if zbs else None
    elif z_filter <= -1e8:
        _z_filter = None  # disabled — pass None to worker so no filtering occurs
    else:
        _z_filter = z_filter

    missing_shapes = plan["missing_shapes"]
    if target_shapes:
        missing_shapes = [s for s in target_shapes if s in missing_shapes]
        if not missing_shapes:
            print(f"ERROR: Specified shapes {target_shapes} not in missing: {plan['missing_shapes']}")
            sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Family      : {family}")
    print(f"  Known       : {known_shapes}")
    print(f"  Synthesize  : {missing_shapes}")
    print(f"  Static src  : {static_src}")
    print(f"  Static hashes: {len(static_hashes)}")
    print(f"  Output dir  : {out_dir}")
    if _z_filter is not None:
        print(f"  Z-filter    : cz < {_z_filter} mm will be dropped")
    if min_z_guards:
        for _s, _t in min_z_guards.items():
            print(f"  min_z guard : {_s} — drop if bbox.Min.Z < {_t} mm")
    if strategy:
        print(f"  Routing     :", end="")
        for s in missing_shapes:
            archs = _archs_for_shape(s, strategy, default_arch)
            print(f"  {s}:{'+'.join(archs) or 'SKIP'}", end="")
        print()
    print()

    shape_plan_by_name = {sp["missing_shape"]: sp for sp in plan["missing_shape_plans"]}
    results = []

    # ── auto-fallback setup ────────────────────────────────────
    # Compute expected validation metrics once (reused per shape).
    # Load blacklist; pre-write VALIDATE_WORKER once for efficiency.
    _af_blacklist: dict = {}
    _af_exp_stone_cz = _af_exp_basket = _af_exp_count = 0.0
    _af_hm_count_mean = plan["target_frame"].get("mutable_count_mean_all_shapes", 0.0)
    _af_hm_style_ref: dict = {}
    _af_is_elevated = False
    if auto_fallback:
        _af_blacklist = _load_blacklist()
        _af_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
        _af_hm_frames = [_af_cache[f"{family}|{s}"]
                         for s in plan["known_shapes"] if f"{family}|{s}" in _af_cache]
        if _af_hm_frames:
            def _af_mean(k):
                vs = [f[k] for f in _af_hm_frames if f.get(k) is not None]
                return sum(vs) / len(vs) if vs else 0.0
            def _af_median(k):
                vs = sorted(f[k] for f in _af_hm_frames if f.get(k) is not None)
                if not vs: return 0.0
                mid = len(vs) // 2
                return vs[mid] if len(vs) % 2 else (vs[mid - 1] + vs[mid]) / 2
            _af_exp_stone_cz = _af_median("stone_cz") or _af_median("cz_mean") or _af_mean("stone_cz")
            _af_exp_basket   = _af_mean("basket_depth")
            _af_exp_count    = _af_mean("count")
            # hm_style_ref: mode-style majority (same logic as analyze())
            _af_style_pairs  = [(_af_mean("melee_count_est"), _af_mean("prong_count_est"))]
            _af_hm_style_ref = {"melee_count_est": int(_af_style_pairs[0][0]),
                                 "prong_count_est": int(_af_style_pairs[0][1])}
        VALIDATE_WORKER_FILE.write_text(VALIDATE_WORKER_SRC, encoding="utf-8")
        print(f"  Auto-fallback: ON  (max attempts per shape: {max_fallback_attempts})")
        print(f"  Expected     : stone_cz={_af_exp_stone_cz:.2f}mm"
              f"  basket={_af_exp_basket:.2f}mm  count={_af_exp_count:.1f}")
        _af_is_elevated = (_af_exp_basket > 0) and (_af_exp_basket < ELEVATED_BASKET_THRESHOLD)
        if _af_is_elevated:
            print(f"  Elevated-setting target detected (basket ~{_af_exp_basket:.1f}mm)"
                  f" -- extended attempt pool ({ELEVATED_MIN_ATTEMPTS} min attempts)")
        print()

    for miss in missing_shapes:
        archs_to_run = _archs_for_shape(miss, strategy, default_arch)

        if not archs_to_run:
            print(f"  {miss}: SKIP (strategy routes to no architectures)")
            results.append({"shape": miss, "status": "SKIP", "reason": "strategy_empty"})
            continue

        sp = shape_plan_by_name.get(miss)
        if not sp or not sp["top_candidates"]:
            print(f"  {miss}: NO CANDIDATES -- skipping")
            results.append({"shape": miss, "status": "SKIP", "reason": "no_candidates"})
            continue

        cands = sp["top_candidates"]

        # ── candidate selection ─────────────────────────────────
        if auto_fallback:
            # Filter: skip pre-rejected and blacklisted donors
            _pre_ok: list[dict] = []
            _pre_skipped = 0
            for c in cands:
                rej, reasons = _pre_reject(c, _af_hm_count_mean, _af_hm_style_ref,
                                           is_elevated=_af_is_elevated)
                bl_st = _bl_status(_af_blacklist, family, miss, c["donor_family"])
                if rej:
                    _pre_skipped += 1
                elif bl_st in ("temporary_blacklist", "permanent_blacklist"):
                    _pre_skipped += 1
                else:
                    _pre_ok.append(c)
            if not _pre_ok:
                note = (f"all {len(cands)} candidates pre-rejected or blacklisted"
                        if _pre_skipped == len(cands)
                        else f"{_pre_skipped} of {len(cands)} pre-rejected/blacklisted; pool empty")
                print(f"  {miss}: REVIEW_NEEDED -- {note}")
                results.append({"shape": miss, "status": "REVIEW_NEEDED",
                                 "reason": note, "pre_rejected": _pre_skipped})
                continue
            print(f"  {miss}: {len(_pre_ok)} valid candidates"
                  f" ({_pre_skipped} pre-rejected/blacklisted)")
            # Re-sort for elevated-setting targets: prefer style-matching donors with
            # sufficient count (Z-filter will trim some off, leaving the right amount).
            if _af_is_elevated and len(_pre_ok) > 1:
                _tgt_style = _style_class(
                    int(_af_hm_style_ref.get("melee_count_est", 0)),
                    int(_af_hm_style_ref.get("prong_count_est", 0)),
                )
                def _elev_sort_key(c, _ts=_tgt_style, _ec=_af_exp_count):
                    dtf   = c.get("donor_target_frame") or {}
                    d_sty = _style_class(int(dtf.get("melee_count_est", 0)),
                                         int(dtf.get("prong_count_est", 0)))
                    d_cnt = dtf.get("count", 0)
                    return (0 if d_sty == _ts else 1,    # style match first
                            0 if d_cnt >= _ec else 1,    # count above mean second
                            -c["donor_score"])            # score as tiebreaker
                _pre_ok = sorted(_pre_ok, key=_elev_sort_key)
            # Elevated archA-only shapes: switch to archB when donors sit much
            # higher than the target stone (gap > 5 mm).  archB scale_z compresses
            # the donor basket depth into range, while the stone_cz-anchor (below)
            # corrects dZ so the stone lands at the target family's cz.
            if (_af_is_elevated and archs_to_run == ["A"] and _af_exp_stone_cz > 0 and _pre_ok):
                _sc       = _pre_ok[0]
                _sc_dn_cz = _af_cache.get(
                    f"{_sc.get('donor_family','')}|{_sc.get('bridge_shape_used','')}", {}
                ).get("stone_cz", 0.0)
                if _sc_dn_cz > 0 and abs(_af_exp_stone_cz - _sc_dn_cz) > 5.0:
                    archs_to_run = ["B"]
                    print(f"  {miss}: cz-gap {abs(_af_exp_stone_cz - _sc_dn_cz):.1f}mm"
                          f" -- elevated archA -> archB (stone_cz-anchor)")
        else:
            pick_idx = min(donor_rank - 1, len(cands) - 1)
            _pre_ok  = [cands[pick_idx]]   # single candidate, existing behavior

        # Pull common fields from the first candidate for printing
        best      = _pre_ok[0]
        donor_fam = best["donor_family"]
        score     = best["donor_score"]
        conf      = best["expected_confidence"]

        if donor_fam not in libs:
            print(f"  {miss}: donor '{donor_fam}' missing from libs -- skipping")
            results.append({"shape": miss, "status": "SKIP", "reason": "donor_not_in_libs"})
            continue

        # ── candidate attempt loop ──────────────────────────────
        # auto_fallback=False: single iteration (_pre_ok has exactly one entry)
        # auto_fallback=True:  iterate up to max_fallback_attempts valid candidates;
        #                      elevated targets get at least ELEVATED_MIN_ATTEMPTS
        _shape_accepted = False
        _attempt_log: list[dict] = []
        if auto_fallback:
            _attempt_limit = max(max_fallback_attempts,
                                 ELEVATED_MIN_ATTEMPTS if _af_is_elevated else 0)
        else:
            _attempt_limit = 1

        for _attempt_n, best in enumerate(_pre_ok[:_attempt_limit], 1):
            donor_fam = best["donor_family"]
            score     = best["donor_score"]
            conf      = best["expected_confidence"]

            if donor_fam not in libs:
                if auto_fallback:
                    continue
                print(f"  {miss}: donor '{donor_fam}' missing from libs -- skipping")
                results.append({"shape": miss, "status": "SKIP",
                                 "reason": "donor_not_in_libs"})
                break

            donor_idx = libs[donor_fam]["idx"]
            if miss not in donor_idx:
                if auto_fallback:
                    continue
                print(f"  {miss}: donor '{donor_fam}' has no {miss} shape -- skipping")
                results.append({"shape": miss, "status": "SKIP",
                                 "reason": "donor_missing_shape"})
                break

            donor_src = str(Path(donor_idx[miss]["source_file"]))
            mut_idxs  = donor_idx[miss]["mutable_object_indices"]

            attempt_pfx = f"  [{_attempt_n}]" if auto_fallback else " "
            print(f"{attempt_pfx} {miss}: donor={donor_fam} score={score:.3f} conf={conf}"
                  f" mut={len(mut_idxs)}  archs=[{'+'.join(archs_to_run)}]")

            # Remove stale arch files only when using canonical rank-1 donor (no fallback)
            if not auto_fallback:
                for obsolete_arch in ("A", "B"):
                    if donor_rank > 1:
                        break
                    if obsolete_arch not in archs_to_run:
                        stale      = out_dir / f"{family}_{miss}_arch{obsolete_arch}.3dm"
                        stale_meta = out_dir / f"{family}_{miss}_arch{obsolete_arch}_meta.json"
                        if stale.exists():
                            try:
                                stale.unlink()
                                print(f"       removed stale arch{obsolete_arch}: {stale.name}")
                            except PermissionError:
                                print(f"       WARNING: cannot remove {stale.name}"
                                      f" (file in use -- close it in Rhino)")
                        if stale_meta.exists():
                            try:
                                stale_meta.unlink()
                            except PermissionError:
                                pass

            _attempt_accepted = False
            for arch in archs_to_run:
                affine_p = best["affine_params"] if arch == "B" else None

                # Stone-cz anchor: override dZ when bridge frame comparison mismatches
                # target stone height by >2 mm.  Only fires for outlier cases (e.g. three-
                # stone families where the bridge shape's cz_mean != center stone height).
                if auto_fallback and arch == "B" and affine_p and _af_exp_stone_cz > 0:
                    _bshape = best.get("bridge_shape_used")
                    _dn_fam = best.get("donor_family", "")
                    # Prefer target-shape cz over bridge-shape cz: the donor's PE/PR/OV stone
                    # may sit at a different height than its bridge shape.
                    _dn_tgt_cz  = _af_cache.get(f"{_dn_fam}|{miss}", {}).get("stone_cz")
                    _dn_ref_cz  = _dn_tgt_cz or (
                        _af_cache.get(f"{_dn_fam}|{_bshape}", {}).get("stone_cz")
                        if _bshape else None
                    )
                    if _dn_ref_cz:
                        _sz = affine_p.get("scale_z", 1.0)
                        _stone_dz = _af_exp_stone_cz - _sz * _dn_ref_cz
                        if abs(_stone_dz - affine_p["translate_z"]) > 2.0:
                            affine_p = dict(affine_p)
                            affine_p["translate_z"] = round(_stone_dz, 4)
                            print(f"       [stone_cz-anchor] dz "
                                  f"{best['affine_params']['translate_z']:+.2f}"
                                  f" -> {_stone_dz:+.2f}mm"
                                  f" (exp={_af_exp_stone_cz:.2f}"
                                  f" dn_tgt_cz={_dn_ref_cz:.2f})")

                rank_tag = f"_r{donor_rank}" if (donor_rank > 1 and not auto_fallback) else ""
                out_stem = f"{family}_{miss}_arch{arch}{rank_tag}"
                out_3dm  = out_dir / f"{out_stem}.3dm"
                out_meta = out_dir / f"{out_stem}_meta.json"

                affine_note = ""
                if arch == "B" and affine_p:
                    affine_note = (f" sxy={affine_p['scale_xy']:.3f}"
                                   f" sz={affine_p['scale_z']:.3f}"
                                   f" dz={affine_p['translate_z']:+.2f}mm")

                # archA z-offset: in auto-fallback, prefer target-shape frame over bridge-shape
                # plan value — the donor's target-shape stone_cz may differ from its bridge shape.
                if arch == "A":
                    if auto_fallback and _af_exp_stone_cz > 0:
                        _dn_tgt_cz_a = _af_cache.get(f"{donor_fam}|{miss}", {}).get("stone_cz")
                        _z_off = (round(_af_exp_stone_cz - _dn_tgt_cz_a, 4)
                                  if _dn_tgt_cz_a
                                  else (z_offsets.get(miss) if z_offsets else None))
                    else:
                        _z_off = z_offsets.get(miss) if z_offsets else None
                else:
                    _z_off = None

                synth_args = {
                    "static_src":      static_src,
                    "donor_src":       donor_src,
                    "mutable_indices": mut_idxs,
                    "static_hashes":   list(static_hashes),
                    "output_path":     str(out_3dm),
                    "affine":          affine_p,
                    "z_filter":        _z_filter,
                    "min_z_guard":     min_z_guards.get(miss) if min_z_guards else None,
                    "z_offset_correction": _z_off,
                }

                t_shape = time.time()
                result  = _run_synth(synth_args)
                elapsed = round(time.time() - t_shape, 1)

                if result and result.get("ok"):
                    size_kb  = out_3dm.stat().st_size // 1024 if out_3dm.exists() else 0
                    filtered = result.get("mutable_filtered", 0)
                    flt_note = f" filtered={filtered}" if filtered else ""
                    print(f"       [arch{arch}]{affine_note}  "
                          f"static={result['static_added']} mutable={result['mutable_added']}"
                          f"{flt_note} total={result['total']} ({size_kb} KB) [{elapsed}s]")
                    synth_ok = True
                else:
                    print(f"       [arch{arch}] FAIL  [{elapsed}s]")
                    synth_ok = False

                # ── Phase 6: post-synthesis validation (auto-fallback only) ──
                val_verdict = None
                val_result  = None
                if auto_fallback and synth_ok and out_3dm.exists():
                    val_result = _validate_output_file(
                        out_file         = out_3dm,
                        arch             = arch,
                        affine_params    = affine_p,
                        result_meta      = result,
                        static_hashes    = list(static_hashes),
                        exp_stone_cz     = _af_exp_stone_cz,
                        exp_basket_depth = _af_exp_basket,
                        exp_count        = _af_exp_count,
                        shape            = miss,
                        elevated_mode    = _af_is_elevated,
                    )
                    val_verdict = val_result["verdict"]
                    warn_n = val_result["n_warn"]
                    fail_n = val_result["n_fail"]
                    print(f"       [validate]  verdict={val_verdict}"
                          f"  warn={warn_n}  fail={fail_n}")
                    # Track check results for REVIEW_NEEDED diagnostic
                    _attempt_log.append({
                        "donor":  donor_fam,
                        "n_pass": 8 - val_result.get("n_fail", 0) - val_result.get("n_warn", 0),
                        "n_fail": val_result.get("n_fail", 0),
                        "checks": val_result.get("checks", {}),
                    })
                    if val_verdict == "FAIL":
                        # Record strike and try next candidate
                        _record_strike(_af_blacklist, family, miss, donor_fam, val_result)
                        _save_blacklist(_af_blacklist)
                        strikes_now = _af_blacklist[
                            _bl_key(family, miss, donor_fam)]["strikes"]
                        status_now  = _af_blacklist[
                            _bl_key(family, miss, donor_fam)]["status"]
                        print(f"       [blacklist] {donor_fam} -> strikes={strikes_now}"
                              f" status={status_now}")
                        synth_ok = False  # treat this attempt as failed
                    elif val_verdict == "ERROR":
                        synth_ok = False  # validator crashed; try next candidate

                    # Stone-centering correction: if stone lands off-center, apply a
                    # counter-translation and re-synthesize once.  Works for both
                    # archB (updates translate_x/y in affine_p) and archA (passes
                    # xy_offset_correction into synth_args).
                    if (synth_ok and val_result
                            and val_result.get("checks", {}).get(
                                "stone_centered", {}).get("verdict") == "WARN"):
                        _xy_cx   = val_result["checks"]["stone_centered"].get("cx",   0.0)
                        _xy_cy   = val_result["checks"]["stone_centered"].get("cy",   0.0)
                        _xy_dist = val_result["checks"]["stone_centered"].get("dist", 0.0)
                        if _xy_dist > 0.5:
                            if arch == "B" and affine_p:
                                affine_p = dict(affine_p)
                                affine_p["translate_x"] = round(
                                    affine_p.get("translate_x", 0.0) - _xy_cx, 4)
                                affine_p["translate_y"] = round(
                                    affine_p.get("translate_y", 0.0) - _xy_cy, 4)
                                synth_args["affine"] = affine_p
                            else:  # archA
                                _xy_prev = synth_args.get("xy_offset_correction") \
                                           or {"x": 0.0, "y": 0.0}
                                synth_args["xy_offset_correction"] = {
                                    "x": round(_xy_prev.get("x", 0.0) - _xy_cx, 4),
                                    "y": round(_xy_prev.get("y", 0.0) - _xy_cy, 4),
                                }
                            print(f"       [xy-center] correction"
                                  f" dx={-_xy_cx:+.3f} dy={-_xy_cy:+.3f}")
                            _r2 = _run_synth(synth_args)
                            if _r2 and _r2.get("ok"):
                                val_result2 = _validate_output_file(
                                    out_file         = out_3dm,
                                    arch             = arch,
                                    affine_params    = affine_p,
                                    result_meta      = _r2,
                                    static_hashes    = list(static_hashes),
                                    exp_stone_cz     = _af_exp_stone_cz,
                                    exp_basket_depth = _af_exp_basket,
                                    exp_count        = _af_exp_count,
                                    shape            = miss,
                                    elevated_mode    = _af_is_elevated,
                                )
                                if val_result2["verdict"] != "ERROR":
                                    val_result  = val_result2
                                    val_verdict = val_result["verdict"]
                                    result = _r2
                                    print(f"       [xy-center] revised"
                                          f" verdict={val_verdict}"
                                          f"  warn={val_result['n_warn']}"
                                          f"  fail={val_result['n_fail']}")

                status = "OK" if synth_ok else "FAIL"

                meta_extra: dict = {}
                if auto_fallback:
                    meta_extra = {
                        "fallback_mode":        True,
                        "attempt_number":       _attempt_n,
                        "pre_rejected_count":   (len(cands) - len(_pre_ok)),
                        "elevated_setting_mode": _af_is_elevated,
                        "validation_verdict":   val_verdict,
                        "validation_checks":    val_result.get("checks") if val_result else None,
                    }

                meta = {
                    "target_family":         family,
                    "target_shape":          miss,
                    "architecture":          arch,
                    "synthesis_date":        datetime.now().isoformat(timespec="seconds"),
                    "donor_family":          donor_fam,
                    "donor_score":           score,
                    "expected_confidence":   conf,
                    "risk_factors":          best.get("risk_factors", []),
                    "affine_params":         affine_p,
                    "static_hashes_count":   len(static_hashes),
                    "mutable_indices_count": len(mut_idxs),
                    "result":                result,
                    "status":                status,
                    "output_file":           str(out_3dm),
                    **meta_extra,
                }
                out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")

                if synth_ok:
                    results.append({"shape": miss, "arch": arch, "status": status,
                                     "donor": donor_fam, "conf": conf,
                                     "attempt": _attempt_n if auto_fallback else None,
                                     "validation": val_verdict})
                    _attempt_accepted = True

            if _attempt_accepted:
                _shape_accepted = True
                break   # move on to next shape
            # else: try next candidate in fallback loop

        if auto_fallback and not _shape_accepted:
            print(f"  {miss}: REVIEW_NEEDED -- all {min(len(_pre_ok), _attempt_limit)}"
                  f" attempts exhausted")
            if _attempt_log:
                best_att = max(_attempt_log, key=lambda x: x["n_pass"])
                fail_cts: dict[str, int] = {}
                for r in _attempt_log:
                    for chk, v in r["checks"].items():
                        verdict = v["verdict"] if isinstance(v, dict) else v
                        if verdict == "FAIL":
                            fail_cts[chk] = fail_cts.get(chk, 0) + 1
                top_fails = sorted(fail_cts, key=lambda k: -fail_cts[k])[:3]
                print(f"  Best attempt: {best_att['donor']}"
                      f" ({best_att['n_pass']}/8 passed, {best_att['n_fail']} failed)")
                if top_fails:
                    fail_str = ", ".join(f"{k}({fail_cts[k]}x)" for k in top_fails)
                    print(f"  Most-failing checks: {fail_str}")
            results.append({"shape": miss, "status": "REVIEW_NEEDED",
                             "reason": "all_attempts_failed"})

    SYNTH_WORKER_FILE.unlink(missing_ok=True)
    if auto_fallback:
        VALIDATE_WORKER_FILE.unlink(missing_ok=True)

    print()
    print("=" * 60)
    print(f"  Synthesis Complete -- {family}")
    print("=" * 60)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    rv_count = sum(1 for r in results if r["status"] == "REVIEW_NEEDED")
    print(f"  {ok_count}/{len(results)} outputs generated successfully"
          + (f"  ({rv_count} REVIEW_NEEDED)" if rv_count else ""))
    for r in results:
        mark  = "OK" if r["status"] == "OK" else ("RV" if r["status"] == "REVIEW_NEEDED" else "--")
        donor = r.get("donor", "---")
        conf  = r.get("conf",  "---")
        arch  = r.get("arch",  "?")
        val   = f"  val={r['validation']}" if r.get("validation") else ""
        att   = f"  attempt={r['attempt']}" if r.get("attempt") else ""
        print(f"  [{mark}] {r['shape']:<5} arch{arch}  donor={donor:<20}  conf={conf}{att}{val}")
    if auto_fallback:
        bl = _load_blacklist()
        fam_strikes = {k: v for k, v in bl.items() if k.startswith(f"{family}|")}
        if fam_strikes:
            print(f"\n  Blacklist entries for {family}: {len(fam_strikes)}")
            for k, v in sorted(fam_strikes.items()):
                print(f"    {k}  strikes={v['strikes']}  status={v['status']}")
    print(f"  Output : {out_dir}/")
    print(f"  Elapsed: {round(time.time()-t0, 1)}s")


# ─────────────────────────────────────────────────────────────
#  Real-vs-generated comparison
# ─────────────────────────────────────────────────────────────

COMPARE_WORKER_SRC = """\
import sys, json, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import rhino3dm
from jewelry_transform import fingerprint_object

args          = json.loads(sys.argv[1])
synth_path    = args["synth_path"]
real_path     = args["real_path"]
static_hashes = set(args["static_hashes"])
shape         = args["shape"]

def read_fps(path):
    model = rhino3dm.File3dm.Read(path)
    if not model:
        return None
    fps = []
    for obj in model.Objects:
        fp = fingerprint_object(obj)
        if fp is not None:
            fps.append(fp)
    return fps

synth_fps = read_fps(synth_path)
real_fps  = read_fps(real_path)
if synth_fps is None or real_fps is None:
    print(json.dumps({"error": "cannot_read"}))
    sys.exit(0)

def split(fps):
    st = [f for f in fps if f["sig_hash"] in static_hashes]
    mu = [f for f in fps if f["sig_hash"] not in static_hashes]
    return st, mu

s_st, s_mu = split(synth_fps)
r_st, r_mu = split(real_fps)

# 1. Object counts
count_delta        = abs(len(synth_fps) - len(real_fps))
mutable_count_delta = abs(len(s_mu) - len(r_mu))

# 2. Static integrity
s_sh = set(f["sig_hash"] for f in s_st)
r_sh = set(f["sig_hash"] for f in r_st)
static_match = s_sh == r_sh

# 5. Geometry hash overlap (mutable)
s_mh = set(f["sig_hash"] for f in s_mu)
r_mh = set(f["sig_hash"] for f in r_mu)
shared_mu      = s_mh & r_mh
mu_overlap_n   = len(shared_mu)
mu_overlap_pct = round(mu_overlap_n / max(len(r_mh), 1) * 100, 1)

# 3. Bbox similarity
def mframe(fps):
    if not fps:
        return None
    czs  = [f["cz"] for f in fps]
    sxs  = [f["sx"] for f in fps]
    sys_ = [f["sy"] for f in fps]
    szs  = [f["sz"] for f in fps]
    cxs  = [f["cx"] for f in fps]
    cys  = [f["cy"] for f in fps]
    return {
        "count":        len(fps),
        "cz_mean":      round(sum(czs)/len(fps), 4),
        "cx_mean":      round(sum(cxs)/len(fps), 4),
        "cy_mean":      round(sum(cys)/len(fps), 4),
        "footprint_xy": round(max(max(sxs), max(sys_)), 4),
        "z_top":        round(max(czs), 4),
        "z_bottom":     round(min(czs), 4),
        "z_range":      round(max(czs) - min(czs), 4),
        "xy_span_x":    round(max(cxs) - min(cxs), 4),
        "xy_span_y":    round(max(cys) - min(cys), 4),
        "silhouette":   round(sum(f["sx"]*f["sy"] for f in fps), 2),
    }

sf = mframe(s_mu)
rf = mframe(r_mu)
bbox = {}
if sf and rf:
    bbox = {
        "footprint_ratio":  round(sf["footprint_xy"] / max(rf["footprint_xy"], 0.001), 4),
        "z_range_ratio":    round(sf["z_range"]      / max(rf["z_range"],      0.001), 4),
        "z_top_delta":      round(sf["z_top"]    - rf["z_top"],    4),
        "cz_mean_delta":    round(sf["cz_mean"]  - rf["cz_mean"],  4),
        "xy_span_x_ratio":  round(sf["xy_span_x"] / max(rf["xy_span_x"], 0.001), 4),
        "xy_span_y_ratio":  round(sf["xy_span_y"] / max(rf["xy_span_y"], 0.001), 4),
        "silhouette_ratio": round(sf["silhouette"] / max(rf["silhouette"], 0.001), 4),
    }

# 4. Center-stone placement (H7)
def detect_stone(fps):
    n = len(fps)
    if n == 0:
        return None
    czs  = [f["cz"] for f in fps];  sxs  = [f["sx"] for f in fps]
    sys_ = [f["sy"] for f in fps];  szs  = [f["sz"] for f in fps]
    cxs  = [f["cx"] for f in fps];  cys  = [f["cy"] for f in fps]
    nsrf = [f.get("n_surfaces") or 0 for f in fps]
    areas   = [sxs[i]*sys_[i]       for i in range(n)]
    volumes = [sxs[i]*sys_[i]*szs[i] for i in range(n)]
    dists   = [math.sqrt(cxs[i]**2+cys[i]**2) for i in range(n)]
    dr      = [szs[i]/max(max(sxs[i],sys_[i]),0.001) for i in range(n)]
    def rank(vals, asc=True):
        order = sorted(range(n), key=lambda i: vals[i], reverse=not asc)
        return {idx: r for r, idx in enumerate(order)}
    r_xy  = rank(areas,   asc=False);  r_vol = rank(volumes, asc=False)
    r_nsf = rank(nsrf,    asc=False);  r_cz  = rank(czs,     asc=False)
    scores = []
    for i in range(n):
        cb  = -n*0.10*math.exp(-(dists[i]**2)/(2*1.5**2))
        fpp = n*0.10 if dr[i] < 0.15 else 0.0
        scores.append(0.25*r_xy[i]+0.25*r_vol[i]+0.15*r_nsf[i]+0.25*r_cz[i]+cb+fpp)
    st = scores.index(min(scores))
    sx, sy = sxs[st], sys_[st]
    return {
        "cz": round(czs[st],4), "cx": round(cxs[st],4), "cy": round(cys[st],4),
        "footprint": round(max(sx,sy),4),
        "ar":        round(max(sx,sy)/max(min(sx,sy),0.001), 4),
        "above_z0":  czs[st] > 0,
    }

s_stone = detect_stone(s_mu)
r_stone = detect_stone(r_mu)
stone = {}
if s_stone and r_stone:
    stone = {
        "synth":            s_stone,
        "real":             r_stone,
        "cz_delta":         round(s_stone["cz"]        - r_stone["cz"],        4),
        "ar_delta":         round(s_stone["ar"]         - r_stone["ar"],        4),
        "footprint_delta":  round(s_stone["footprint"]  - r_stone["footprint"], 4),
        "cx_delta":         round(s_stone["cx"]         - r_stone["cx"],        4),
        "cy_delta":         round(s_stone["cy"]         - r_stone["cy"],        4),
    }

# 6. Silhouette similarity (in bbox above)

# 7. Symmetry deviation
def sym_dev(fps):
    if not fps:
        return None
    return round(sum(abs(f["cx"])+abs(f["cy"]) for f in fps)/len(fps), 4)
sym = {
    "synth_axis_dev": sym_dev(s_mu),
    "real_axis_dev":  sym_dev(r_mu),
    "delta":          round((sym_dev(s_mu) or 0)-(sym_dev(r_mu) or 0), 4),
}

# Scoring
def c01(x): return max(0.0, min(1.0, x))
sc = {}
sc["static_integrity"] = 1.0 if static_match else 0.0
sc["count_similarity"] = c01(1.0 - mutable_count_delta / 10.0)
sc["stone_cz"]   = math.exp(-(stone.get("cz_delta",99)**2)/(2*2.0**2))  if stone else 0.0
sc["stone_ar"]   = math.exp(-(stone.get("ar_delta",99)**2)/(2*0.3**2))  if stone else 0.0
sc["footprint"]  = c01(1.0 - abs(bbox.get("footprint_ratio",0)-1.0)/0.3) if bbox else 0.0
sc["z_range"]    = c01(1.0 - abs(bbox.get("z_range_ratio",0) -1.0)/0.3) if bbox else 0.0
sc["silhouette"] = c01(1.0 - abs(bbox.get("silhouette_ratio",0)-1.0)/0.5) if bbox else 0.0
sc["symmetry"]   = math.exp(-(sym["delta"]**2)/(2*1.0**2)) if sym["delta"] is not None else 0.0

W = {"static_integrity":0.15,"count_similarity":0.15,"stone_cz":0.20,
     "stone_ar":0.15,"footprint":0.10,"z_range":0.10,"silhouette":0.10,"symmetry":0.05}
overall = round(sum(sc[k]*W[k] for k in W), 4)
ranking = "EXCELLENT" if overall >= 0.75 else ("GOOD" if overall >= 0.50 else "POOR")

print(json.dumps({
    "shape":          shape,
    "synth_file":     synth_path,
    "real_file":      real_path,
    "object_count": {
        "synth_total": len(synth_fps), "real_total": len(real_fps),
        "total_delta": count_delta,
        "synth_static": len(s_st), "real_static": len(r_st),
        "synth_mutable": len(s_mu), "real_mutable": len(r_mu),
        "mutable_delta": mutable_count_delta,
    },
    "static_integrity": {"match": static_match},
    "geometry_hash_overlap": {
        "mutable_shared":    mu_overlap_n,
        "mutable_overlap_pct": mu_overlap_pct,
        "synth_only_hashes": len(s_mh - r_mh),
        "real_only_hashes":  len(r_mh - s_mh),
    },
    "bbox_similarity":      bbox,
    "synth_mutable_frame":  sf,
    "real_mutable_frame":   rf,
    "stone_placement":      stone,
    "symmetry_deviation":   sym,
    "scoring": {
        "criteria": {k: round(v,4) for k,v in sc.items()},
        "weights":  W,
        "overall":  overall,
    },
    "ranking": ranking,
}))
"""

# ─────────────────────────────────────────────────────────────
#  Embedded worker: validate synthesized output file geometry
# ─────────────────────────────────────────────────────────────

VALIDATE_WORKER_SRC = """\
import sys, json, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import rhino3dm
from jewelry_transform import fingerprint_object, is_contaminant

args          = json.loads(sys.argv[1])
file_path     = args["file"]
static_hashes = set(args["static_hashes"])

model = rhino3dm.File3dm.Read(file_path)
if not model:
    print(json.dumps({"error": "cannot_read"}))
    sys.exit(0)

raw_count        = sum(1 for _ in model.Objects)
contaminant_count = 0
all_fps = []
for obj in model.Objects:
    if is_contaminant(obj, model):
        contaminant_count += 1
        continue
    fp = fingerprint_object(obj)
    if fp is None:
        continue
    all_fps.append(fp)

static_fps  = [f for f in all_fps if f["sig_hash"] in static_hashes]
mutable_fps = [f for f in all_fps if f["sig_hash"] not in static_hashes]

fps = mutable_fps
n   = len(fps)
if n == 0:
    print(json.dumps({
        "error":              "no_mutables",
        "raw_object_count":   raw_count,
        "contaminant_count":  contaminant_count,
        "total_count":        len(all_fps),
        "static_count":       len(static_fps),
        "mutable_count":      0,
    }))
    sys.exit(0)

cxs  = [f["cx"] for f in fps]
cys  = [f["cy"] for f in fps]
czs  = [f["cz"] for f in fps]
sxs  = [f["sx"] for f in fps]
sys_ = [f["sy"] for f in fps]
szs  = [f["sz"] for f in fps]
nsrf = [f.get("n_surfaces") or 0 for f in fps]

zmx = max(czs)

areas   = [sxs[i] * sys_[i]        for i in range(n)]
volumes = [sxs[i] * sys_[i] * szs[i] for i in range(n)]
dists   = [math.sqrt(cxs[i]**2 + cys[i]**2) for i in range(n)]
dr      = [szs[i] / max(max(sxs[i], sys_[i]), 0.001) for i in range(n)]

def _rank(vals, ascending=True):
    order = sorted(range(len(vals)), key=lambda i: vals[i], reverse=not ascending)
    return {idx: r for r, idx in enumerate(order)}

r_xy  = _rank(areas,   ascending=False)
r_vol = _rank(volumes, ascending=False)
r_nsf = _rank(nsrf,    ascending=False)
r_cz  = _rank(czs,     ascending=False)

scores = []
for i in range(n):
    cb  = -n * 0.10 * math.exp(-(dists[i]**2) / (2 * 1.5**2))
    fpp = n * 0.10 if dr[i] < 0.15 else 0.0
    scores.append(0.25*r_xy[i] + 0.25*r_vol[i] + 0.15*r_nsf[i] + 0.25*r_cz[i] + cb + fpp)

st_idx   = scores.index(min(scores))
stone_sx = sxs[st_idx]
stone_sy = sys_[st_idx]
stone_ar = max(stone_sx, stone_sy) / max(min(stone_sx, stone_sy), 0.001)

prong_est = sum(
    1 for i in range(n)
    if czs[i] > zmx * 0.85 and max(sxs[i], sys_[i]) < stone_sx * 0.6
)
melee_est = sum(
    1 for i in range(n)
    if i != st_idx
    and abs(czs[i] - czs[st_idx]) < 2.0
    and max(sxs[i], sys_[i]) < stone_sx * 0.60
    and czs[i] > 3.0
)
strata_count = len(set(int(cz // 1.0) for cz in czs))
basket_depth = round(czs[st_idx] - min(czs), 4)

print(json.dumps({
    "raw_object_count":  raw_count,
    "contaminant_count": contaminant_count,
    "total_count":       len(all_fps),
    "static_count":      len(static_fps),
    "mutable_count":     n,
    "count":             n,
    "cz_mean":         round(sum(czs)/n, 4),
    "footprint_xy":    round(max(max(sxs), max(sys_)), 4),
    "z_top":           round(zmx, 4),
    "z_bottom":        round(min(czs), 4),
    "z_range":         round(zmx - min(czs), 4),
    "stone_sx":        round(stone_sx, 4),
    "stone_sy":        round(stone_sy, 4),
    "stone_cz":        round(czs[st_idx], 4),
    "stone_cx":        round(cxs[st_idx], 4),
    "stone_cy":        round(cys[st_idx], 4),
    "stone_ar":        round(stone_ar, 4),
    "prong_count_est": prong_est,
    "melee_count_est": melee_est,
    "strata_count":    strata_count,
    "basket_depth":    basket_depth,
}))
"""


def compare_vs_real(
    family: str,
    real_dir: Path,
    synth_dir: Path,
    out_path: Path,
    strategy_path: Path | None,
) -> None:
    t0 = time.time()
    print("=" * 60)
    print("  Real vs Generated Comparison")
    print("=" * 60)

    libs = load_all_libraries()
    if family not in libs:
        print(f"ERROR: '{family}' not in libraries.")
        sys.exit(1)

    fam_cls = libs[family]["cls"]
    static_hashes = list(
        set(fam_cls.get("exact_static_hashes", [])) |
        set(fam_cls.get("fuzzy_static_hashes",  []))
    )

    strategy = _load_strategy(strategy_path)

    # Map shape → real file by scanning real_dir for known shape codes
    SHAPE_PATTERNS = {
        "OV": ["OV"], "PE": ["PE"], "PR": ["PR"],
        "RA": ["RA"], "RD": ["RD"], "AS": ["AS"],
        "MQ": ["MQ"], "CU": ["CU"], "ELCU": ["ELCU"], "EM": ["EM"],
    }
    real_files: dict[str, Path] = {}
    for p in sorted(real_dir.glob("*.3dm")):
        stem = p.stem.upper()
        for shape, tokens in SHAPE_PATTERNS.items():
            if any(f"_{t}" in stem or stem.endswith(t) for t in tokens):
                if shape not in real_files:
                    real_files[shape] = p

    # Map shape → synth file via strategy routing
    synth_files: dict[str, Path] = {}
    for shape in list(real_files.keys()):
        archs = _archs_for_shape(shape, strategy, "B")
        if not archs:
            continue
        arch = archs[-1]  # canonical = last in routing list
        candidate = synth_dir / f"{family}_{shape}_arch{arch}.3dm"
        if candidate.exists():
            synth_files[shape] = candidate

    shapes_to_compare = sorted(set(real_files) & set(synth_files))
    if not shapes_to_compare:
        print("ERROR: No matching (real, synth) file pairs found.")
        sys.exit(1)

    print(f"  Family    : {family}")
    print(f"  Real dir  : {real_dir}")
    print(f"  Synth dir : {synth_dir}")
    print(f"  Shapes    : {shapes_to_compare}")
    print()

    # Run comparison worker per shape pair (parallel)
    COMPARE_WORKER_FILE.write_text(COMPARE_WORKER_SRC, encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def _run_compare(args: dict) -> dict | None:
        try:
            r = subprocess.run(
                [sys.executable, str(COMPARE_WORKER_FILE), json.dumps(args)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, env=env,
            )
            if not r.stdout.strip():
                return None
            d = json.loads(r.stdout.strip())
            return None if "error" in d else d
        except Exception:
            return None

    raw: dict[str, dict | None] = {}
    with __import__("concurrent.futures", fromlist=["ThreadPoolExecutor", "as_completed"]).ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_run_compare, {
                "synth_path":    str(synth_files[s]),
                "real_path":     str(real_files[s]),
                "static_hashes": static_hashes,
                "shape":         s,
            }): s
            for s in shapes_to_compare
        }
        for fut in __import__("concurrent.futures", fromlist=["as_completed"]).as_completed(futures):
            s = futures[fut]
            try:
                raw[s] = fut.result()
            except Exception:
                raw[s] = None

    COMPARE_WORKER_FILE.unlink(missing_ok=True)

    # Print per-shape summary
    ranking_order = {"EXCELLENT": 0, "GOOD": 1, "POOR": 2}
    shape_results = []
    for s in shapes_to_compare:
        res = raw.get(s)
        if not res:
            print(f"  {s}: LOAD ERROR")
            continue
        r = res["ranking"]
        oc = res["object_count"]
        st = res["stone_placement"]
        sc = res["scoring"]
        bbox = res["bbox_similarity"]
        ho = res["geometry_hash_overlap"]
        print(f"  {s}  [{r}]  overall={sc['overall']:.3f}")
        print(f"     objects: synth={oc['synth_mutable']} real={oc['real_mutable']}"
              f" delta={oc['mutable_delta']}  static_ok={res['static_integrity']['match']}")
        if st:
            print(f"     stone:  cz_delta={st['cz_delta']:+.3f}mm"
                  f"  ar_delta={st['ar_delta']:+.4f}"
                  f"  fp_delta={st['footprint_delta']:+.3f}mm")
        if bbox:
            print(f"     bbox:   fp_ratio={bbox['footprint_ratio']:.3f}"
                  f"  z_range_ratio={bbox['z_range_ratio']:.3f}"
                  f"  sil_ratio={bbox['silhouette_ratio']:.3f}")
        print(f"     hashes: mutable_overlap={ho['mutable_overlap_pct']}%"
              f"  ({ho['mutable_shared']} shared)")
        print()
        shape_results.append((s, r, sc["overall"]))

    shape_results.sort(key=lambda x: (ranking_order.get(x[1], 9), -x[2]))

    report = {
        "family":           family,
        "analysis_date":    datetime.now().isoformat(timespec="seconds"),
        "real_dir":         str(real_dir),
        "synth_dir":        str(synth_dir),
        "shapes_compared":  shapes_to_compare,
        "metric_weights": {
            "static_integrity": 0.15, "count_similarity": 0.15,
            "stone_cz": 0.20,         "stone_ar":         0.15,
            "footprint": 0.10,        "z_range":          0.10,
            "silhouette": 0.10,       "symmetry":         0.05,
        },
        "ranking_thresholds": {"EXCELLENT": 0.75, "GOOD": 0.50, "POOR": "<0.50"},
        "shape_results":    {s: raw[s] for s in shapes_to_compare if raw.get(s)},
        "summary": {
            "by_ranking": {
                r: [s for s, rank, _ in shape_results if rank == r]
                for r in ["EXCELLENT", "GOOD", "POOR"]
            },
            "ranked_list": [
                {"shape": s, "ranking": r, "overall_score": round(sc, 4)}
                for s, r, sc in shape_results
            ],
        },
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("=" * 60)
    print(f"  Final Rankings")
    print("=" * 60)
    for s, r, sc in shape_results:
        print(f"  {r:<10}  {s}  (score={sc:.3f})")
    print(f"\n  Report -> {out_path}  ({round(time.time()-t0,1)}s)")


# ─────────────────────────────────────────────────────────────
#  Acceptance report writer
# ─────────────────────────────────────────────────────────────

def write_acceptance_report(
    family:       str,
    plan_path:    Path,
    synth_dir:    Path,
    compare_path: Path | None,
    out_path:     Path,
) -> None:
    """
    Produce {family}_acceptance_report.json by running production_decision()
    for every synthesized shape, merging synthesis metadata with compare-vs-real results.
    """
    t0 = time.time()
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    known_shapes   = plan.get("known_shapes", [])
    missing_shapes = plan.get("missing_shapes", [])

    # Load compare-vs-real shape results if available
    compare_results: dict = {}
    if compare_path and compare_path.exists():
        try:
            cr = json.loads(compare_path.read_text(encoding="utf-8"))
            compare_results = cr.get("shape_results", {})
        except Exception:
            pass

    # Find best arch file per shape in synth_dir
    def _best_meta(shape: str) -> dict | None:
        for arch in ("B", "A"):
            p = synth_dir / f"{family}_{shape}_arch{arch}_meta.json"
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return None

    shape_decisions: dict = {}
    for shape in missing_shapes:
        meta         = _best_meta(shape)
        shape_result = compare_results.get(shape)

        if meta is None:
            shape_decisions[shape] = {
                "decision":   "REVIEW_NEEDED",
                "reasons":    ["no synthesis output found"],
                "score":      None,
                "ranking":    None,
                "confidence": "UNKNOWN",
                "filter_rate": None,
                "donor":      None,
                "filters_applied": {},
            }
            continue

        dec = production_decision(meta, shape_result)
        shape_decisions[shape] = {
            **dec,
            "donor":           meta.get("donor_family"),
            "architecture":    meta.get("architecture"),
            "filters_applied": {
                "z_filter":    meta.get("affine_params"),   # z_filter stored in synthesis run
                "min_z_guard": {shape: 0.0}
                               if (meta.get("result", {}).get("mutable_filtered", 0) > 0
                                   and meta.get("result", {}).get("mutable_filtered", 0) !=
                                       (meta.get("result", {}).get("mutable_added", 0) +
                                        meta.get("result", {}).get("mutable_filtered", 0)) * 0)
                               else {},
            },
        }

    summary: dict[str, list] = {"AUTO_ACCEPT": [], "REVIEW_NEEDED": [], "REJECT": []}
    for shape, dec in shape_decisions.items():
        summary[dec["decision"]].append(shape)

    report = {
        "family":          family,
        "run_date":        datetime.now().isoformat(timespec="seconds"),
        "known_shapes":    known_shapes,
        "missing_shapes":  missing_shapes,
        "shape_decisions": shape_decisions,
        "summary":         summary,
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"  Acceptance Report — {family}")
    print("=" * 60)
    col_w = 8
    print(f"  {'Shape':<6}  {'Decision':<14}  {'Score':>5}  {'Rank':<10}  Reasons")
    print("  " + "-" * 70)
    for shape in missing_shapes:
        d = shape_decisions.get(shape, {})
        dec_str  = d.get("decision", "?")
        sc       = d.get("score")
        sc_str   = f"{sc:.3f}" if sc is not None else "  N/A"
        rank_str = (d.get("ranking") or "N/A")[:10]
        rsn      = "; ".join(d.get("reasons", []))[:60]
        print(f"  {shape:<6}  {dec_str:<14}  {sc_str:>5}  {rank_str:<10}  {rsn}")
    print("=" * 60)
    print(f"  AUTO_ACCEPT   : {summary['AUTO_ACCEPT']}")
    print(f"  REVIEW_NEEDED : {summary['REVIEW_NEEDED']}")
    print(f"  REJECT        : {summary['REJECT']}")
    print(f"  Report -> {out_path}  ({round(time.time()-t0,1)}s)")


# ─────────────────────────────────────────────────────────────
#  Phase 5: post-synthesis validator
# ─────────────────────────────────────────────────────────────

def _vcheck(val: float, lo_pass: float, hi_pass: float,
            lo_warn: float, hi_warn: float) -> str:
    """Return PASS / WARN / FAIL for a ratio or delta."""
    if lo_pass <= val <= hi_pass:
        return "PASS"
    if lo_warn <= val <= hi_warn:
        return "WARN"
    return "FAIL"


def validate_synthesis(family: str, synth_dir: Path, out_path: Path) -> None:
    t0 = time.time()
    print("=" * 72)
    print(f"  Post-Synthesis Validator  --  {family}")
    print("=" * 72)

    libs = load_all_libraries()
    if family not in libs:
        print(f"ERROR: '{family}' not in libraries.")
        sys.exit(1)

    # Static hashes for this family
    fam_cls = libs[family]["cls"]
    static_hashes = list(
        set(fam_cls.get("exact_static_hashes", [])) |
        set(fam_cls.get("fuzzy_static_hashes",  []))
    )

    cache = load_cache()
    hm_known = libs[family]["shapes"]
    hm_frames = [cache[f"{family}|{s}"] for s in hm_known if f"{family}|{s}" in cache]
    if not hm_frames:
        print(f"ERROR: No frame cache entries for {family}. Run build-frame-db first.")
        sys.exit(1)

    # Expected metrics — median across known shapes for stone_cz (outlier-robust),
    # mean for basket_depth and count (less prone to single-shape anomalies).
    def _mean(key: str) -> float:
        vals = [f[key] for f in hm_frames if f.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0
    def _median(key: str) -> float:
        vs = sorted(f[key] for f in hm_frames if f.get(key) is not None)
        if not vs: return 0.0
        mid = len(vs) // 2
        return vs[mid] if len(vs) % 2 else (vs[mid - 1] + vs[mid]) / 2

    exp_stone_cz     = _median("stone_cz") or _median("cz_mean") or _mean("stone_cz")
    exp_basket_depth = _mean("basket_depth")
    exp_count        = _mean("count")

    meta_files = sorted(synth_dir.glob(f"{family}_*_meta.json"))
    if not meta_files:
        print(f"  No synthesis metadata files found in: {synth_dir}")
        return

    print(f"  Known shapes    : {hm_known}")
    print(f"  Expected stone_cz: {exp_stone_cz:.3f}mm  basket: {exp_basket_depth:.3f}mm  count: {exp_count:.1f}")
    print(f"  Validating {len(meta_files)} output(s) in {synth_dir}\n")

    VALIDATE_WORKER_FILE.write_text(VALIDATE_WORKER_SRC, encoding="utf-8")

    all_results = []
    for meta_file in meta_files:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        shape    = meta.get("target_shape", "?")
        arch     = meta.get("architecture", "?")
        donor    = meta.get("donor_family", "?")
        d_score  = meta.get("donor_score", 0.0)
        out_file = Path(meta.get("output_file", ""))

        label = f"{family}_{shape}_arch{arch}"

        if not out_file.exists():
            print(f"  [{label}]  ERROR: output file not found: {out_file}")
            all_results.append({"label": label, "shape": shape, "arch": arch,
                                 "donor": donor, "donor_score": d_score,
                                 "verdict": "ERROR", "error": "file_not_found", "checks": {}})
            continue

        # Mutable loss from metadata (no file parse needed)
        res        = meta.get("result", {})
        m_added    = res.get("mutable_added",    0)
        m_filtered = res.get("mutable_filtered", 0)
        m_total    = m_added + m_filtered
        loss_pct   = round(m_filtered / max(m_total, 1) * 100, 1)

        # Affine params
        aff = meta.get("affine_params") or {}
        sxy = aff.get("scale_xy",     1.0)
        sz  = aff.get("scale_z",      1.0)
        dz  = aff.get("translate_z",  0.0)

        # Run VALIDATE_WORKER_SRC
        worker_args = {"file": str(out_file), "static_hashes": static_hashes}
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        frame = None
        try:
            r = subprocess.run(
                [sys.executable, str(VALIDATE_WORKER_FILE), json.dumps(worker_args)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180, env=env,
            )
            if r.stdout.strip():
                frame = json.loads(r.stdout.strip())
                if frame.get("error"):
                    frame = None
        except Exception:
            pass

        # Delegate to shared helper (same 8 checks as the auto-fallback path)
        vr = _validate_output_file(
            out_file         = out_file,
            arch             = arch,
            affine_params    = meta.get("affine_params"),
            result_meta      = meta.get("result", {}),
            static_hashes    = static_hashes,
            exp_stone_cz     = exp_stone_cz,
            exp_basket_depth = exp_basket_depth,
            exp_count        = exp_count,
            shape            = shape,
            elevated_mode    = (exp_basket_depth > 0 and
                                exp_basket_depth < ELEVATED_BASKET_THRESHOLD),
        )
        if vr["verdict"] == "ERROR":
            print(f"  [{label}]  ERROR: frame extraction failed")
            all_results.append({"label": label, "shape": shape, "arch": arch,
                                 "donor": donor, "donor_score": d_score,
                                 "verdict": "ERROR", "error": "frame_extract_failed", "checks": {}})
            continue

        checks    = vr["checks"]
        overall   = vr["verdict"]
        n_fail    = vr["n_fail"]
        n_warn    = vr["n_warn"]
        frame     = vr["frame"]

        all_results.append({
            "label":       label,
            "shape":       shape,
            "arch":        arch,
            "donor":       donor,
            "donor_score": d_score,
            "verdict":     overall,
            "n_warn":      n_warn,
            "n_fail":      n_fail,
            "frame":       frame,
            "checks":      checks,
        })

        # Console line per shape
        v_sym    = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "ERROR": "ERR "}
        cz_str   = f"{checks['stone_cz']['delta']:+.2f}mm"
        ar_str   = f"{checks['stone_ar']['actual']:.2f}[{checks['stone_ar']['verdict'][0]}]"
        out_count = frame.get("mutable_count", 0) if frame else 0
        ct_str   = f"{out_count}/{exp_count:.0f}[{checks['object_count']['verdict'][0]}]"
        basket   = frame.get("basket_depth", 0.0) if frame else 0.0
        bd_str   = f"{basket:.2f}[{checks['basket_depth']['verdict'][0]}]"
        dist     = frame and math.sqrt(frame.get("stone_cx",0)**2 + frame.get("stone_cy",0)**2)
        dc_str   = f"{(dist or 0):.2f}[{checks['stone_centered']['verdict'][0]}]"
        loss_pct = checks["mutable_loss"]["loss_pct"]
        ls_str   = f"{loss_pct:.0f}%[{checks['mutable_loss']['verdict'][0]}]"
        cont_rate = checks["contaminant_rate"]["rate_pct"]
        cont_str = f"cont:{cont_rate:.0f}%[{checks['contaminant_rate']['verdict'][0]}]"
        aff      = meta.get("affine_params") or {}
        sxy      = aff.get("scale_xy", 1.0)
        aff_str  = (f"sXY={sxy:.3f}[{checks['affine_sanity']['verdict'][0]}]"
                    if "affine_sanity" in checks else "archA")
        print(f"  {shape:<5} arch{arch}  {donor:<20} {d_score:.3f}  "
              f"CZ:{cz_str}  AR:{ar_str}  N:{ct_str}  BD:{bd_str}  "
              f"XY:{dc_str}  loss:{ls_str}  {cont_str}  {aff_str}  -> {v_sym[overall]}")

    VALIDATE_WORKER_FILE.unlink(missing_ok=True)

    n_pass  = sum(1 for r in all_results if r["verdict"] == "PASS")
    n_warn  = sum(1 for r in all_results if r["verdict"] == "WARN")
    n_fail  = sum(1 for r in all_results if r["verdict"] == "FAIL")
    n_err   = sum(1 for r in all_results if r["verdict"] == "ERROR")
    n_total = len(all_results)

    print()
    print("=" * 72)
    print(f"  PASS: {n_pass}/{n_total}   WARN: {n_warn}/{n_total}   "
          f"FAIL: {n_fail}/{n_total}   ERROR: {n_err}/{n_total}")
    print("=" * 72)

    report = {
        "family":           family,
        "validation_date":  datetime.now().isoformat(timespec="seconds"),
        "synth_dir":        str(synth_dir),
        "expected_metrics": {
            "stone_cz":     round(exp_stone_cz,     3),
            "basket_depth": round(exp_basket_depth, 3),
            "count":        round(exp_count,         1),
        },
        "thresholds": {
            "stone_cz_delta_pass": 1.5,  "stone_cz_delta_warn": 3.0,
            "count_ratio_pass":    [0.75, 1.40], "count_ratio_warn": [0.55, 1.65],
            "basket_ratio_pass":   [0.75, 1.45], "basket_ratio_warn": [0.55, 1.80],
            "stone_dist_pass": 1.5, "stone_dist_warn": 3.5,
            "loss_pct_pass":   10,  "loss_pct_warn":   25,
            "affine_sxy_pass": [0.80, 1.25], "affine_sxy_warn": [0.65, 1.40],
            "affine_dz_pass":  3.5, "affine_dz_warn":  7.0,
        },
        "summary": {"PASS": n_pass, "WARN": n_warn, "FAIL": n_fail, "ERROR": n_err, "total": n_total},
        "results": all_results,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  Report -> {out_path}  ({round(time.time()-t0, 1)}s)")


# ─────────────────────────────────────────────────────────────
#  Phase 6: blacklist management commands
# ─────────────────────────────────────────────────────────────

def show_blacklist(family: str | None = None) -> None:
    bl = _load_blacklist()
    entries = [(k, v) for k, v in bl.items()
               if family is None or k.startswith(f"{family}|")]
    if not entries:
        msg = f"  No blacklist entries" + (f" for {family}" if family else "") + "."
        print(msg)
        return

    print(f"  {'Key':<40}  {'Strikes':>7}  {'Status':<22}  Last strike")
    print("  " + "-" * 90)
    for key, entry in sorted(entries):
        strikes    = entry.get("strikes", 0)
        status     = entry.get("status", "?")
        last       = entry.get("last_strike", "?")[:19]
        failed     = entry["history"][-1].get("checks_failed", []) if entry.get("history") else []
        print(f"  {key:<40}  {strikes:>7}  {status:<22}  {last}")
        if failed:
            print(f"    last failed checks: {failed}")
    print()
    by_status: dict[str, int] = {}
    for _, v in entries:
        s = v.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
    for s in ("warning", "temporary_blacklist", "permanent_blacklist"):
        if by_status.get(s):
            print(f"  {s}: {by_status[s]}")
    print(f"  Total: {len(entries)}")


def clear_blacklist(
    family: str | None = None,
    shape:  str | None = None,
    donor:  str | None = None,
) -> None:
    bl = _load_blacklist()
    before     = len(bl)
    to_remove  = []
    for key in list(bl.keys()):
        parts = key.split("|", 2)
        if len(parts) != 3:
            continue
        k_fam, k_shp, k_don = parts
        if family and k_fam != family:
            continue
        if shape  and k_shp != shape:
            continue
        if donor  and k_don != donor:
            continue
        to_remove.append(key)
    for key in to_remove:
        del bl[key]
    _save_blacklist(bl)
    print(f"  Cleared {len(to_remove)}/{before} blacklist entries.")
    if to_remove:
        for key in to_remove:
            print(f"    removed: {key}")


# ─────────────────────────────────────────────────────────────
#  batch-synthesize helpers + orchestrator
# ─────────────────────────────────────────────────────────────

def _collect_verdicts(family: str, shapes: list, out_dir: Path, fam_result: dict) -> None:
    """Scan synthesis_output for meta files and accumulate verdict counts."""
    for shape in shapes:
        for arch in ("B", "A"):          # prefer archB if both exist
            meta_p = out_dir / f"{family}_{shape}_arch{arch}_meta.json"
            if meta_p.exists():
                try:
                    meta    = json.loads(meta_p.read_text(encoding="utf-8"))
                    verdict = meta.get("validation_verdict", "UNKNOWN")
                except Exception:
                    verdict = "UNKNOWN"
                fam_result["shapes"][shape] = {"arch": arch, "verdict": verdict}
                if verdict == "PASS":
                    fam_result["n_pass"] += 1
                elif verdict == "WARN":
                    fam_result["n_warn"] += 1
                elif verdict in ("FAIL", "ERROR"):
                    fam_result["n_fail"] += 1
                break


def batch_synthesize(
    families:              list | None,
    out_dir:               Path,
    plan_dir:              Path,
    strategy_path:         Path | None,
    auto_fallback:         bool,
    max_fallback_attempts: int,
    skip_existing:         bool,
    retry_warn:            bool,
    dry_run:               bool,
    report_path:           Path,
) -> None:
    """
    Analyze + synthesize all (or selected) families in shape_library/.
    Resume-safe: shapes with existing meta files are skipped by default.
    """
    t0 = time.time()

    # Ensure frame cache exists before the first analyze call
    if not dry_run and not CACHE_FILE.exists():
        print("[batch] Frame cache missing -- building ...")
        build_frame_db(rebuild=False)

    # Discover families
    if families is None:
        families = sorted(
            d.name for d in LIBRARY_DIR.iterdir()
            if d.is_dir() and (d / "shape_index.json").exists()
        )

    # Synthesizable shapes = shapes with non-empty routing in strategy
    strategy_data = _load_strategy(strategy_path)
    shape_routing = strategy_data.get("shape_routing", {}) if strategy_data else {}
    synth_shapes  = [s for s in ALL_SHAPES if shape_routing.get(s)]

    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    n_total = len(families)
    print(f"\n[batch-synthesize] {n_total} families  synth_shapes={synth_shapes}")
    if dry_run:
        print("  DRY RUN -- no files will be written\n")

    for fam_idx_ctr, family in enumerate(families, 1):
        fam_result = {
            "family": family,
            "status": "PENDING",
            "shapes": {},
            "n_pass": 0,
            "n_warn": 0,
            "n_fail": 0,
            "error":  None,
        }
        print(f"\n[{fam_idx_ctr}/{n_total}] {family}")

        try:
            idx_path = LIBRARY_DIR / family / "shape_index.json"
            if not idx_path.exists():
                print(f"  SKIP -- no shape_index.json")
                fam_result["status"] = "NO_LIBRARY"
                results.append(fam_result)
                continue

            known_shapes = list(json.loads(idx_path.read_text(encoding="utf-8")).keys())
            missing      = [s for s in synth_shapes if s not in known_shapes]

            if not missing:
                print(f"  SKIP -- all synth shapes present ({known_shapes})")
                fam_result["status"] = "NO_MISSING"
                results.append(fam_result)
                continue

            # Filter shapes that already have meta files
            if skip_existing:
                pending = []
                for s in missing:
                    meta_files = [out_dir / f"{family}_{s}_arch{a}_meta.json"
                                  for a in ("B", "A")]
                    existing = [m for m in meta_files if m.exists()]
                    if not existing:
                        pending.append(s)
                    elif retry_warn:
                        for mp in existing:   # archB preferred (listed first)
                            v = json.loads(mp.read_text(encoding="utf-8")).get(
                                "validation_verdict")
                            if v == "WARN":
                                pending.append(s)
                            break
            else:
                pending = list(missing)

            if not pending:
                print(f"  COMPLETE -- all shapes already synthesized")
                _collect_verdicts(family, missing, out_dir, fam_result)
                fam_result["status"] = "COMPLETE"
                results.append(fam_result)
                continue

            print(f"  known={known_shapes}  missing={missing}  pending={pending}")
            plan_path = plan_dir / f"{family.lower().replace(' ', '_')}_transfer_plan.json"

            if dry_run:
                print(f"  [dry] analyze -> {plan_path.name}")
                print(f"  [dry] synthesize: {pending}")
                fam_result["status"] = "DRY_RUN"
                results.append(fam_result)
                continue

            # Analyze only if plan is absent
            if not plan_path.exists():
                print(f"  Analyzing {family} ...")
                analyze(family, plan_path, forced_bridge=None, rebuild_cache=False)

            # Synthesize pending shapes only
            print(f"  Synthesizing {pending} ...")
            synthesize(
                family                = family,
                plan_path             = plan_path,
                out_dir               = out_dir,
                default_arch          = "B",
                target_shapes         = pending,
                strategy_path         = strategy_path,
                donor_rank            = 1,
                z_filter              = None,
                min_z_guards          = None,
                auto_fallback         = auto_fallback,
                max_fallback_attempts = max_fallback_attempts,
            )

            _collect_verdicts(family, missing, out_dir, fam_result)
            fam_result["status"] = "DONE"

        except SystemExit as exc:
            fam_result["status"] = "ERROR"
            fam_result["error"]  = f"SystemExit({exc.code})"
            print(f"  ERROR: SystemExit({exc.code})")
        except Exception as exc:
            fam_result["status"] = "ERROR"
            fam_result["error"]  = str(exc)
            print(f"  ERROR: {exc}")

        results.append(fam_result)

    n_done     = sum(1 for r in results if r["status"] == "DONE")
    n_complete = sum(1 for r in results if r["status"] == "COMPLETE")
    n_error    = sum(1 for r in results if r["status"] == "ERROR")
    elapsed    = round(time.time() - t0, 1)

    report = {
        "batch_date":     datetime.now().isoformat(timespec="seconds"),
        "total_families": n_total,
        "done":           n_done,
        "complete":       n_complete,
        "no_missing":     sum(1 for r in results if r["status"] == "NO_MISSING"),
        "no_library":     sum(1 for r in results if r["status"] == "NO_LIBRARY"),
        "error":          n_error,
        "results":        results,
        "elapsed_s":      elapsed,
    }
    if not dry_run:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n[batch-synthesize] complete in {elapsed}s"
          f"  done={n_done}  complete={n_complete}  error={n_error}")
    if not dry_run:
        print(f"  report: {report_path}")


# ─────────────────────────────────────────────────────────────
#  auto-onboard orchestrator
# ─────────────────────────────────────────────────────────────

def auto_onboard(
    family:      str,
    plan_path:   Path,
    out_dir:     Path,
    real_dir:    Path | None,
    strategy:    Path | None,
    dry_run:     bool,
    no_compare:  bool,
) -> None:
    """
    Single-command onboarding: analyze → synthesize → compare → accept/reject.

    Reads recommended_filters and auto_arch from the transfer plan (populated
    by analyze()) and applies them automatically in the synthesis step.
    """
    t0 = time.time()
    compare_path = Path(f"{family.lower().replace(' ', '_')}_real_vs_generated.json")
    report_path  = Path(f"{family.lower().replace(' ', '_')}_acceptance_report.json")

    print("=" * 60)
    print(f"  auto-onboard : {family}  {'[DRY RUN]' if dry_run else ''}")
    print("=" * 60)

    # ── Step 1: ensure frame cache is populated ───────────────
    print("\n[1/5] Frame cache check ...")
    if not CACHE_FILE.exists():
        print("  Cache missing — building ...")
        if not dry_run:
            build_frame_db(rebuild=False)
    else:
        print(f"  Cache exists ({CACHE_FILE})")

    # ── Step 2: analyze ────────────────────────────────────────
    print(f"\n[2/5] Analyze {family} ...")
    if not dry_run:
        analyze(family, plan_path, forced_bridge=None, rebuild_cache=False)
    if not plan_path.exists():
        print("  ERROR: transfer plan not found after analyze step.")
        return

    # ── Step 3: read plan → extract recommended filters + arch ─
    print("\n[3/5] Extracting filter and arch recommendations from plan ...")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    missing_shapes = plan.get("missing_shapes", [])

    # Collect auto min_z_guards, z_offset_corrections, and per-shape arch from rank-1 candidate
    auto_min_z_guards: dict[str, float] = {}
    auto_z_offsets:    dict[str, float] = {}
    shape_arch_map:    dict[str, list]  = {}
    for sp in plan.get("missing_shape_plans", []):
        shape = sp["missing_shape"]
        cands = sp.get("top_candidates", [])
        if not cands:
            continue
        best = cands[0]
        rec_flt  = best.get("recommended_filters", {})
        auto_arc = best.get("auto_arch", ["B"])
        auto_min_z_guards.update(rec_flt.get("min_z_guard", {}))
        if rec_flt.get("z_offset_correction") is not None:
            auto_z_offsets[shape] = rec_flt["z_offset_correction"]
        shape_arch_map[shape] = auto_arc

    if auto_min_z_guards:
        print(f"  min_z guards recommended: {auto_min_z_guards}")
    else:
        print("  No min_z guards needed.")
    if auto_z_offsets:
        print(f"  z_offset corrections    : {auto_z_offsets}")

    # Determine which architectures to run per shape (strategy file overrides)
    strategy_data = _load_strategy(strategy)
    for shape in missing_shapes:
        if strategy_data and shape in strategy_data.get("shape_routing", {}):
            shape_arch_map[shape] = strategy_data["shape_routing"][shape] or ["B"]

    # ── Step 4: synthesize ─────────────────────────────────────
    print(f"\n[4/5] Synthesize missing shapes: {missing_shapes} ...")
    if not dry_run:
        synthesize(
            family      = family,
            plan_path   = plan_path,
            out_dir     = out_dir,
            default_arch= "B",
            target_shapes = missing_shapes,
            strategy_path = strategy,
            donor_rank  = 1,
            z_filter    = None,
            min_z_guards= auto_min_z_guards or None,
            z_offsets   = auto_z_offsets or None,
        )
    else:
        for shape in missing_shapes:
            archs = shape_arch_map.get(shape, ["B"])
            guard = auto_min_z_guards.get(shape)
            guard_str = f"  min_z_guard={guard}" if guard is not None else ""
            print(f"  [dry] {shape}: arch={'+'.join(archs)}{guard_str}")

    # ── Step 5: compare vs real (optional) ────────────────────
    if real_dir and not no_compare:
        print(f"\n[5a/5] Compare vs real ({real_dir}) ...")
        if not dry_run:
            compare_vs_real(
                family     = family,
                real_dir   = real_dir,
                synth_dir  = out_dir,
                out_path   = compare_path,
                strategy_path = strategy,
            )
    else:
        print("\n[5a/5] Skipping compare-vs-real (no --real-dir or --no-compare).")
        compare_path = None

    # ── Step 6: produce acceptance report ─────────────────────
    print(f"\n[5b/5] Generating acceptance report ...")
    if not dry_run:
        write_acceptance_report(
            family       = family,
            plan_path    = plan_path,
            synth_dir    = out_dir,
            compare_path = compare_path,
            out_path     = report_path,
        )
    else:
        print(f"  [dry] Would write {report_path}")

    print(f"\n  auto-onboard complete in {round(time.time()-t0,1)}s")


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "debug-stone-detection":
        out = Path("stone_detection_report.json")
        for arg in sys.argv[2:]:
            if arg.startswith("--out") and "=" in arg:
                out = Path(arg.split("=", 1)[1])
        print("=" * 60)
        print("  Stone Detection Diagnostic")
        print("=" * 60)
        debug_stone_detection(out)

    elif cmd == "analyze":
        if len(sys.argv) < 3:
            print("Usage: python cross_family_transfer.py analyze <family_name>")
            sys.exit(1)
        family  = sys.argv[2]
        out     = Path("transfer_plan.json")
        bridge  = None
        rebuild = False
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--out" and i+1 < len(sys.argv):
                out = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--bridge" and i+1 < len(sys.argv):
                bridge = sys.argv[i+1]; i += 2
            elif sys.argv[i] == "--rebuild-cache":
                rebuild = True; i += 1
            else:
                i += 1
        print("=" * 60)
        print("  Cross-Family Transfer -- Diagnostic")
        print("=" * 60)
        analyze(family, out, bridge, rebuild)

    elif cmd == "compare-vs-real":
        if len(sys.argv) < 3:
            print("Usage: python cross_family_transfer.py compare-vs-real <family> [options]")
            sys.exit(1)
        family   = sys.argv[2]
        real_dir = Path("3dm") / family
        synth_dir = Path("synthesis_output")
        out_path  = Path("high_mira_real_vs_generated.json")
        strategy  = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--real-dir" and i+1 < len(sys.argv):
                real_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--synth-dir" and i+1 < len(sys.argv):
                synth_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--out" and i+1 < len(sys.argv):
                out_path = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--strategy" and i+1 < len(sys.argv):
                strategy = Path(sys.argv[i+1]); i += 2
            else:
                i += 1
        compare_vs_real(family, real_dir, synth_dir, out_path, strategy)

    elif cmd == "build-frame-db":
        rebuild = "--rebuild-cache" in sys.argv
        build_frame_db(rebuild)

    elif cmd == "synthesize":
        if len(sys.argv) < 3:
            print("Usage: python cross_family_transfer.py synthesize <family> [options]")
            sys.exit(1)
        family      = sys.argv[2]
        plan        = Path("transfer_plan.json")
        out_dir     = Path("synthesis_output")
        arch        = "B"
        shapes      = None
        strategy    = None
        donor_rank    = 1
        z_filter      = None  # None = auto from known-shape envelope
        min_z_guards  = {}    # shape → float threshold, e.g. {"PR": 0.0}
        auto_fallback = False
        max_fallback_attempts = 5
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--plan" and i+1 < len(sys.argv):
                plan = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--out-dir" and i+1 < len(sys.argv):
                out_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--arch" and i+1 < len(sys.argv):
                arch = sys.argv[i+1].upper(); i += 2
            elif sys.argv[i] == "--shapes" and i+1 < len(sys.argv):
                shapes = [s.strip().upper() for s in sys.argv[i+1].split(",")]; i += 2
            elif sys.argv[i] == "--strategy" and i+1 < len(sys.argv):
                strategy = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--donor-rank" and i+1 < len(sys.argv):
                donor_rank = int(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--z-filter" and i+1 < len(sys.argv):
                z_filter = float(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--no-z-filter":
                z_filter = -1e9; i += 1  # disable: no object is ever below -1e9
            elif sys.argv[i] == "--min-z-guard" and i+1 < len(sys.argv):
                for pair in sys.argv[i+1].split(","):
                    shape_key, _, thresh = pair.strip().partition(":")
                    if shape_key and thresh:
                        min_z_guards[shape_key.strip().upper()] = float(thresh.strip())
                i += 2
            elif sys.argv[i] == "--auto-fallback":
                auto_fallback = True; i += 1
            elif sys.argv[i] == "--max-attempts" and i+1 < len(sys.argv):
                max_fallback_attempts = int(sys.argv[i+1]); i += 2
            else:
                i += 1
        if arch not in ("A", "B"):
            print("ERROR: --arch must be A or B")
            sys.exit(1)
        synthesize(family, plan, out_dir, arch, shapes, strategy, donor_rank, z_filter,
                   min_z_guards or None, auto_fallback=auto_fallback,
                   max_fallback_attempts=max_fallback_attempts)

    elif cmd == "batch-synthesize":
        out_dir     = Path("synthesis_output")
        plan_dir    = Path(".")
        strategy    = None
        families    = None
        auto_fb     = False
        max_att     = 5
        skip_ex     = True
        retry_warn  = False
        dry_run     = False
        report      = Path("batch_synthesis_report.json")
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--families" and i+1 < len(sys.argv):
                families = [f.strip() for f in sys.argv[i+1].split(",")]; i += 2
            elif sys.argv[i] == "--out-dir" and i+1 < len(sys.argv):
                out_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--plan-dir" and i+1 < len(sys.argv):
                plan_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--strategy" and i+1 < len(sys.argv):
                strategy = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--auto-fallback":
                auto_fb = True; i += 1
            elif sys.argv[i] == "--max-attempts" and i+1 < len(sys.argv):
                max_att = int(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--no-skip":
                skip_ex = False; i += 1
            elif sys.argv[i] == "--retry-warn":
                retry_warn = True; i += 1
            elif sys.argv[i] == "--dry-run":
                dry_run = True; i += 1
            elif sys.argv[i] == "--report" and i+1 < len(sys.argv):
                report = Path(sys.argv[i+1]); i += 2
            else:
                i += 1
        batch_synthesize(families, out_dir, plan_dir, strategy, auto_fb, max_att,
                         skip_ex, retry_warn, dry_run, report)

    elif cmd == "auto-onboard":
        if len(sys.argv) < 3:
            print("Usage: python cross_family_transfer.py auto-onboard <family> [options]")
            sys.exit(1)
        family     = sys.argv[2]
        plan       = Path("transfer_plan.json")
        out_dir    = Path("synthesis_output")
        real_dir   = None
        strategy   = None
        dry_run    = False
        no_compare = False
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--plan" and i+1 < len(sys.argv):
                plan = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--out-dir" and i+1 < len(sys.argv):
                out_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--real-dir" and i+1 < len(sys.argv):
                real_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--strategy" and i+1 < len(sys.argv):
                strategy = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--dry-run":
                dry_run = True; i += 1
            elif sys.argv[i] == "--no-compare":
                no_compare = True; i += 1
            else:
                i += 1
        auto_onboard(family, plan, out_dir, real_dir, strategy, dry_run, no_compare)

    elif cmd == "acceptance-report":
        if len(sys.argv) < 3:
            print("Usage: python cross_family_transfer.py acceptance-report <family> [options]")
            sys.exit(1)
        family       = sys.argv[2]
        plan         = Path("transfer_plan.json")
        out_dir      = Path("synthesis_output")
        compare_path = Path(f"{family.lower().replace(' ', '_')}_real_vs_generated.json")
        out_path     = Path(f"{family.lower().replace(' ', '_')}_acceptance_report.json")
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--plan" and i+1 < len(sys.argv):
                plan = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--out-dir" and i+1 < len(sys.argv):
                out_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--compare" and i+1 < len(sys.argv):
                compare_path = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--out" and i+1 < len(sys.argv):
                out_path = Path(sys.argv[i+1]); i += 2
            else:
                i += 1
        write_acceptance_report(family, plan, out_dir, compare_path, out_path)

    elif cmd == "validate-synthesis":
        if len(sys.argv) < 3:
            print("Usage: python cross_family_transfer.py validate-synthesis <family> [--dir <path>] [--out <report.json>]")
            sys.exit(1)
        family   = sys.argv[2]
        # Default: try synthesis_p4/<family> first, fallback to synthesis_output
        synth_dir = Path("synthesis_p4") / family
        if not synth_dir.exists():
            synth_dir = Path("synthesis_output")
        out_path  = Path(f"{family.lower().replace(' ', '_')}_validation_report.json")
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--dir" and i+1 < len(sys.argv):
                synth_dir = Path(sys.argv[i+1]); i += 2
            elif sys.argv[i] == "--out" and i+1 < len(sys.argv):
                out_path = Path(sys.argv[i+1]); i += 2
            else:
                i += 1
        validate_synthesis(family, synth_dir, out_path)

    elif cmd == "show-blacklist":
        family = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
        show_blacklist(family)

    elif cmd == "clear-blacklist":
        family = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
        shape  = None
        donor  = None
        i = 3 if family else 2
        while i < len(sys.argv):
            if sys.argv[i] == "--shape" and i+1 < len(sys.argv):
                shape = sys.argv[i+1].upper(); i += 2
            elif sys.argv[i] == "--donor" and i+1 < len(sys.argv):
                donor = sys.argv[i+1]; i += 2
            else:
                i += 1
        clear_blacklist(family, shape, donor)

    else:
        print("Usage:")
        print("  python cross_family_transfer.py analyze <family> [--out plan.json] [--bridge EM] [--rebuild-cache]")
        print("  python cross_family_transfer.py build-frame-db [--rebuild-cache]")
        print("  python cross_family_transfer.py synthesize <family> [--plan transfer_plan.json]")
        print("    [--out-dir synthesis_output] [--arch A|B] [--shapes AS,OV,PE]")
        print("    [--strategy architecture_strategy.json] [--donor-rank N]")
        print("    [--auto-fallback] [--max-attempts N]")
        print("  python cross_family_transfer.py batch-synthesize [--families F1,F2,...]")
        print("    [--out-dir synthesis_output] [--plan-dir .] [--strategy architecture_strategy.json]")
        print("    [--auto-fallback] [--max-attempts N] [--no-skip] [--retry-warn] [--dry-run]")
        print("    [--report batch_synthesis_report.json]")
        print("  python cross_family_transfer.py auto-onboard <family>")
        print("    [--plan transfer_plan.json] [--out-dir synthesis_output]")
        print("    [--real-dir 3dm/<family>] [--strategy architecture_strategy.json]")
        print("    [--dry-run] [--no-compare]")
        print("  python cross_family_transfer.py acceptance-report <family>")
        print("    [--plan transfer_plan.json] [--out-dir synthesis_output]")
        print("    [--compare <report.json>] [--out <output.json>]")
        print("  python cross_family_transfer.py validate-synthesis <family>")
        print("    [--dir synthesis_p4/<family>] [--out <report.json>]")
        print("  python cross_family_transfer.py show-blacklist [family]")
        print("  python cross_family_transfer.py clear-blacklist [family] [--shape SHAPE] [--donor DONOR]")
        print("  python cross_family_transfer.py debug-stone-detection [--out=stone_detection_report.json]")
        sys.exit(1)


if __name__ == "__main__":
    main()
