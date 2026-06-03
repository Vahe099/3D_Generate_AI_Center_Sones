"""
Phase 23B Pass 1d comprehensive blacklist:

AD_11_PE: blacklist TS30 (contaminant=8.7%). ER1/ER13_Solitare next.
TS31_PE : blacklist all high-count PE donors (count>19) so N1/AD_2/N4/ER8_Solitare
          are reached. Count ratio>1.357 will stay WARN, so remove them from pool.
TS35_PE : clear TS19 and TS8 strikes. TS8 will be pre-rejected by PR-4
          (stone_ar=10.886). TS19 should PASS (count=49/38.1=1.286, basket=0.973,
          stone_ar=1.538 -- previous WARN was pre-Fix-A stone_cz error).
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, note='manual_23b_pass1d'):
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
            'checks_failed': ['phase_23b_pass1d_force'],
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

def clear_bl(family, shape, donor):
    key = '%s|%s|%s' % (family, shape, donor)
    if key in bl:
        del bl[key]
        print('  %s  -> cleared' % key)
    else:
        print('  %s  (not in blacklist)' % key)

# AD_11_PE: blacklist current WARN donor
print('=== AD_11_PE ===')
force_temp_bl('AD_11', 'PE', 'TS30')

# TS35_PE: clear TS19 (pre-Fix-A WARN, should PASS now), clear TS8 (PR-4 will reject)
print('=== TS35_PE ===')
clear_bl('TS35', 'PE', 'TS19')
clear_bl('TS35', 'PE', 'TS8')

# TS31_PE: blacklist all donors with PE count > 19 (ratio > 1.357 -> WARN zone)
# These score higher than good candidates due to stone_cz proximity in scorer
# but produce object_count WARN. Force the pool to low-count donors.
print('=== TS31_PE high-count donors ===')
high_count_pe = [
    'TS13', 'TS15', 'TS20', 'TS23', 'TS27', 'TS28', 'TS29', 'TS7',
    'AD_8', 'U4', 'ER6_Hidden', 'ER6_Pave_H_Shank', 'SP6',
]
for donor in high_count_pe:
    force_temp_bl('TS31', 'PE', donor)

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
