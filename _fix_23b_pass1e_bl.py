"""Phase 23B Pass 1e: force past final WARN-producing donors."""
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
            'rejection_point': 'manual_23b_pass1e',
            'validator_verdict': 'WARN',
            'checks_failed': ['phase_23b_pass1e_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

# AD_11_PE: TS20 gives contaminant=9.1% (2 of 22), same as all ArtDeco-adjacent donors
# ER1/ER13_Solitare (strikes=1) have no shared geometry -> should give 0 contaminants
force_temp_bl('AD_11', 'PE', 'TS20')

# TS31_PE: TS9 still gives count=22 (ratio=1.571 WARN); N1(14) and AD_2(15) are next
force_temp_bl('TS31', 'PE', 'TS9')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
