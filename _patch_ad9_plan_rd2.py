"""Inject V5 and V3 into AD_9 RD top_candidates.

After ER7_Pave_H_Shank (BL), ER5_Halo (BL), ER8_Halo (BL), TS2 (BL),
remaining plan candidates all have count<90 (FAIL zone).
V5 RD count=188 (ratio=1.10, PASS), V3 RD count=106 (ratio=0.62, WARN) are not in plan.
"""
import json, math
from pathlib import Path

cache = json.loads(Path('_cf_frame_cache.json').read_text(encoding='utf-8'))
lib_root = Path('shape_library')
plan_path = Path('ad_9_transfer_plan.json')
plan = json.loads(plan_path.read_text(encoding='utf-8'))

TARGET = 'AD_9'
MISS = 'RD'
HM_KNOWN = ['AS', 'EM', 'PR', 'RA']
STONE_AR_RD = (0.92, 1.08)

idx_target = json.loads((lib_root / TARGET / 'shape_index.json').read_text(encoding='utf-8'))
hm_counts = [idx_target[s]['mutable_object_count'] for s in HM_KNOWN if s in idx_target]
hm_count_mean = sum(hm_counts) / max(len(hm_counts), 1)


def _gauss(x, s): return math.exp(-0.5 * (x / s) ** 2)
def _rel(a, b): return a / max(b, 0.001) - 1.0


