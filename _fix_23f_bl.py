"""Phase 23F: blacklist current WARN donors for all 8 targets.

AD_11_RD : AD_10      mutable_loss=14.3%  (strikes=1 -> BL)
AD_13_OV : ER15_Solitare  object_count=1.575  (strikes=1 -> BL)
AD_5_PE  : TS14       basket_depth=0.578  (strikes=1 -> BL)
ER9_Halo_AS : SP3     obj_count=0.593 + contam=30.5%  (fresh -> BL)
GS_62_PE : ER6_Solitare  mutable_loss=22.2%  (already BL -- skip)
GS_62_RA : ER1_Solitare  obj_count=1.500 + basket=0.720  (strikes=1 -> BL)
N6_AS    : ER3_Pave_H_Shank  obj_count=0.613 + basket=0.672  (strikes=1 -> BL)
TS35_PE  : TS5        obj_count=0.630 + basket=0.748  (fresh -> BL)
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')


def force_bl(family, shape, donor, checks_failed, note='manual_23f'):
    key = '%s|%s|%s' % (family, shape, donor)
    e = bl.get(key, {'strikes': 0, 'history': []})
    needed = max(0, 2 - e['strikes'])
    if needed == 0:
        print('  %s  already BL (strikes=%d)' % (key, e['strikes']))
        return
    for _ in range(needed):
        e['history'].append({'timestamp': ts, 'rejection_point': note,
                             'validator_verdict': 'WARN', 'checks_failed': checks_failed})
    e['strikes'] = 2
    e['status'] = 'temporary_blacklist'
    e['last_strike'] = ts
    bl[key] = e
    print('  %s  -> BL' % key)


force_bl('AD_11',    'RD', 'AD_10',            ['mutable_loss'])
force_bl('AD_13',    'OV', 'ER15_Solitare',    ['object_count'])
force_bl('AD_5',     'PE', 'TS14',             ['basket_depth'])
force_bl('ER9_Halo', 'AS', 'SP3',              ['object_count', 'contaminant_rate'])
force_bl('GS_62',    'PE', 'ER6_Solitare',     ['mutable_loss'])   # already BL, no-op
force_bl('GS_62',    'RA', 'ER1_Solitare',     ['object_count', 'basket_depth'])
force_bl('N6',       'AS', 'ER3_Pave_H_Shank', ['object_count', 'basket_depth'])
force_bl('TS35',     'PE', 'TS5',              ['object_count', 'basket_depth'])

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
