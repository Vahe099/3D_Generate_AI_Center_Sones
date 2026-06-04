"""Phase 23G: blacklist current WARN donors for all targets.

AD_11_RD       : AD_7           mutable_loss=11.8%
ER3_Halo_AS    : TM8            basket_depth=0.657
ER3_Halo_PE    : ER4_Solitare   basket_depth=0.608
ER3_Halo_PR    : ER13_Solitare  basket_depth=0.567
ER4_Halo_AS    : AD_8           basket_depth=0.699
ER4_Halo_OV    : TS20           basket_depth=0.689 + contaminant=6.7%
ER4_Halo_RA    : TS27           basket_depth=0.637
ER7_Solitare_PE: N4             basket_depth=0.729 + contaminant=18.2%
N3_PE          : SP2            basket_depth=0.605
U5_PR          : ER1_Solitare   basket_depth=0.679 + affine_sanity
TS21_PR_archB  : AD_7           basket_depth=0.682
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')


def force_bl(family, shape, donor, checks_failed, note='manual_23g'):
    key = '%s|%s|%s' % (family, shape, donor)
    e = bl.get(key, {'strikes': 0, 'history': []})
    needed = max(0, 2 - e['strikes'])
    if needed == 0:
        print('  %s  already BL' % key); return
    for _ in range(needed):
        e['history'].append({'timestamp': ts, 'rejection_point': note,
                             'validator_verdict': 'WARN', 'checks_failed': checks_failed})
    e['strikes'] = 2; e['status'] = 'temporary_blacklist'; e['last_strike'] = ts
    bl[key] = e
    print('  %s  -> BL' % key)


force_bl('AD_11',        'RD', 'AD_7',          ['mutable_loss'])
force_bl('ER3_Halo',     'AS', 'TM8',           ['basket_depth'])
force_bl('ER3_Halo',     'PE', 'ER4_Solitare',  ['basket_depth'])
force_bl('ER3_Halo',     'PR', 'ER13_Solitare', ['basket_depth'])
force_bl('ER4_Halo',     'AS', 'AD_8',          ['basket_depth'])
force_bl('ER4_Halo',     'OV', 'TS20',          ['basket_depth', 'contaminant_rate'])
force_bl('ER4_Halo',     'RA', 'TS27',          ['basket_depth'])
force_bl('ER7_Solitare', 'PE', 'N4',            ['basket_depth', 'contaminant_rate'])
force_bl('N3',           'PE', 'SP2',           ['basket_depth'])
force_bl('U5',           'PR', 'ER1_Solitare',  ['basket_depth', 'affine_sanity'])
force_bl('TS21',         'PR', 'AD_7',          ['basket_depth'])

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
