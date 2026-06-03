import json
from pathlib import Path

targets = [
    ('AD_11', 'PE', 'A'),
    ('TS35',  'PE', 'A'),
    ('TS31',  'PE', 'A'),
    ('TS31',  'RD', 'A'),
]

out_dir = Path('synthesis_output')
for fam, shape, arch in targets:
    mp = out_dir / ('%s_%s_arch%s_meta.json' % (fam, shape, arch))
    if not mp.exists():
        print('%s_%s: no meta' % (fam, shape))
        continue
    meta = json.loads(mp.read_text(encoding='utf-8'))
    donor = meta.get('donor_family', '?')
    verdict = meta.get('validation_verdict', '?')
    attempt = meta.get('attempt_number', 1)
    checks = meta.get('validation_checks', {})
    print('%s_%s  donor=%-20s  attempt=%d  verdict=%s' % (fam, shape, donor, attempt, verdict))
    for ck, v in checks.items():
        if isinstance(v, dict):
            vd = v.get('verdict', '?')
            marker = 'FAIL' if vd not in ('PASS', None) else 'pass'
            details = {k: val for k, val in v.items() if k != 'verdict'}
            print('  [%s] %-20s  %s' % (marker, ck, details))
    print()
