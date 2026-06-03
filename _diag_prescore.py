"""Compute pre-score (C1-C4) for V5 and ER7_Pave_H_Shank for AD_9 RD,
then show where they would rank in the full prescored list."""
import json, math
from pathlib import Path

cache   = json.loads(Path('_cf_frame_cache.json').read_text(encoding='utf-8'))
lib_root = Path('shape_library')

# --- AD_9 parameters
HM_KNOWN  = ['AS', 'EM', 'PR', 'RA']
TARGET    = 'AD_9'
MISS      = 'RD'

# hm_count_mean from shape_library idx (not frame cache)
idx_ad9 = json.loads((lib_root / TARGET / 'shape_index.json').read_text(encoding='utf-8'))
hm_counts = [idx_ad9[s]['mutable_object_count'] for s in HM_KNOWN if s in idx_ad9]
hm_count_mean = sum(hm_counts) / max(len(hm_counts), 1)
print('hm_count_mean (from idx):', hm_count_mean)

def _gauss(x, sigma):
    return math.exp(-0.5 * (x / sigma) ** 2)

def _rel(a, b):
    return a / max(b, 0.001) - 1.0

def _score(dn_bf, hm_bf, dn_tgt_count, dn_shapes):
    dn_cz  = dn_bf.get('stone_cz') or dn_bf.get('cz_mean', 0)
    hm_cz  = hm_bf.get('stone_cz') or hm_bf.get('cz_mean', 0)
    dn_dep = dn_bf.get('basket_depth') or dn_bf.get('z_range', 1.0)
    hm_dep = hm_bf.get('basket_depth') or hm_bf.get('z_range', 1.0)
    dn_fp  = dn_bf['footprint_xy']
    hm_fp  = hm_bf['footprint_xy']

    c1 = (_gauss(_rel(dn_cz, hm_cz), 0.20) *
          _gauss(_rel(dn_fp, hm_fp), 0.80) *
          _gauss(_rel(dn_dep, hm_dep), 0.50))

    c2 = max(0.0, 1.0 - abs(dn_tgt_count - hm_count_mean) / max(hm_count_mean, 1))

    r_dn = dn_fp / max(dn_bf.get('z_top', dn_bf.get('z_range', 1.0)), 0.01)
    r_hm = hm_fp / max(hm_bf.get('z_top', hm_bf.get('z_range', 1.0)), 0.01)
    c3 = _gauss(r_dn / max(r_hm, 0.001) - 1.0, 0.50)

    c4 = len(set(dn_shapes) & set(HM_KNOWN)) / max(len(HM_KNOWN), 1)

    composite = round(0.40*c1 + 0.20*c2 + 0.20*c3 + 0.20*c4, 4)
    return composite, {'c1': round(c1,4), 'c2': round(c2,4), 'c3': round(c3,4), 'c4': round(c4,4)}

# Load all libraries to simulate the full pool
all_libs = {}
for cls_f in sorted(lib_root.glob('*/classification.json')):
    fam = cls_f.parent.name
    idx_f = cls_f.parent / 'shape_index.json'
    if not idx_f.exists():
        continue
    try:
        idx = json.loads(idx_f.read_text(encoding='utf-8'))
        all_libs[fam] = {'idx': idx, 'shapes': list(idx.keys())}
    except Exception:
        pass

# Pool: donors with RD + at least one HM known shape
pool_rd = [
    fam for fam, fd in all_libs.items()
    if fam != TARGET
    and MISS in fd['shapes']
    and any(s in fd['shapes'] for s in HM_KNOWN if s != MISS)
]
print('RD pool size:', len(pool_rd))

# For each donor: find best bridge, compute pre-score
rows = []
for fam in pool_rd:
    fd = all_libs[fam]
    best_s, best_delta = None, float('inf')
    for s in HM_KNOWN:
        if s == MISS:
            continue
        dn_frame = cache.get('%s|%s' % (fam, s))
        hm_frame = cache.get('%s|%s' % (TARGET, s))
        if dn_frame and hm_frame:
            d = abs(dn_frame['footprint_xy'] - hm_frame['footprint_xy'])
            if d < best_delta:
                best_delta = d
                best_s = s
    if not best_s:
        continue
    dn_bf = cache['%s|%s' % (fam, best_s)]
    hm_bf = cache['%s|%s' % (TARGET, best_s)]
    dn_tgt_count = fd['idx'].get(MISS, {}).get('mutable_object_count', 0)
    sc, bk = _score(dn_bf, hm_bf, dn_tgt_count, fd['shapes'])
    rows.append((sc, fam, bk, best_s, dn_tgt_count))

rows.sort(reverse=True)
print('\nAD_9 RD pre-score ranking (computed now):')
print('%-5s %-28s %6s  %-4s  %s' % ('rank', 'donor', 'score', 'brdg', 'c1/c2/c3/c4'))
focus = {'V5', 'ER7_Pave_H_Shank', 'V3', 'ER5_Halo', 'ER8_Halo', 'TS2'}
for i, (sc, fam, bk, brdg, cnt) in enumerate(rows, 1):
    if i <= 45 or fam in focus:
        flag = ' ***' if fam in focus else ''
        print('  %3d  %-28s %.4f  %-4s  c1=%.3f c2=%.3f c3=%.3f c4=%.3f cnt=%d%s' % (
            i, fam, sc, brdg,
            bk['c1'], bk['c2'], bk['c3'], bk['c4'], cnt, flag))
