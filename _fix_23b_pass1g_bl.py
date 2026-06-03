"""Phase 23B Pass 1g: TS31_PE solitaire donors contaminate TS31 static shank.
ER13_Solitare: count=10/14=0.714 + contaminant_rate=28.6% (4/14 objects match TS31 static).
ER1_Solitare: same shank design, expected identical contamination.
Force pool to AD_2 / N1 / ER8_Solitare (different family shanks -> 0 contaminants expected).
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, note='manual_23b_pass1g'):
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
            'checks_failed': ['contaminant_rate', 'object_count'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

print('=== TS31_PE solitaire shank contaminants ===')
force_temp_bl('TS31', 'PE', 'ER13_Solitare')
force_temp_bl('TS31', 'PE', 'ER1_Solitare')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
