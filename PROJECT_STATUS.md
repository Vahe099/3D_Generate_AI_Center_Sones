# Project Status

## Current Phase: Phase 23C (committed) → Phase 23D (planning)

**Last successful commit:** 5e2d694 — Phase 23B (targeted donor blacklist + retry lifts WARN→PASS +8)

---

## Synthesis Scorecard (as of 2026-06-03 Phase 23C)

| Verdict | Count |
|---------|-------|
| PASS    | 42    |
| WARN    | 31    |
| FAIL    | 4     |
| UNK     | 5 (old partial-format files, ignore) |

**Phase 23C lifted +4 to PASS** (relative to Phase 23B commit):
- AD_13_PE: WARN → PASS (donor: AD_7)
- AD_1_OV:  WARN → PASS (donor: AD_7)
- AD_9_OV:  WARN → PASS (donor: ER7_Pave_H_Shank)
- TS22_PE:  WARN → PASS (donor: U6)

---

## Active Issues

### Remaining WARNs from Phase 23C target group (actionable)

| Target           | Donor (current)     | Failing check        | Next candidate            |
|------------------|---------------------|----------------------|---------------------------|
| AD_9_PE          | ER5_Halo (BL)       | object_count 0.731   | ER7_Pave_H_Shank (199)    |
| AD_9_RD          | ER7_Pave_H_Shank    | mutable_loss 17.4%   | V5 (188 objs)             |
| Warren_PR        | ER2_Solitare (BL)   | object_count 0.667   | ER8_Solitare/TS34/TM9 (16)|
| Warren_RD        | ER1_Solitare (att=5)| object_count 0.606   | AD_2/ER8_Solitare (16)    |
| Warren_AS        | ER6_Pave_H_Shank    | mutable_loss 15.0%   | blacklist → next           |
| AD_1_PE          | N1 (str=1)          | obj_count + contam   | blacklist N1 → next        |
| AD_13_RD         | TS9                 | contaminant 10%      | blacklist TS9 → next       |
| ER9_Solitare_AS  | TS30                | basket_depth 0.71    | TS30 not BL → blacklist it |
| AD_1_RD          | TS9                 | contaminant 10%      | blacklist TS9 → next       |

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

## Next Action — Phase 23D

### Step 1: Create blacklist script `_fix_23d_bl.py`

Blacklist current WARN donors to force next candidates:

```python
# AD_9_RD: ER7_Pave_H_Shank gave mutable_loss=17.4%
force_temp_bl('AD_9',  'RD', 'ER7_Pave_H_Shank', ['mutable_loss'])

# Warren_AS: ER6_Pave_H_Shank gave mutable_loss=15%
force_temp_bl('Warren', 'AS', 'ER6_Pave_H_Shank', ['mutable_loss'])

# AD_1_PE: N1 gave object_count+contaminant
force_temp_bl('AD_1', 'PE', 'N1', ['object_count', 'contaminant_rate'])

# AD_13_RD: TS9 gave contaminant_rate=10%
force_temp_bl('AD_13', 'RD', 'TS9', ['contaminant_rate'])

# ER9_Solitare_AS: TS30 gave basket_depth=0.71
force_temp_bl('ER9_Solitare', 'AS', 'TS30', ['basket_depth'])

# AD_1_RD: TS9 gave contaminant_rate=10%
force_temp_bl('AD_1', 'RD', 'TS9', ['contaminant_rate'])

# Warren_RD: ER1_Solitare (attempt=5, object_count 0.606)
force_temp_bl('Warren', 'RD', 'ER1_Solitare', ['object_count'])
```

### Step 2: Re-synthesize targeted families

```powershell
python cross_family_transfer.py synthesize AD_9   --plan ad_9_transfer_plan.json   --auto-fallback --retry-warn --shapes PE RD --max-attempts 15
python cross_family_transfer.py synthesize AD_1   --plan ad_1_transfer_plan.json   --auto-fallback --retry-warn --shapes PE RD --max-attempts 15
python cross_family_transfer.py synthesize AD_13  --plan ad_13_transfer_plan.json  --auto-fallback --retry-warn --shapes RD --max-attempts 15
python cross_family_transfer.py synthesize Warren --plan warren_transfer_plan.json --auto-fallback --retry-warn --shapes AS RD --max-attempts 15
python cross_family_transfer.py synthesize ER9_Solitare --plan er9_solitare_transfer_plan.json --auto-fallback --retry-warn --shapes AS --max-attempts 15
```

Or as a single batch targeting those shapes:

```powershell
python cross_family_transfer.py batch-synthesize --families AD_9 AD_1 AD_13 Warren ER9_Solitare --retry-warn --max-attempts 15
```

### Step 3: Verify and commit

Check results with `python _check_23c.py` (targets are the same shapes).
After verification: update scorecard in this file, commit.

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
| 23C | (this commit) | Blacklist 23B WARN donors → +4 PASS (42 total) |
