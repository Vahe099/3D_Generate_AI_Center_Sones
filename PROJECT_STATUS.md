# Project Status

## Current Phase: Phase 23D (committed) → Phase 23E (planning)

**Last successful commit:** 8f93dde — Phase 23C (blacklist + re-synthesis, WARN→PASS +4, total 42)

---

## Synthesis Scorecard (as of 2026-06-03 Phase 23D)

| Verdict | Count |
|---------|-------|
| PASS    | 45    |
| WARN    | 28    |
| FAIL    | 4     |
| UNK     | 5 (old partial-format files, ignore) |

**Phase 23D lifted +3 to PASS** (relative to Phase 23C commit):
- AD_9_RD:         WARN → PASS (donor: V5, all checks PASS, mutable_loss=0%)
- AD_13_RD:        WARN → PASS (donor: ER13_Solitare)
- ER9_Solitare_AS: WARN → PASS (donor: TS27)

**Phase 23D improved but still WARN**:
- AD_9_PE: object_count fixed (was 0.731→1.016 PASS), mutable_loss still 13.1% WARN

**Phase 23D result restored (Warren shapes)**:
- Warren_AS/PR/RD: further synthesis attempts degraded to FAIL — restored to Phase 23C best state (all WARN)

---

## Active Issues

### Remaining WARNs — actionable targets for Phase 23E

| Target    | Donor (current)    | Failing check       | Status / Note                          |
|-----------|--------------------|---------------------|----------------------------------------|
| AD_9_PE   | ER7_Pave_H_Shank   | mutable_loss 13.1%  | V5 (rank 2) already tried; structural  |
| Warren_AS | ER6_Pave_H_Shank   | mutable_loss 15.0%  | Structural; pool exhausted             |
| Warren_PR | ER2_Solitare       | object_count 0.667  | Structural; many donors at FAIL        |
| Warren_RD | ER1_Solitare       | object_count 0.606  | Structural; many donors at FAIL        |
| AD_1_PE   | TS20               | contaminant 9.1%    | Threshold is 5%; near-structural       |
| AD_1_RD   | TS30               | contaminant 9.1%    | Same — 2/22 contaminants               |

### Other WARNs (not yet targeted — likely structural or need separate investigation)

AD_11_RD, AD_13_OV, AD_5_PE,
ER3_Halo_AS/OV/PE/PR (basket_depth dominant),
ER4_Halo_AS/OV/RA (basket_depth dominant),
ER7_Solitare_PE, ER9_Halo_AS,
GS_62_PE/RA, N3_PE, N6_AS,
TS21_AS/PE/PR/RD (archB — all basket_depth),
TS35_PE, U5_PR

### FAILs (hard failures — structural, do not target)

| Target      | Root cause                          |
|-------------|-------------------------------------|
| TS21_AS     | stone_cz delta 11.99, basket 2.3x   |
| TS21_PE     | stone_ar wrong shape, basket 0.14x  |
| TS21_RD     | stone_cz delta 9.37, stone_off 4mm  |
| U14_PE      | contaminant_rate 85.7%, stone_cz 9mm off |

---

## Next Action — Phase 23E

### Remaining tractable WARNs

**AD_9_PE (mutable_loss 13.1%)**: V5 was already tried by retry-warn (no improvement). Both ER7_Pave_H_Shank and V5 filter ~13-17% of objects due to Z-range mismatch in AD_9 geometry. No clear path without changing the Z-filter tolerance.

**Warren AS/PR/RD**: Structural — pool exhausted. Warren ring has unusual geometry. Attempting more donors degrades to FAIL. Accept WARN.

**AD_1_PE/RD (contaminant 9.1%)**: Contaminant threshold is 5%. Need donor with ≤1 contaminant out of ~22 objects. TS20/TS30 both give exactly 2 contaminants. Could try re-analyzing AD_1 for a larger donor pool.

### Suggested next synthesis

```powershell
# Re-analyze AD_1 to expand donor pool
python cross_family_transfer.py analyze AD_1 --out ad_1_transfer_plan.json

# Synthesize with fresh pool
python cross_family_transfer.py synthesize AD_1 --plan ad_1_transfer_plan.json --auto-fallback --retry-warn --shapes PE,RD --max-attempts 20
```

Check results with `python _check_23c.py`. If AD_1 PE/RD get PASS → commit as Phase 23E.

---

## Key Files (do not touch unless you know what you're doing)

| File | Role |
|------|------|
| `cross_family_transfer.py` | Main synthesis engine |
| `_donor_blacklist.json` | Donor blacklist (428+ entries) — modified by `_fix_23c_bl.py` etc. |
| `_cf_frame_cache.json` | Frame metric cache — do not delete |
| `synthesis_output/*_meta.json` | Per-shape synthesis results |
| `shape_library/*/` | Built libraries — do not rebuild unless 3dm files changed |

## What NOT to Touch

- **TS21** families (archA = FAIL, archB = WARN due to structural mismatch — no donor can fix this)
- **U14_PE** (contaminant explosion — structural problem with this family's library)
- **UNK verdict files** (GS_62_ELCU, GS_62_MQ, GS_62_OV_archA, GS_62_RA_archA, Warren_ELCU — old format, leave as-is)
- **The `3dm/` directory** — raw source files; only modify if renaming/normalizing

## Phase History

| Phase | Commit | Effect |
|-------|--------|--------|
| 18 | baf259e | Batch synthesize 127 families (initial run) |
| 19 | 8ec5123 | WARN→PASS: Z correction + retry-warn (introduced regressions) |
| 20 | 6754cc3 | Fix retry-warn safety, restore FAIL count to baseline |
| 22 | f6a79e4 | Fix B threshold + stone_cz re-synthesis (PASS 24→26) |
| 23A | f4d5023 | Donor retry for contaminant_rate and mutable_loss groups |
| 23B | 5e2d694 | Targeted donor blacklist + retry → WARN→PASS (+8) |
| 23C | 8f93dde | Blacklist 23B WARN donors → +4 PASS (42 total) |
| 23D | (this commit) | V5+plan-patch for AD_9_RD + new donors → +3 PASS (45 total) |
