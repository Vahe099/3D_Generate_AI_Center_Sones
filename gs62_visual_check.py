"""
Geometric validation of GS_62 synthesized outputs vs known shapes.
Checks: stone placement, prong height, basket depth, floating geometry,
ring proportions, sub-plane objects.
"""
import json, math, sys
import rhino3dm
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jewelry_transform import fingerprint_object

STATIC_HASHES_FILE = Path("shape_library/GS_62/classification.json")
cls = json.loads(STATIC_HASHES_FILE.read_text(encoding="utf-8"))
STATIC_HASHES = (
    set(cls.get("exact_static_hashes", [])) |
    set(cls.get("fuzzy_static_hashes", []))
)

KNOWN_FILES = {
    "AS": Path("3dm/GS_62/GS_62_AS.3dm"),
    "CU": Path("3dm/GS_62/GS_62_CU.3dm"),
    "EM": Path("3dm/GS_62/GS_62_EM.3dm"),
    "PR": Path("3dm/GS_62/GS_62_PR.3dm"),
}
SYNTH_FILES = {
    "ELCU": Path("synthesis_output/GS_62_ELCU_archA.3dm"),
    "MQ":   Path("synthesis_output/GS_62_MQ_archA.3dm"),
    "OV":   Path("synthesis_output/GS_62_OV_archA.3dm"),
    "PE":   Path("synthesis_output/GS_62_PE_archA.3dm"),
    "RA":   Path("synthesis_output/GS_62_RA_archA.3dm"),
    "RD":   Path("synthesis_output/GS_62_RD_archA.3dm"),
}

def analyze_file(label, fpath, is_synth=False):
    model = rhino3dm.File3dm.Read(str(fpath))
    if not model:
        return {"label": label, "error": "cannot read file"}

    all_objs = []
    for obj in model.Objects:
        fp = fingerprint_object(obj)
        if fp is None:
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        sx = bb.Max.X - bb.Min.X
        sy = bb.Max.Y - bb.Min.Y
        sz = bb.Max.Z - bb.Min.Z
        cx = (bb.Max.X + bb.Min.X) / 2
        cy = (bb.Max.Y + bb.Min.Y) / 2
        cz = (bb.Max.Z + bb.Min.Z) / 2
        footprint = max(sx, sy)
        min_z = cz - sz / 2
        max_z = cz + sz / 2

        is_static = fp["sig_hash"] in STATIC_HASHES
        all_objs.append({
            "type": type(obj.Geometry).__name__,
            "is_static": is_static,
            "cz": round(cz, 3),
            "min_z": round(min_z, 3),
            "max_z": round(max_z, 3),
            "sz": round(sz, 3),
            "footprint": round(footprint, 3),
        })

    static_objs  = [o for o in all_objs if o["is_static"]]
    mutable_objs = [o for o in all_objs if not o["is_static"]]

    # Stone: largest footprint mutable object above Z=5
    stone_candidates = [o for o in mutable_objs if o["cz"] > 5 and o["footprint"] > 3]
    stone = max(stone_candidates, key=lambda o: o["footprint"]) if stone_candidates else None

    # Prongs: mutable objects with cz 6-14mm and footprint 0.5-5mm
    prongs = [o for o in mutable_objs if 5 < o["cz"] < 16 and 0.5 < o["footprint"] < 6
              and (stone is None or o is not stone)]

    # Basket/setting: mutable objects with footprint > 5mm (below stone)
    basket = [o for o in mutable_objs if o["footprint"] > 5
              and (stone is None or abs(o["cz"] - stone["cz"]) < 8)
              and (stone is None or o is not stone)]

    # Sub-plane objects (min_z < 0) — floating geometry
    subplane = [o for o in mutable_objs if o["min_z"] < 0]

    # Z range of all mutable objects
    if mutable_objs:
        z_bottom = min(o["min_z"] for o in mutable_objs)
        z_top    = max(o["max_z"] for o in mutable_objs)
        z_range  = round(z_top - z_bottom, 3)
    else:
        z_bottom = z_top = z_range = None

    # Check: stone above expected GS_62 range (12-15mm)
    stone_ok = (stone is not None and 11 < stone["cz"] < 16) if stone else False

    # Check: any mutable object far above stone (floating high)
    floating_high = [o for o in mutable_objs
                     if stone and o["max_z"] > stone["max_z"] + 3 and o is not stone]

    return {
        "label":         label,
        "is_synth":      is_synth,
        "total_objects": len(all_objs),
        "static_count":  len(static_objs),
        "mutable_count": len(mutable_objs),
        "stone_cz":      round(stone["cz"], 3) if stone else None,
        "stone_fp":      round(stone["footprint"], 3) if stone else None,
        "stone_sz":      round(stone["sz"], 3) if stone else None,
        "stone_ok":      stone_ok,
        "prong_count":   len(prongs),
        "prong_z_range": (round(min(p["cz"] for p in prongs), 2),
                          round(max(p["cz"] for p in prongs), 2)) if prongs else None,
        "basket_count":  len(basket),
        "z_bottom_mm":   round(z_bottom, 3) if z_bottom is not None else None,
        "z_top_mm":      round(z_top, 3) if z_top is not None else None,
        "z_range_mm":    z_range,
        "subplane_count": len(subplane),
        "subplane_objects": [{"cz": o["cz"], "min_z": o["min_z"], "fp": o["footprint"]} for o in subplane],
        "floating_high_count": len(floating_high),
    }

