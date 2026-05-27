"""
High_Mira PR/RD: old SP6 vs new AD_8 head-to-head comparison.
Metrics: score, stone_cz delta, z_range_ratio, footprint ratio (silhouette), risk flags.
Real files compared with Gem-layer refs stripped in-memory (no writes).
"""
import json, math, sys
from pathlib import Path
import rhino3dm

sys.path.insert(0, str(Path(__file__).parent))
from jewelry_transform import fingerprint_object, is_contaminant

SHAPES = ["PR", "RD"]

REAL_FILES = {
    "PR": Path("3dm/High_Mira/High Mira_PR.3dm"),
    "RD": Path("3dm/High_Mira/High Mira_RD.3dm"),
}
OLD_FILES = {
    "PR": Path("synthesis_output/High_Mira_PR_archB.3dm"),
    "RD": Path("synthesis_output/High_Mira_RD_archA.3dm"),
}
NEW_FILES = {
    "PR": Path("synthesis_output_ad8/High_Mira_PR_archB.3dm"),
    "RD": Path("synthesis_output_ad8/High_Mira_RD_archA.3dm"),
}

HM_CLS = Path("shape_library/High_Mira/classification.json")
cls = json.loads(HM_CLS.read_text(encoding="utf-8"))
STATIC_HASHES = set(cls.get("combined_static_hashes", []))


def load_clean(fpath):
    model = rhino3dm.File3dm.Read(str(fpath))
    if not model:
        raise RuntimeError(f"Cannot read {fpath}")
    return model


def best_stone_cz(model):
    best_fp, best_cz = 0.0, None
    for obj in model.Objects:
        if is_contaminant(obj, model):
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        cz = (bb.Max.Z + bb.Min.Z) / 2
        fp = max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y)
        if cz > 8 and fp > best_fp:
            best_fp = fp
            best_cz = round(cz, 3)
    return best_cz


def stone_ar(model):
    best_fp, ar = 0.0, None
    for obj in model.Objects:
        if is_contaminant(obj, model):
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        cz = (bb.Max.Z + bb.Min.Z) / 2
        dx = bb.Max.X - bb.Min.X
        dy = bb.Max.Y - bb.Min.Y
        fp = max(dx, dy)
        if cz > 8 and fp > best_fp:
            best_fp = fp
            mn = min(dx, dy)
            ar = round(fp / mn, 4) if mn > 0.001 else None
    return ar


def z_range_mutable(model):
    zs = []
    for obj in model.Objects:
        if is_contaminant(obj, model):
            continue
        fp_data = fingerprint_object(obj)
        if fp_data is None:
            continue
        if fp_data["sig_hash"] in STATIC_HASHES:
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        cz = (bb.Max.Z + bb.Min.Z) / 2
        if cz < 0:
            continue
        zs.append(bb.Min.Z)
        zs.append(bb.Max.Z)
    if not zs:
        return 0.0, 0.0, 0.0
    return round(min(zs), 3), round(max(zs), 3), round(max(zs) - min(zs), 3)


def footprint_xy(model):
    best = 0.0
    for obj in model.Objects:
        if is_contaminant(obj, model):
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        fp = max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y)
        cz = (bb.Max.Z + bb.Min.Z) / 2
        if cz > 2 and fp > best:
            best = fp
    return round(best, 3)


def count_mutable(model):
    n = 0
    for obj in model.Objects:
        if is_contaminant(obj, model):
            continue
        fp_data = fingerprint_object(obj)
        if fp_data is None:
            continue
        if fp_data["sig_hash"] not in STATIC_HASHES:
            n += 1
    return n


def count_sub_plane(model):
    n = 0
    for obj in model.Objects:
        if is_contaminant(obj, model):
            continue
        bb = obj.Geometry.GetBoundingBox()
        if not bb:
            continue
        cz = (bb.Max.Z + bb.Min.Z) / 2
        fp = max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y)
        if cz < -0.5 and fp > 1:
            n += 1
    return n


WEIGHTS = {"stone_cz": 0.30, "z_range": 0.20, "footprint": 0.25, "ar": 0.15, "count": 0.10}


