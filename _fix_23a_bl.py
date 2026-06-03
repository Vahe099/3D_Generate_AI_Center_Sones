"""
Phase 23A blacklist fix.
Forces 2 strikes (temporary_blacklist) on the WARN-producing donor for each
contaminant_rate and mutable_loss target shape so --retry-warn skips them.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor):
    key = f'{family}|{shape}|{donor}'
    entry = bl.get(key, {'strikes': 0, 'history': []})
    needed = max(0, 2 - entry['strikes'])
    if needed == 0:
        print(f'  {key}  already at strikes={entry["strikes"]} ({entry.get("status","?")})')
        return
    for _ in range(needed):
        entry['history'].append({
            'timestamp': ts,
            'rejection_point': 'manual_23a',
            'validator_verdict': 'WARN',
            'checks_failed': ['phase_23a_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print(f'  {key}  -> strikes=2 temporary_blacklist')

# contaminant_rate donors
print('=== contaminant_rate ===')
force_temp_bl('AD_12',  'PE', 'TS11')
force_temp_bl('AD_12',  'RD', 'TS11')
force_temp_bl('TS22',   'PE', 'TS11')
force_temp_bl('AD_6',   'OV', 'AD_3')
force_temp_bl('AD_11',  'RD', 'AD_8')
force_temp_bl('AD_13',  'PE', 'TS23')
force_temp_bl('AD_13',  'RD', 'AD_8')

# mutable_loss donors
print('=== mutable_loss ===')
force_temp_bl('Warren', 'AS', 'ER6_Hidden')
force_temp_bl('Warren', 'PE', 'ER6_Pave_H_Shank')
force_temp_bl('Warren', 'RD', 'ER6_Hidden')
force_temp_bl('AD_1',   'PE', 'AD_7')
force_temp_bl('GS_62',  'PE', 'ER6_Solitare')
force_temp_bl('TS21',   'PR', 'N1')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
