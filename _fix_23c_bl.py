"""Phase 23C: blacklist current WARN-producing donors for all target shapes.

AD_9_OV/PE/RD : ER5_Halo gives 118-129 objects (expected 172.3, ratio 0.685-0.749 WARN).
  Next tier: ER7_Pave_H_Shank (PE=199, OV=181, RD=184) and V5 (PE=193, OV/RD=188)
  both in PASS zone (ratio 1.04-1.15). V4/V7 are pre-rejected (ratio>>1.65).

Warren_PR  : ER2_Solitare count=11 (ratio=0.667 WARN). Need >=13 objects.
  Fresh candidates with count 13-20: ER8_Solitare(16), TS34(16), TM9(17), AD_10(19).

Warren_RD  : ER5_Hidden count=25 (ratio=1.515 WARN). Need <=23 objects.
  Fresh candidates with count 13-20: AD_2(16), ER6_Pave_H_Shank(16), TM9(16), AD_7(17).

AD_1_PE    : TS30 contaminant_rate=8.7% (2/23 objects). Need non-contaminating donor.
TS22_PE    : AD_3 contaminant_rate=13.9% (5/36 objects). ArtDeco donor on TS22 target.
AD_13_PE   : TS30 count=21/13.3=1.575 + contam=8.7%. Need count<=18 + 0 contaminants.
AD_13_RD   : TS30 count=20/13.3=1.500 + contam=9.1%. Same.
ER9_Solitare_AS: TS20 basket=13.492mm (need>=13.563mm, ratio=0.746 vs PASS=0.75). Very close.
AD_1_OV    : N1 count=12/20.3=0.590 + affine_sanity tz=-4.452mm. Need >=16 objects.
AD_1_RD    : AD_7 count=15/20.3=0.738 + mutable_loss=11.8%. Need >=16 + 0 filtered.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, checks_failed, note='manual_23c'):
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

print('=== AD_9 OV/PE/RD: ER5_Halo insufficient count ===')
for shape in ('OV', 'PE', 'RD'):
    force_temp_bl('AD_9', shape, 'ER5_Halo', ['object_count'])

print('=== Warren_PR/RD ===')
force_temp_bl('Warren', 'PR', 'ER2_Solitare', ['object_count'])
force_temp_bl('Warren', 'RD', 'ER5_Hidden',   ['object_count'])

print('=== Contaminant_rate group ===')
force_temp_bl('AD_1',  'PE', 'TS30', ['contaminant_rate'])
force_temp_bl('TS22',  'PE', 'AD_3', ['contaminant_rate'])
force_temp_bl('AD_13', 'PE', 'TS30', ['object_count', 'contaminant_rate'])
force_temp_bl('AD_13', 'RD', 'TS30', ['object_count', 'contaminant_rate'])

print('=== ER9_Solitare_AS: basket just below threshold ===')
force_temp_bl('ER9_Solitare', 'AS', 'TS20', ['basket_depth'])

print('=== AD_1 OV/RD: count + secondary WARNs ===')
force_temp_bl('AD_1', 'OV', 'N1',   ['object_count', 'affine_sanity'])
force_temp_bl('AD_1', 'RD', 'AD_7', ['object_count', 'mutable_loss'])

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
