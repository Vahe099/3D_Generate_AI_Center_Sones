"""Phase 23B Pass 1f: force past WARN-producing donors."""
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
            'rejection_point': 'manual_23b_pass1f',
            'validator_verdict': 'WARN',
            'checks_failed': ['phase_23b_pass1f_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

# AD_11_PE: TS23 gives contaminant_rate=10.0% (2/20 objects, WARN/FAIL boundary).
# All ArtDeco-adjacent donors share 2 static-hash objects with AD_11.
# ER1_Solitare and ER13_Solitare (both at strikes=1) share no ArtDeco geometry -> 0 contaminants expected.
force_temp_bl('AD_11', 'PE', 'TS23')

# TS31_PE: N4 gives count=10/14=0.714 (below 0.75 PASS threshold, WARN).
# Remaining pool: AD_2 (count=15, ratio=1.071), N1 (count=14, ratio=1.000),
# ER8_Solitare (count=16, ratio=1.143) -- all within PASS zone.
force_temp_bl('TS31', 'PE', 'N4')

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
