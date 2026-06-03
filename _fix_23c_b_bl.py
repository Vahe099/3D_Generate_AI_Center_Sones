"""Phase 23C batch 2: blacklist current WARN-producing donors.

AD_9_RD   : ER5_Halo restored (safety guard). Current BL leaders: ER8_Halo(strikes=1), TS2(strikes=1).
            ER7_Pave_H_Shank has RD count=184 (ratio=1.07 PASS zone) -- should be next valid candidate.
Warren_RD : ER6_Pave_H_Shank mutable_loss=12.5% (2/16 filtered). Try AD_2/TM9/ER8_Solitare (count=16).
AD_1_PE   : TS18 contaminant_rate=10.0%. Try non-TS donors.
AD_1_RD   : TS20 contaminant_rate=5.0% (1 contaminant). Try donors with 0 contaminants.
AD_13_RD  : TS20 count=19/13.3=1.425 (barely above 1.40) + contam=5.0%. Same TS20 culprit.
ER9_Solitare_AS: AD_8 basket=12.503/18.084=0.691 (WARN). Need basket>13.563mm.
AD_1_OV   : TS30 affine_sanity translate_z=3.5615mm (barely above 3.5mm PASS cutoff). Try smaller-tz donor.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, checks_failed, note='manual_23c_b'):
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
            'checks_failed': checks_failed,
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

print('=== AD_9_RD: clear path for ER7_Pave_H_Shank (count=184, ratio=1.07) ===')
force_temp_bl('AD_9', 'RD', 'ER8_Halo', ['object_count'])
force_temp_bl('AD_9', 'RD', 'TS2',      ['object_count'])

print('=== Warren_RD: ER6_Pave_H_Shank mutable_loss=12.5% ===')
force_temp_bl('Warren', 'RD', 'ER6_Pave_H_Shank', ['mutable_loss'])

print('=== AD_1_PE: TS18 contaminant_rate=10.0% ===')
force_temp_bl('AD_1', 'PE', 'TS18', ['contaminant_rate'])

print('=== AD_1_RD + AD_13_RD: TS20 contaminant_rate ===')
force_temp_bl('AD_1',  'RD', 'TS20', ['contaminant_rate'])
force_temp_bl('AD_13', 'RD', 'TS20', ['object_count', 'contaminant_rate'])

print('=== ER9_Solitare_AS: AD_8 basket=0.691 WARN ===')
force_temp_bl('ER9_Solitare', 'AS', 'AD_8', ['basket_depth'])

print('=== AD_1_OV: TS30 affine_sanity tz=3.5615mm ===')
force_temp_bl('AD_1', 'OV', 'TS30', ['affine_sanity'])

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
