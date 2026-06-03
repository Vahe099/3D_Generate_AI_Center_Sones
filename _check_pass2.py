"""Check current meta for Pass 2 target shapes."""
import json
from pathlib import Path

targets = [
    ('ER3_Halo', 'RA'),
    ('ER9_Solitare', 'RA'),
    ('U12', 'PR'),
    ('U12', 'RA'),
    ('U8', 'PR'),
]

out_dir = Path('synthesis_output')

for family, shape in targets:
    for arch in ('B', 'A'):
        mp = out_dir / f'{family}_{shape}_arch{arch}_meta.json'
        if mp.exists():
            m = json.loads(mp.read_text(encoding='utf-8'))
            verdict = m.get('validation_verdict', '?')
            donor = m.get('donor_family', '?')
            attempt = m.get('attempt', '?')
            checks = m.get('validation_checks', {})
            print(f'{family}_{shape}  donor={donor:<22} attempt={attempt}  verdict={verdict}')
            for k, v in checks.items():
                # meta may store verdict in top-level 'status' (new) or nested 'verdict' (old)
                status = v.get('status') or v.get('verdict') or '?'
                tag = '[FAIL]' if status == 'FAIL' else '[warn]' if status == 'WARN' else '[pass]'
                detail = {kk: vv for kk, vv in v.items() if kk not in ('status', 'verdict')}
                print(f'  {tag} {k:<22} {detail}')
            break
    else:
        print(f'{family}_{shape}  -- no meta found')
    print()
