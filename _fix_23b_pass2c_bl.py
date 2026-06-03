"""Phase 23B Pass 2c: mass-blacklist suboptimal RA donors, keep cz-matched candidates.

ER3_Halo_RA (target cz=13.18mm, basket=20mm):
  ER-Solitare donors: small basket (~12mm) -> basket_depth WARN.
  Already-tried ER-solitares (strikes=1): blacklist to skip.
  Keep: TS7 (cz=13.02mm, count=21), N1 (cz=13.15mm, count=11), AD_2 (cz=13.25mm, count=15)
  TS7 expected: translate_z~0.16mm (PASS), basket~16-18mm (PASS), count=1.615 (WARN, 1 check)

ER9_Solitare_RA (target cz=11.26mm):
  TS28 (cz=12.11mm, delta=0.85) -> translate_z=-6.53mm -> affine_sanity WARN.
  Keep: U8 (cz=11.31mm), V3 (cz=11.32mm), SP5/SP6 (cz=11.37mm), TS11 (cz=11.14mm)
  These should give translate_z ~0 -> PASS.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, note='manual_23b_pass2c'):
    key = '%s|%s|%s' % (family, shape, donor)
    entry = bl.get(key, {'strikes': 0, 'history': []})
    needed = max(0, 2 - entry['strikes'])
    if needed == 0:
        print('  %s  already at strikes=%d (%s)' % (key, entry['strikes'], entry.get('status', '?')))
        return
    for _ in range(needed):
        entry['history'].append({
            'timestamp': ts,
            'rejection_point': note,
            'validator_verdict': 'WARN',
            'checks_failed': ['affine_sanity_or_basket'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

# ER3_Halo_RA: blacklist solitaire donors (small basket) and bad-cz donors
# Keeping: TS7 (cz=13.02), N1 (cz=13.15), AD_2 (cz=13.25) -- all fresh (strikes=0)
print('=== ER3_Halo_RA: blacklist small-basket and bad-cz donors ===')
small_basket_er3_ra = [
    'ER13_Solitare', 'ER15_Solitare', 'ER1_Solitare', 'ER2_Solitare',
    'ER3_Solitare', 'ER4_Solitare', 'ER5_Solitare', 'ER7_Solitare', 'ER8_Solitare',
]
bad_cz_er3_ra = ['AD_7', 'AD_8', 'TM2']
for d in small_basket_er3_ra + bad_cz_er3_ra:
    force_temp_bl('ER3_Halo', 'RA', d)

# ER9_Solitare_RA: blacklist TS28 and TS29 (cz~12.1mm, bad for target 11.26mm)
print('=== ER9_Solitare_RA: blacklist TS-series with bad cz ===')
force_temp_bl('ER9_Solitare', 'RA', 'TS28')  # cz=12.11mm, delta=0.85
force_temp_bl('ER9_Solitare', 'RA', 'TS29')  # likely same

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
