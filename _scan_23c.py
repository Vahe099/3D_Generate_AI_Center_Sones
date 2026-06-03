"""Scan frame cache for Phase 23C donors.
1. High-count PE/OV/RD donors for AD_9 (expected 172.3 -- need actual >= 130).
2. PR donors with count 13-23 for Warren_PR (expected 16.5).
3. RD donors with count 13-23 for Warren_RD (expected 16.5).
4. AD_13 blacklist state.
"""
import json
from pathlib import Path

cache = json.loads(Path('_cf_frame_cache.json').read_text(encoding='utf-8'))
bl    = json.loads(Path('_donor_blacklist.json').read_text(encoding='utf-8'))

def bl_status(fam, shape, donor):
    k = f'{fam}|{shape}|{donor}'
    e = bl.get(k)
    if not e:
        return ''
    return f"strikes={e['strikes']}({'BL' if e['strikes']>=2 else 'warn'})"

# --- AD_9: find donors with count>=100 for PE/OV/RD ---
print('=== AD_9: high-count donors (need actual>=130, expected=172.3) ===')
for shape in ('PE', 'OV', 'RD'):
    rows = []
    for key, val in cache.items():
        dn_fam, dn_shape = key.split('|', 1)
        if dn_shape != shape or dn_fam == 'AD_9':
            continue
        cnt = val.get('count', 0) or 0
        if cnt >= 80:
            bl_st = bl_status('AD_9', shape, dn_fam)
            rows.append((cnt, dn_fam, bl_st))
    rows.sort(reverse=True)
    print(f'  {shape}: (top donors with count>=80)')
    for cnt, dn, st in rows[:12]:
        print(f'    {dn:<28} count={cnt:<5} {st}')
    print()

# --- Warren_PR: donors with PR count 13-23 ---
print('=== Warren_PR: donors with PR count 13-23 (expected=16.5, PASS zone 12-23) ===')
rows = []
for key, val in cache.items():
    dn_fam, dn_shape = key.split('|', 1)
    if dn_shape != 'PR' or dn_fam == 'Warren':
        continue
    cnt = val.get('count', 0) or 0
    if 12 <= cnt <= 25:
        bl_st = bl_status('Warren', 'PR', dn_fam)
        rows.append((abs(cnt - 16.5), cnt, dn_fam, bl_st))
rows.sort()
for _, cnt, dn, st in rows[:15]:
    print(f'  {dn:<28} count={cnt:<5} {st}')
print()

# --- Warren_RD: donors with RD count 13-23 ---
print('=== Warren_RD: donors with RD count 13-23 (expected=16.5, need <=23) ===')
rows = []
for key, val in cache.items():
    dn_fam, dn_shape = key.split('|', 1)
    if dn_shape != 'RD' or dn_fam == 'Warren':
        continue
    cnt = val.get('count', 0) or 0
    if 12 <= cnt <= 25:
        bl_st = bl_status('Warren', 'RD', dn_fam)
        rows.append((abs(cnt - 16.5), cnt, dn_fam, bl_st))
rows.sort()
for _, cnt, dn, st in rows[:15]:
    print(f'  {dn:<28} count={cnt:<5} {st}')
print()

# --- AD_13 blacklist state ---
print('=== AD_13 blacklist (PE + RD) ===')
for key, entry in sorted(bl.items()):
    if key.startswith('AD_13|PE|') or key.startswith('AD_13|RD|'):
        print(f'  {key:<40} strikes={entry["strikes"]}  status={entry.get("status","?")}')