# --- Run analysis ---
results = {}
print("=" * 80)
print("  GS_62 Geometric Validation")
print("=" * 80)

print("\n--- KNOWN shapes (reference baseline) ---")
for label, fpath in KNOWN_FILES.items():
    r = analyze_file(label, fpath, is_synth=False)
    results[label] = r
    flag = "✗ STONE BAD" if not r.get("stone_ok") else ""
    sub  = f"  *** {r['subplane_count']} SUB-PLANE" if r.get("subplane_count") else ""
    print(f"  {label:<6} total={r['total_objects']:>2}  static={r['static_count']}  mutable={r['mutable_count']}"
          f"  stone_cz={r['stone_cz']:>7.3f}  stone_fp={r['stone_fp']:>6.2f}"
          f"  z_bottom={r['z_bottom_mm']:>7.3f}  z_range={r['z_range_mm']:>6.3f}"
          f"  prongs={r['prong_count']}"
          f"  {flag}{sub}")

# Baseline stats from known shapes
known_stone_czs  = [r["stone_cz"] for r in results.values() if r.get("stone_cz")]
known_z_bottoms  = [r["z_bottom_mm"] for r in results.values() if r.get("z_bottom_mm") is not None]
known_z_ranges   = [r["z_range_mm"] for r in results.values() if r.get("z_range_mm") is not None]
ref_stone_cz_min = min(known_stone_czs) if known_stone_czs else 0
ref_stone_cz_max = max(known_stone_czs) if known_stone_czs else 0
ref_z_bottom_min = min(known_z_bottoms) if known_z_bottoms else 0
ref_z_range_min  = min(known_z_ranges)  if known_z_ranges  else 0

print(f"\n  Reference envelope:")
print(f"    stone_cz range : {ref_stone_cz_min:.2f} – {ref_stone_cz_max:.2f} mm")
print(f"    z_bottom min   : {ref_z_bottom_min:.3f} mm")
print(f"    z_range min    : {ref_z_range_min:.3f} mm")

print("\n--- SYNTHESIZED shapes (arch A) ---")
issues = {}
for label, fpath in SYNTH_FILES.items():
    r = analyze_file(label, fpath, is_synth=True)
    results[label] = r

    flags = []
    # Stone placement check
    if r.get("stone_cz") is None:
        flags.append("NO_STONE_DETECTED")
    elif not (ref_stone_cz_min - 2 < r["stone_cz"] < ref_stone_cz_max + 2):
        flags.append(f"STONE_OOB(cz={r['stone_cz']})")

    # Sub-plane objects
    if r.get("subplane_count", 0) > 0:
        flags.append(f"SUBPLANE_x{r['subplane_count']}")

    # Floating high
    if r.get("floating_high_count", 0) > 0:
        flags.append(f"FLOAT_HIGH_x{r['floating_high_count']}")

    # z_range vs reference
    if r.get("z_range_mm") and r["z_range_mm"] < ref_z_range_min * 0.5:
        flags.append(f"Z_RANGE_LOW({r['z_range_mm']}mm)")

    # Too few mutable objects
    if r.get("mutable_count", 0) < 3:
        flags.append("TOO_FEW_MUTABLE")

    flag_str = "  !!! " + ", ".join(flags) if flags else "  OK"
    sub = f"  *** {r['subplane_count']} SUB-PLANE" if r.get("subplane_count") else ""

    stone_cz_str = f"{r['stone_cz']:>7.3f}" if r.get("stone_cz") is not None else "    N/A"
    stone_fp_str = f"{r['stone_fp']:>6.2f}" if r.get("stone_fp") is not None else "   N/A"
    zb_str = f"{r['z_bottom_mm']:>7.3f}" if r.get("z_bottom_mm") is not None else "    N/A"
    zr_str = f"{r['z_range_mm']:>6.3f}" if r.get("z_range_mm") is not None else "   N/A"

    print(f"  {label:<6} total={r['total_objects']:>2}  static={r['static_count']}  mutable={r['mutable_count']}"
          f"  stone_cz={stone_cz_str}  stone_fp={stone_fp_str}"
          f"  z_bottom={zb_str}  z_range={zr_str}"
          f"  prongs={r['prong_count']}"
          f"{flag_str}{sub}")

    if flags:
        issues[label] = flags
        if r.get("subplane_objects"):
            for sp in r["subplane_objects"]:
                print(f"         sub-plane: cz={sp['cz']:.3f}  min_z={sp['min_z']:.3f}  fp={sp['fp']:.2f}")

# Summary
print("\n" + "=" * 80)
print("  SUMMARY")
print("=" * 80)
if not issues:
    print("  All synthesized shapes passed geometric checks.")
else:
    print(f"  {len(issues)} shape(s) have issues:")
    for shape, flags in issues.items():
        print(f"    {shape}: {', '.join(flags)}")

print()
verdict = "GEOMETRY_OK - visual Rhino review still required" if not issues else "GEOMETRY_ISSUES - inspect flagged shapes first"
print(f"  Verdict: {verdict}")
print()
