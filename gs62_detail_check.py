"""
Detailed per-object breakdown of GS_62 synthesized files.
Focus: why prong_count shows 1 vs reference 3-7.
Dumps all mutable objects with their geometry stats.
"""
import json, sys
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

FILES = {
    # Reference
    "PR (known)":   Path("3dm/GS_62/GS_62_PR.3dm"),
    # Synthesized
    "ELCU (synth)": Path("synthesis_output/GS_62_ELCU_archA.3dm"),
    "MQ (synth)":   Path("synthesis_output/GS_62_MQ_archA.3dm"),
    "RD (synth)":   Path("synthesis_output/GS_62_RD_archA.3dm"),
}

def classify_role(cz, sz, fp, stone_cz):
    """Heuristic role classification."""
    min_z = cz - sz / 2
    max_z = cz + sz / 2
    if fp > 8 and abs(cz - stone_cz) < 3:
        return "STONE/BASKET"
    if fp < 5 and 6 < cz < 15:
        return "PRONG"
    if fp > 15:
        return "SHANK/LARGE"
    if min_z < 1 and sz > 5:
        return "SHANK"
    return "OTHER"

for label, fpath in FILES.items():
    model = rhino3dm.File3dm.Read(str(fpath))
    if not model:
        print(f"\n{label}: ERROR reading file")
        continue

    mutable = []
    static_count = 0
    for obj in model.Objects:
        fp_data = fingerprint_object(obj)
        if fp_data is None:
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        sx = bb.Max.X - bb.Min.X
        sy = bb.Max.Y - bb.Min.Y
        sz = bb.Max.Z - bb.Min.Z
        cz = (bb.Max.Z + bb.Min.Z) / 2
        fp = max(sx, sy)
        min_z = cz - sz / 2
        is_static = fp_data["sig_hash"] in STATIC_HASHES
        if is_static:
            static_count += 1
        else:
            mutable.append({
                "type": type(obj.Geometry).__name__,
                "cz":   round(cz, 3),
                "sz":   round(sz, 3),
                "fp":   round(fp, 3),
                "min_z": round(min_z, 3),
            })

    # Find stone cz for role classification
    stones = [o for o in mutable if o["cz"] > 8]
    stone_cz = max(stones, key=lambda o: o["fp"])["cz"] if stones else 13.0

    print(f"\n{'='*60}")
    print(f"  {label}  (static={static_count}, mutable={len(mutable)})")
    print(f"{'='*60}")
    print(f"  {'Type':<22} {'cz':>7} {'sz':>7} {'fp':>7} {'min_z':>7} {'role'}")
    print(f"  {'-'*22} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'----'}")
    for o in sorted(mutable, key=lambda x: -x["cz"]):
        role = classify_role(o["cz"], o["sz"], o["fp"], stone_cz)
        print(f"  {o['type']:<22} {o['cz']:>7.3f} {o['sz']:>7.3f} {o['fp']:>7.3f} {o['min_z']:>7.3f}  {role}")
