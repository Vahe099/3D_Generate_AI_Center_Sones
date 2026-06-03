import json
from pathlib import Path

out_dir = Path('synthesis_output')
results = {}

for mp in out_dir.glob('*_meta.json'):
    stem = mp.stem
    parts = stem.split('_arch')
    if len(parts) != 2:
        continue
    fam_shape = parts[0]
    arch = parts[1].replace('_meta', '')

    try:
        meta = json.loads(mp.read_text(encoding='utf-8'))
    except Exception:
        continue

    verdict = meta.get('validation_verdict', 'UNKNOWN')
    donor = meta.get('donor_family', '?')

    key = fam_shape
    if key not in results:
        results[key] = (arch, verdict, donor)
    else:
        prev_arch = results[key][0]
        if arch == 'B' and prev_arch == 'A':
            results[key] = (arch, verdict, donor)

counts = {'PASS': 0, 'WARN': 0, 'FAIL': 0}
by_verdict = {'PASS': [], 'WARN': [], 'FAIL': []}

for key, (arch, verdict, donor) in sorted(results.items()):
    v = verdict if verdict in counts else 'FAIL'
    counts[v] += 1
    by_verdict[v].append((key, arch, donor))

print('=== AFTER-STATE COUNTS ===')
print('PASS:', counts['PASS'])
print('WARN:', counts['WARN'])
print('FAIL:', counts['FAIL'])
print('TOTAL:', sum(counts.values()))
print()
print('PASS shapes:')
for s, a, d in by_verdict['PASS']:
    print('  arch%s  %-30s  donor=%s' % (a, s, d))
print()
print('FAIL shapes:')
for s, a, d in by_verdict['FAIL']:
    print('  arch%s  %-30s  donor=%s' % (a, s, d))
