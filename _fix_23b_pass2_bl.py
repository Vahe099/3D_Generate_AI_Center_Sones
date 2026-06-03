"""Phase 23B Pass 2: blacklist current WARN-producing donors.

ER3_Halo_RA : TS27 -> affine_sanity WARN (translate_z=-6.02mm, |tz|>3.5mm threshold).
ER9_Solitare_RA: TS20 -> affine_sanity WARN (translate_z=-6.33mm). Both need donor
                 with RA stone_cz closer to target expected cz.
U12_PR       : ER10_Solitare -> count=4/7=0.571 WARN + mutable_loss=20% WARN.
U12_RA       : ER10_Solitare -> count=5/7=0.714 WARN. Need donor with 6+ RA objects.
U8_PR        : U11 -> count=6/9.2=0.651 WARN + contaminant_rate=25% (U-series donor
               shares static hash with U8). Need non-U-series donor with 7-12 PR objects.
"""
import json
from pathlib import Path
from datetime import datetime

bl_path = Path('_donor_blacklist.json')
bl = json.loads(bl_path.read_text(encoding='utf-8'))
ts = datetime.now().isoformat(timespec='seconds')

def force_temp_bl(family, shape, donor, checks_failed, note='manual_23b_pass2'):
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
            'checks_failed': checks_failed,
        })
    entry['strikes'] = 2
    entry['status'] = 'temporary_blacklist'
    entry['last_strike'] = ts
    bl[key] = entry
    print('  %s  -> strikes=2 temporary_blacklist' % key)

print('=== ER3_Halo_RA ===')
force_temp_bl('ER3_Halo', 'RA', 'TS27', ['affine_sanity'])

print('=== ER9_Solitare_RA ===')
force_temp_bl('ER9_Solitare', 'RA', 'TS20', ['affine_sanity'])

print('=== U12_PR ===')
force_temp_bl('U12', 'PR', 'ER10_Solitare', ['object_count', 'mutable_loss'])

print('=== U12_RA ===')
force_temp_bl('U12', 'RA', 'ER10_Solitare', ['object_count'])

print('=== U8_PR ===')
force_temp_bl('U8', 'PR', 'U11', ['object_count', 'contaminant_rate'])

bl_path.write_text(json.dumps(bl, indent=2), encoding='utf-8')
print('\nBlacklist updated.')