def build_candidate(donor, rank_slot):
    idx_donor = json.loads((lib_root / donor / 'shape_index.json').read_text(encoding='utf-8'))
    best_s, best_delta = None, float('inf')
    for s in HM_KNOWN:
        dn_f = cache.get('%s|%s' % (donor, s))
        hm_f = cache.get('%s|%s' % (TARGET, s))
        if dn_f and hm_f:
            d = abs(dn_f['footprint_xy'] - hm_f['footprint_xy'])
            if d < best_delta:
                best_delta = d; best_s = s
    assert best_s, 'No bridge for %s' % donor
    dn_bf = cache['%s|%s' % (donor, best_s)]
    hm_bf = cache['%s|%s' % (TARGET, best_s)]
    dn_tf = cache.get('%s|%s' % (donor, MISS)) or {}
    dn_tgt_count = idx_donor.get(MISS, {}).get('mutable_object_count', 0)

    dn_cz = dn_bf.get('stone_cz') or dn_bf.get('cz_mean', 0)
    hm_cz = hm_bf.get('stone_cz') or hm_bf.get('cz_mean', 0)
    dn_dep = dn_bf.get('basket_depth') or dn_bf.get('z_range', 1.0)
    hm_dep = hm_bf.get('basket_depth') or hm_bf.get('z_range', 1.0)
    dn_fp = dn_bf['footprint_xy']; hm_fp = hm_bf['footprint_xy']
    dn_top = dn_bf.get('z_top') or dn_bf.get('z_range', 1.0)
    hm_top = hm_bf.get('z_top') or hm_bf.get('z_range', 1.0)

    c1 = _gauss(_rel(dn_cz, hm_cz), 0.20) * _gauss(_rel(dn_fp, hm_fp), 0.80) * _gauss(_rel(dn_dep, hm_dep), 0.50)
    c2 = max(0.0, 1.0 - abs(dn_tgt_count - hm_count_mean) / max(hm_count_mean, 1))
    r_dn = dn_fp / max(dn_top, 0.01); r_hm = hm_fp / max(hm_top, 0.01)
    c3 = _gauss(r_dn / max(r_hm, 0.001) - 1.0, 0.50)
    c4 = len(set(list(idx_donor.keys())) & set(HM_KNOWN)) / max(len(HM_KNOWN), 1)
    prescore = round(0.40*c1 + 0.20*c2 + 0.20*c3 + 0.20*c4, 4)

    hm_style_frames = [(cache['%s|%s' % (TARGET, s)].get('melee_count_est', 0),
                        cache['%s|%s' % (TARGET, s)].get('prong_count_est', 0))
                       for s in HM_KNOWN if cache.get('%s|%s' % (TARGET, s))]
    hm_melee_ref = max((m for m, p in hm_style_frames), default=0)
    dn_ar = dn_tf.get('stone_ar')
    dn_mel = dn_tf.get('melee_count_est', 0)
    ar_lo, ar_hi = STONE_AR_RD
    hm_target_ar = (ar_lo + ar_hi) / 2
    ar_sim = round(min(hm_target_ar, dn_ar) / max(hm_target_ar, dn_ar), 4) if dn_ar else None
    c7 = None
    if dn_mel is not None:
        c7 = (1.0 if (hm_melee_ref == 0 and dn_mel == 0)
              else min(hm_melee_ref, dn_mel) / max(max(hm_melee_ref, dn_mel), 1))
        c7 = round(c7, 4)
    w_c5 = 0.10 if ar_sim is not None else 0.0
    w_c7 = 0.20 if c7 is not None else 0.0
    w_base = round(1.0 - w_c5 - w_c7, 2)
    final_score = round(w_base*prescore + (w_c5*ar_sim if ar_sim else 0) + (w_c7*c7 if c7 else 0), 4)
    sxy = round(hm_fp / max(dn_fp, 0.001), 4)
    sz = round(hm_dep / max(dn_dep, 0.001), 4)
    dz = round(hm_cz - sz * (dn_tf.get('stone_cz') or dn_cz), 4)

    print('  %s: prescore=%.4f final=%.4f bridge=%s count=%d' % (
        donor, prescore, final_score, best_s, dn_tgt_count))
    return {
        'rank': rank_slot,
        'donor_family': donor,
        'donor_score': final_score,
        'score_breakdown': {'frame_similarity': round(c1, 4), 'count_sim': round(c2, 4),
                            'spatial_ratio': round(c3, 4), 'shared_shapes': round(c4, 4)},
        'stone_ar_sim': ar_sim, 'melee_sim': c7,
        'bridge_shape_used': best_s,
        'donor_target_count': dn_tgt_count, 'hm_expected_count': round(hm_count_mean, 1),
        'affine_params': {'scale_xy': sxy, 'scale_z': sz, 'translate_z': dz},
        'donor_target_frame': {
            'stone_ar': dn_tf.get('stone_ar'), 'stone_cz': dn_tf.get('stone_cz'),
            'footprint_xy': dn_tf.get('footprint_xy'), 'z_range': dn_tf.get('z_range'),
            'prong_count_est': dn_tf.get('prong_count_est'), 'melee_count_est': dn_tf.get('melee_count_est'),
            'strata_count': dn_tf.get('strata_count'), 'basket_depth': dn_tf.get('basket_depth'),
            'count': dn_tf.get('count'),
        } if dn_tf else None,
        'expected_confidence': 'LOW',
        'risk_factors': [], 'risk_count': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
        'recommended_filters': [], 'auto_arch': ['A'],
    }


donors_to_inject = ['V5', 'V3']
print('Injecting into AD_9 RD (hm_count_mean=%.1f):' % hm_count_mean)
injected = [build_candidate(d, i + 1) for i, d in enumerate(donors_to_inject)]

for sp in plan['missing_shape_plans']:
    if sp['missing_shape'] == MISS:
        existing = [c for c in sp['top_candidates'] if c['donor_family'] not in donors_to_inject]
        for i, c in enumerate(existing, len(injected) + 1):
            c['rank'] = i
        sp['top_candidates'] = injected + existing
        print('\nInjected %s into %s (%d -> %d candidates)' % (
            donors_to_inject, MISS, len(existing), len(sp['top_candidates'])))
        break

plan_path.write_text(json.dumps(plan, indent=2), encoding='utf-8')
print('Plan saved.')
