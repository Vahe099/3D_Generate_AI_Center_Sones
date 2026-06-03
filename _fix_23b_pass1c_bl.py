"""
Phase 23B Pass 1c: blacklist current WARN-producing donors to force exploration.
- AD_10  for AD_11_PE  (mutable_loss=13.6%: ArtDeco donor shares static geometry)
- TS32   for TS35_PE   (count=23/38.1 + basket=14.71/19.96: too small)
- TS18   for TS31_PE   (count=20/14: 15 candidates still unexplored)
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
            'rejection_point': 'manual_23b_pass1c',
            'validator_verdict': 'WARN',
            'checks_failed': ['phase_23b_pass1c_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

force_temp_bl('AD_11', 'PE', 'AD_10')
force_temp_bl('TS35',  'PE', 'TS32')
force_temp_bl('TS31',  'PE', 'TS18')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
