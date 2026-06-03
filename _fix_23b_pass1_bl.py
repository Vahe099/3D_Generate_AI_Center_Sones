"""
Phase 23B Pass 1 blacklist fix.
stone_ar group: TM9 for AD_11_PE, TS33 for TS35_PE
object_count group: TS30 for TS31_PE, TS20 for TS31_RD
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor):
    key = '%s|%s|%s' % (family, shape, donor)
    entry = bl.get(key, {'strikes': 0, 'history': []})
    needed = max(0, 2 - entry['strikes'])
    if needed == 0:
        print('  %s  already at strikes=%d (%s)' % (key, entry['strikes'], entry.get('status', '?')))
        return
    for _ in range(needed):
        entry['history'].append({
            'timestamp': ts,
            'rejection_point': 'manual_23b_pass1',
            'validator_verdict': 'WARN',
            'checks_failed': ['phase_23b_pass1_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

print('=== stone_ar donors ===')
force_temp_bl('AD_11', 'PE', 'TM9')
force_temp_bl('TS35',  'PE', 'TS33')

print('=== object_count donors ===')
force_temp_bl('TS31', 'PE', 'TS30')
force_temp_bl('TS31', 'RD', 'TS20')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
