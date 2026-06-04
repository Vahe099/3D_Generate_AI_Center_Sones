# Project Status

## Current Phase: Phase 23H (committed) → Phase 23I (in progress)

**Last successful commit:** (Phase 23H — see Phase History)

---

## Synthesis Scorecard (as of 2026-06-04 Phase 23H)

| Verdict | Count |
|---------|-------|
| PASS    | 53    |
| WARN    | 20    |
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

**Phase 23E — no PASS gain (structural contamination confirmed)**:
- AD_1_PE and AD_1_RD: re-analyzed (40-candidate pool), re-synthesized with max-attempts=20
- Result unchanged: TS20/TS30 remain best donors, contaminant_rate=9.1% (2/22) in both cases
- Diagnosis: 2 objects permanently misclassified — all geometry-compatible donors produce
  exactly 2 contaminants; low-rank fresh donors fail on geometry checks first
- Conclusion: STRUCTURAL WARN — accept, do not re-target

**Phase 23G lifted +1 to PASS** (relative to Phase 23F commit):
- ER7_Solitare_PE: WARN → PASS (donor: N1)

**Phase 23H lifted +3 to PASS** (relative to Phase 23G commit):
- ER4_Halo_AS: WARN → PASS (donor: TS20, basket_depth=0.754)
- ER4_Halo_OV: WARN → PASS (donor: TS28, basket_depth=0.822)
- ER4_Halo_RA: WARN → PASS (donor: AD_8, basket_depth=0.864, conf=HIGH)

**Phase 23H investigated, unchanged**:
- ER3_Halo_PR: affine_sanity tZ=-4.63mm (ER2_Solitare got to -3.95mm but pool exhausted after that)
- TS21_PR_archB: basket_depth=0.669 (structural — TS21 expected basket=5.145mm, pool limited)

**Phase 23F lifted +4 to PASS** (relative to Phase 23D commit):
- AD_13_OV:  WARN → PASS (donor: ER1_Solitare)
- ER9_Halo_AS: WARN → PASS (donor: SP2)
- GS_62_RA:  WARN → PASS (donor: U11)
- TS35_PE:   WARN → PASS (donor: TS16)

**Phase 23F new donors, still WARN**:
- AD_11_RD: new donor AD_7, mutable_loss=11.8% (was AD_10 14.3%)
- AD_5_PE:  new donor TS16, basket_depth=0.579 (unchanged ratio, structural)
- N6_AS:    new donor TS14, obj_count=0.551 + mutable_loss=15.2%

**Phase 23F regression avoided**:
- GS_62_PE: attempted re-synthesis produced FAIL (AD_7, mutable_loss FAIL) — restored to
  committed state (ER6_Solitare, mutable_loss=22.2% WARN)

---

## Active Issues

### Structural WARNs (investigated, no path to PASS without code changes)

| Target    | Donor (current)    | Failing check          | Root cause                                          |
|-----------|--------------------|------------------------|-----------------------------------------------------|
| AD_1_PE   | TS20               | contaminant 9.1%       | 2 objects permanently misclassified in AD_1 context |
| AD_1_RD   | TS30               | contaminant 9.1%       | Same — identical 2/22 pattern across all donors     |
| AD_5_PE   | TS16               | basket_depth 0.579     | AD_5 basket expected=33.4mm; 8 attempts, all FAIL/WARN |
| AD_9_PE   | ER7_Pave_H_Shank   | mutable_loss 13.1%     | Z-range mismatch; V5 also tried, same result        |
| AD_11_RD  | AD_7               | mutable_loss 11.8%     | Best available donor; pool exhausted (AD_7 BL, TM2/TM9 FAIL) |
| GS_62_PE  | ER6_Solitare       | mutable_loss 22.2%     | Very small ring (~9 objects); high filter rate      |
| N6_AS     | TS14               | obj_count+mutable_loss | Pool mostly exhausted; expected=69 objs, pool tops at 62 |
| Warren_AS | ER6_Pave_H_Shank   | mutable_loss 15.0%     | Pool exhausted; unusual ring geometry               |
| Warren_PR | ER2_Solitare       | object_count 0.667     | All adequate-count donors fail on geometry checks   |
| Warren_RD | ER1_Solitare       | object_count 0.606     | Same                                                |

