"""Simulate _pre_reject for every AD_9 RD plan candidate and show which rule fires."""
import json
from pathlib import Path

plan = json.loads(Path('ad_9_transfer_plan.json').read_text(encoding='utf-8'))
bl   = json.loads(Path('_donor_blacklist.json').read_text(encoding='utf-8'))

STONE_AR_RD = (0.92, 1.08)
HM_COUNT_MEAN = 172.3
HM_MEL_REF = 0          # artdeco family has 0 melee stones
IS_ELEVATED = False
INFLATED_CEIL = 1.65
SPARSE_FLOOR  = 0.55

for sp in plan['missing_shape_plans']:
    if sp['missing_shape'] != 'RD':
        continue
    cands = sp['top_candidates']
    print('AD_9 RD — %d plan candidates  (hm_count_mean=%.1f  sparse_floor=%.0f  inflated_ceil=%.0f)' % (
        len(cands), HM_COUNT_MEAN, SPARSE_FLOOR * HM_COUNT_MEAN, INFLATED_CEIL * HM_COUNT_MEAN))
    print('  %-5s %-28s %-6s %-6s %-8s  rejection' % ('rank', 'donor', 'count', 'ratio', 'ar'))
    print('  ' + '-'*80)

    n_valid = 0
    for c in cands:
        dn_fam = c['donor_family']
        dtf    = c.get('donor_target_frame') or {}
        cnt    = dtf.get('count') or 0
        ar     = dtf.get('stone_ar')
        mel    = dtf.get('melee_count_est', 0) or 0

        reasons = []

        # BL
        bl_key = 'AD_9|RD|%s' % dn_fam
        bl_st = bl.get(bl_key, {}).get('strikes', 0)
        if bl_st >= 2:
            reasons.append('BLACKLISTED(strikes=%d)' % bl_st)

        # PR-1 contaminant predicted
        if HM_MEL_REF < 8 and cnt > 0 and (mel / cnt) > 0.40:
            reasons.append('CONTAMINANT_PREDICTED(%.0f%%)' % (mel/cnt*100))

        # PR-2 count
        if cnt > 0:
            ratio = cnt / HM_COUNT_MEAN
            if ratio > INFLATED_CEIL:
                reasons.append('COUNT_INFLATED(%.2fx)' % ratio)
            elif ratio < SPARSE_FLOOR:
                reasons.append('COUNT_SPARSE(%.2fx=%.0f)' % (ratio, cnt))

        # PR-3 style mismatch HIGH
        for risk in c.get('risk_factors', []):
            if risk['factor'] == 'STYLE_MISMATCH' and risk['severity'] == 'HIGH':
                reasons.append('STYLE_MISMATCH_HIGH')
                break

        # PR-4 stone_ar
        if ar is not None and not (STONE_AR_RD[0] <= ar <= STONE_AR_RD[1]):
            reasons.append('STONE_AR(%.3f)' % ar)

        ratio_str = '%.3f' % (cnt / HM_COUNT_MEAN) if cnt else '---'
        ar_str    = '%.3f' % ar if ar is not None else 'None'

        if not reasons:
            n_valid += 1
            tag = 'VALID'
        else:
            tag = ', '.join(reasons)

        print('  %-5d %-28s %-6s %-6s %-8s  %s' % (
            c['rank'], dn_fam, str(cnt), ratio_str, ar_str, tag))

    print('\n  Valid after pre-reject: %d / %d' % (n_valid, len(cands)))
    break

# Also show which high-count donors exist in the cache but are NOT in the plan
print('\n=== High-count RD donors not in plan (count > 94 = 0.55 * 172.3) ===')
cache = json.loads(Path('_cf_frame_cache.json').read_text(encoding='utf-8'))
plan_rd_names = set()
for sp in plan['missing_shape_plans']:
    if sp['missing_shape'] == 'RD':
        plan_rd_names = {c['donor_family'] for c in sp['top_candidates']}
        break

for key, val in sorted(cache.items(), key=lambda x: -(x[1].get('count') or 0)):
    dn_fam, dn_shape = key.split('|', 1)
    if dn_shape != 'RD' or dn_fam == 'AD_9':
        continue
    cnt = val.get('count') or 0
    if cnt < 95:
        break
    if dn_fam not in plan_rd_names:
        print('  %-28s  count=%-5d  ratio=%.3f  (NOT in plan)' % (
            dn_fam, cnt, cnt / HM_COUNT_MEAN))
