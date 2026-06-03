"""Phase 23B Pass 2b: force ER3_Halo_RA and ER9_Solitare_RA to try stone_cz-matched donors.

ER3_Halo_RA (target cz=13.18mm):
  TS28 (cz=12.11mm, delta=1.07) -> affine_sanity WARN. scores high on count (17/13=1.31)
  TS29 (cz=~12.1mm) -> likely same WARN.
  Blacklist both to let N1 (cz=13.15mm, delta=0.03) or AD_2 (cz=13.25mm, delta=0.07) be tried.
  These give translate_z ~0 (exp=13.18, donor_cz~13.2, scale_z~1.0 -> tz~0).

ER9_Solitare_RA (target cz=11.26mm):
  TS27 (cz=12.11mm, delta=0.85) -> affine_sanity WARN. TS20 already blacklisted.
  Blacklist TS27 to let U8 (cz=11.31mm, delta=0.05) or V3/SP5/SP6 be tried.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, note='manual_23b_pass2b'):
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
            'checks_failed': ['affine_sanity'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

print('=== ER3_Halo_RA: blacklist TS-series with bad cz ===')
force_temp_bl('ER3_Halo', 'RA', 'TS28')  # cz=12.11mm, delta=1.07
force_temp_bl('ER3_Halo', 'RA', 'TS29')  # cz likely ~12.1mm, similar WARN

print('=== ER9_Solitare_RA: blacklist TS27 ===')
force_temp_bl('ER9_Solitare', 'RA', 'TS27')  # cz=12.11mm, delta=0.85

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
