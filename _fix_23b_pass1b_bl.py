"""
Phase 23B Pass 1b additional blacklists.
- TS9  for TS31_RD (20 objects still too many; need ≤19)
- AD_7 for AD_11_PE (mutable_loss=11.1%; try next 1.538-group donor)
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
            'rejection_point': 'manual_23b_pass1b',
            'validator_verdict': 'WARN',
            'checks_failed': ['phase_23b_pass1b_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

force_temp_bl('TS31',  'RD', 'TS9')
force_temp_bl('AD_11', 'PE', 'AD_7')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
