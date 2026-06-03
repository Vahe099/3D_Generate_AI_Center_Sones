"""Check current meta detail for Phase 23C target shapes."""
import json
from pathlib import Path

targets = [
    ('AD_9', 'OV'), ('AD_9', 'PE'), ('AD_9', 'RD'),
    ('Warren', 'PR'), ('Warren', 'RD'),
    ('AD_1', 'PE'), ('TS22', 'PE'),
    ('AD_13', 'PE'), ('AD_13', 'RD'),
    ('ER9_Solitare', 'AS'),
    ('AD_1', 'OV'), ('AD_1', 'RD'),
]

out_dir = Path('synthesis_output')

for family, shape in targets:
    for arch in ('B', 'A'):
        mp = out_dir / f'{family}_{shape}_arch{arch}_meta.json'
        if mp.exists():
            m = json.loads(mp.read_text(encoding='utf-8'))
            verdict = m.get('validation_verdict', '?')
            donor = m.get('donor_family', '?')
            checks = m.get('validation_checks', {})
            print(f'{family}_{shape}  donor={donor:<22} verdict={verdict}')
            for k, v in checks.items():
                status = v.get('status') or v.get('verdict') or '?'
                if status in ('WARN', 'FAIL'):
                    tag = '[FAIL]' if status == 'FAIL' else '[warn]'
                    detail = {kk: vv for kk, vv in v.items() if kk not in ('status', 'verdict')}
                    print(f'  {tag} {k:<22} {detail}')
            break
    else:
        print(f'{family}_{shape}  -- no meta')
    print()