def score_model(real_m, synth_m):
    real_cz   = best_stone_cz(real_m)
    synth_cz  = best_stone_cz(synth_m)
    cz_delta  = round(synth_cz - real_cz, 3) if (real_cz and synth_cz) else None

    real_ar   = stone_ar(real_m)
    synth_ar  = stone_ar(synth_m)
    ar_ratio  = round(min(real_ar, synth_ar) / max(real_ar, synth_ar), 4) if (real_ar and synth_ar) else None

    _, _, r_zrange = z_range_mutable(real_m)
    _, _, s_zrange = z_range_mutable(synth_m)
    zrange_ratio   = round(s_zrange / r_zrange, 4) if r_zrange > 0.1 else None

    real_fp   = footprint_xy(real_m)
    synth_fp  = footprint_xy(synth_m)
    fp_ratio  = round(min(real_fp, synth_fp) / max(real_fp, synth_fp), 4) if (real_fp and synth_fp) else None

    real_mut  = count_mutable(real_m)
    synth_mut = count_mutable(synth_m)
    synth_sub = count_sub_plane(synth_m)

    sub_scores = {}
    if cz_delta is not None:
        sub_scores["stone_cz"] = max(0.0, 1.0 - abs(cz_delta) / 5.0)
    if zrange_ratio is not None:
        sub_scores["z_range"]  = max(0.0, 1.0 - abs(1.0 - zrange_ratio) / 0.5)
    if fp_ratio is not None:
        sub_scores["footprint"] = fp_ratio
    if ar_ratio is not None:
        sub_scores["ar"]       = ar_ratio
    if real_mut > 0:
        sub_scores["count"]    = max(0.0, 1.0 - abs(real_mut - synth_mut) / max(real_mut, 1))

    total_w = sum(WEIGHTS[k] for k in sub_scores)
    composite = round(sum(WEIGHTS[k] * v for k, v in sub_scores.items()) / total_w, 4) if total_w > 0 else 0.0

    flags = []
    if cz_delta is not None and abs(cz_delta) > 2.0:
        flags.append(f"STONE_CZ_DELTA_HIGH({cz_delta:+.2f}mm)")
    if zrange_ratio is not None and (zrange_ratio < 0.5 or zrange_ratio > 2.0):
        flags.append(f"Z_RANGE_MISMATCH({zrange_ratio:.3f}x)")
    if fp_ratio is not None and fp_ratio < 0.75:
        flags.append(f"FOOTPRINT_MISMATCH({fp_ratio:.3f})")
    if synth_mut < real_mut * 0.6:
        flags.append(f"COUNT_LOW(synth={synth_mut}/real={real_mut})")
    if synth_sub > 0:
        flags.append(f"SUB_PLANE({synth_sub})")

    return {
        "score":       composite,
        "cz_delta":    cz_delta,
        "zrange_ratio":zrange_ratio,
        "fp_ratio":    fp_ratio,
        "ar_ratio":    ar_ratio,
        "mut_real":    real_mut,
        "mut_synth":   synth_mut,
        "sub_scores":  sub_scores,
        "flags":       flags,
        "real_cz":     real_cz,
        "synth_cz":    synth_cz,
        "r_zrange":    r_zrange,
        "s_zrange":    s_zrange,
    }


def rank(s):
    if s >= 0.80: return "EXCELLENT"
    if s >= 0.60: return "GOOD"
    return "POOR"


results = {}
for shape in SHAPES:
    real_m = load_clean(REAL_FILES[shape])
    old_m  = load_clean(OLD_FILES[shape])
    new_m  = load_clean(NEW_FILES[shape])
    results[shape] = {
        "old": score_model(real_m, old_m),
        "new": score_model(real_m, new_m),
    }

print()
print("=" * 76)
print("  High_Mira PR/RD: SP6 (old) vs AD_8 (new) -- vs real geometry")
print("=" * 76)
print(f"  {'Shape':<5}  {'Donor':<6}  {'Score':>6}  {'Rank':<9}  {'cz_delta':>9}  {'zrange_r':>8}  {'fp_r':>6}  {'mut':>6}  flags")
print(f"  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*6}  {'-'*6}")

for shape in SHAPES:
    for donor_label, key in [("SP6", "old"), ("AD_8", "new")]:
        r = results[shape][key]
        cz_d  = f"{r['cz_delta']:+.2f}mm" if r["cz_delta"] is not None else "  N/A"
        zr    = f"{r['zrange_ratio']:.3f}x" if r["zrange_ratio"] is not None else "  N/A"
        fpr   = f"{r['fp_ratio']:.3f}"  if r["fp_ratio"]   is not None else "  N/A"
        mut   = f"{r['mut_real']}/{r['mut_synth']}"
        flags = "  ".join(r["flags"]) if r["flags"] else "--"
        print(f"  {shape:<5}  {donor_label:<6}  {r['score']:>6.3f}  {rank(r['score']):<9}  {cz_d:>9}  {zr:>8}  {fpr:>6}  {mut:>6}  {flags}")
    print()

print("  Sub-metric breakdown:")
print(f"  {'Shape':<5}  {'Donor':<6}  {'stone_cz':>9}  {'z_range':>8}  {'footprint':>10}  {'ar':>6}  {'count':>7}")
for shape in SHAPES:
    for donor_label, key in [("SP6", "old"), ("AD_8", "new")]:
        ss = results[shape][key]["sub_scores"]
        fmt = lambda k: f"{ss[k]:.3f}" if k in ss else "  N/A"
        print(f"  {shape:<5}  {donor_label:<6}  {fmt('stone_cz'):>9}  {fmt('z_range'):>8}  {fmt('footprint'):>10}  {fmt('ar'):>6}  {fmt('count'):>7}")
    print()

print("  Winner summary:")
for shape in SHAPES:
    o = results[shape]["old"]["score"]
    n = results[shape]["new"]["score"]
    winner = "AD_8 (+{:.3f})".format(n-o) if n > o else ("SP6 (+{:.3f})".format(o-n) if o > n else "TIE")
    print(f"  {shape}: SP6={o:.3f}  AD_8={n:.3f}  --> {winner}")