### Remaining basket_depth / affine WARNs (geometric mismatch — investigated in 23G/23H)

| Target        | Donor         | basket ratio | Note                              |
|---------------|---------------|-------------|-----------------------------------|
| ER3_Halo_AS   | TM8           | 0.657       | Pool tried, best available        |
| ER3_Halo_OV   | TS27          | (stone_ar)  | Different failure mode            |
| ER3_Halo_PE   | ER4_Solitare  | 0.608       | Pool exhausted (REVIEW_NEEDED)    |
| ER3_Halo_PR   | ER1_Solitare  | PASS        | Affine_sanity WARN (new issue)    |
| ER4_Halo_AS   | AD_8          | 0.699       | Pool tried, best available        |
| ER4_Halo_OV   | TS27          | 0.652       | Improved: dropped contaminant WARN |
| ER4_Halo_RA   | TS20          | 0.649       | Pool tried, best available        |
| N3_PE         | SP2           | 0.605       | Pool tried, best available        |
| U5_PR         | ER1_Solitare  | 0.679       | Affine_sanity also WARN           |
| TS21_PR_archB | ER1_Solitare  | 0.669       | archA = FAIL; structural          |

### Other WARNs (not targeted — structural)

TS21_AS/PE/RD (archB — basket+mutable_loss),
TS35_PE, U5_PR

### FAILs (hard failures — structural, do not target)

| Target      | Root cause                          |
|-------------|-------------------------------------|
| TS21_AS     | stone_cz delta 11.99, basket 2.3x   |
| TS21_PE     | stone_ar wrong shape, basket 0.14x  |
| TS21_RD     | stone_cz delta 9.37, stone_off 4mm  |
| U14_PE      | contaminant_rate 85.7%, stone_cz 9mm off |

---

## Next Action — Phase 23I

Phase 23H lifted PASS to 53. 20 WARNs remain. Phase 23I audit (2026-06-05):

### Potentially fixable (5 targets, in priority order)

| Priority | Target | Failing check | Gap | Approach |
|----------|--------|---------------|-----|----------|
| 1 | ER3_Halo_PR | affine_sanity tZ=-4.63mm | 1.13mm from PASS | ER2_Solitare reached -3.95mm; try ER3_Solitare (fresh) |
| 2 | U5_PR | basket+affine (elevated) | basket 0.43mm, tZ 1.70mm | Blacklist ER1_Solitare; re-synthesize |
| 3 | TS21_PR_archB | basket_depth 0.669 | 0.42mm | Need donor with basket ≥3.86mm AND ≤12 objects |
| 4 | ER3_Halo_AS | basket_depth 0.657 | 1.85mm | Inject deep-basket donors (N3/TS33) into plan |
| 5 | ER3_Halo_OV | stone_ar 1.227 + affine | 0.023 stone_ar, tZ gap | Single stone_ar gap is tiny; affine also needs work |

### Structural (15 targets — do not re-target)

AD_1_PE/RD (contaminant structural), AD_5_PE (basket 33mm expected), AD_9_PE (mutable_loss),
AD_11_RD (pool exhausted), GS_62_PE (small ring), N3_PE (basket 32mm expected),
N6_AS (pool exhausted), Warren_AS/PR/RD (pool exhausted), ER3_Halo_PE (pool exhausted),
TS21_AS/PE/RD_archB (archA FAIL pattern), TS21_PE_archB (mutable_loss 21.4%)

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
| 23D | 13dc24d | V5+plan-patch for AD_9_RD + new donors → +3 PASS (45 total) |
| 23E | (no commit) | AD_1_PE/RD re-analyzed: structural contamination confirmed, WARN stays |
| 23F | ee4f39b | Blacklist + re-synthesis 8 targets → +4 PASS (49 total) |
| 23G | 045f102 | ER7_Solitare_PE PASS + basket WARNs investigated → +1 PASS (50 total) |
| 23H | (this commit) | ER4_Halo AS/OV/RA PASS (deep-basket donors) → +3 PASS (53 total) |
