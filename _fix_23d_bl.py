"""Phase 23D: blacklist current WARN-producing donors to force fresh candidates.

AD_9_RD   : ER7_Pave_H_Shank mutable_loss=17.4% (32/184 filtered). Try V5 (count=188).
Warren_AS : ER6_Pave_H_Shank mutable_loss=15.0% (3/20 filtered). Try next clean donor.
Warren_RD : ER1_Solitare object_count=0.606 (10/16.5, attempt=5). Try AD_2/ER8_Solitare/TM9 (all 16 objs).
AD_1_PE   : N1 object_count=0.639 + contaminant=7.1%. strikes=1 already -- push to BL.
AD_13_RD  : TS9 contaminant_rate=10%. strikes=1 -- push to BL. Try non-TS donor.
AD_1_RD   : TS9 contaminant_rate=10%. strikes=1 -- push to BL.
ER9_Solitare_AS: TS30 basket_depth=0.71 (12.832/18.084). Not yet BL. Push to BL.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')


def force_temp_bl(family, shape, donor, checks_failed, note='manual_23d'):
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


print('=== AD_9_RD: ER7_Pave_H_Shank mutable_loss=17.4% ===')
force_temp_bl('AD_9', 'RD', 'ER7_Pave_H_Shank', ['mutable_loss'])

print('=== Warren_AS: ER6_Pave_H_Shank mutable_loss=15.0% ===')
force_temp_bl('Warren', 'AS', 'ER6_Pave_H_Shank', ['mutable_loss'])

print('=== Warren_RD: ER1_Solitare object_count=0.606 ===')
force_temp_bl('Warren', 'RD', 'ER1_Solitare', ['object_count'])

print('=== AD_1_PE: N1 object_count+contaminant ===')
force_temp_bl('AD_1', 'PE', 'N1', ['object_count', 'contaminant_rate'])

print('=== AD_13_RD: TS9 contaminant_rate=10% ===')
force_temp_bl('AD_13', 'RD', 'TS9', ['contaminant_rate'])

print('=== AD_1_RD: TS9 contaminant_rate=10% ===')
force_temp_bl('AD_1', 'RD', 'TS9', ['contaminant_rate'])

print('=== ER9_Solitare_AS: TS30 basket_depth=0.71 ===')
force_temp_bl('ER9_Solitare', 'AS', 'TS30', ['basket_depth'])

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
