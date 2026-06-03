"""Phase 23A report: before/after counts, donor changes, WARN->PASS conversions."""
import json
from pathlib import Path

STANDARD_SHAPES = {'AS', 'OV', 'PE', 'PR', 'RA', 'RD'}

out_dir = Path('synthesis_output')
results = {}

for mp in out_dir.glob('*_meta.json'):
    stem = mp.stem
    parts = stem.split('_arch')
    if len(parts) != 2:
        continue
    fam_shape = parts[0]
    arch = parts[1].replace('_meta', '')

    # extract shape from end of fam_shape
    tokens = fam_shape.rsplit('_', 1)
    if len(tokens) != 2 or tokens[1] not in STANDARD_SHAPES:
        continue
    family, shape = tokens

    try:
        meta = json.loads(mp.read_text(encoding='utf-8'))
    except Exception:
        continue

    verdict = meta.get('validation_verdict', 'UNKNOWN')
    donor = meta.get('donor_family', '?')
    checks = meta.get('validation_checks', {})
    failing = [k for k, v in checks.items() if isinstance(v, dict) and v.get('verdict') not in ('PASS', None)]

    key = fam_shape
    if key not in results:
        results[key] = (arch, verdict, donor, failing)
    else:
        prev_arch = results[key][0]
        if arch == 'B' and prev_arch == 'A':
            results[key] = (arch, verdict, donor, failing)

# Standard-shape counts
counts = {'PASS': 0, 'WARN': 0, 'FAIL': 0}
for key, (arch, verdict, donor, failing) in sorted(results.items()):
    v = verdict if verdict in counts else 'FAIL'
    counts[v] += 1

print('=== PHASE 23A REPORT ===')
print()
print('STANDARD-SHAPE COUNTS (AS/OV/PE/PR/RA/RD only):')
print('  Before (Phase 22): PASS=26  WARN=47  FAIL=1')
print('  After  (Phase 23A): PASS=%d  WARN=%d  FAIL=%d  TOTAL=%d' % (
    counts['PASS'], counts['WARN'], counts['FAIL'], sum(counts.values())))
delta = counts['PASS'] - 26
print('  Delta: PASS %+d' % delta)
print()

# Phase 23A target shapes with their donor and verdict
TARGET_23A = {
    # contaminant_rate group
    'AD_12_PE':  ('TS11', 'contaminant_rate'),
    'AD_12_RD':  ('TS11', 'contaminant_rate'),
    'TS22_PE':   ('TS11', 'contaminant_rate'),
    'AD_6_OV':   ('AD_3', 'contaminant_rate'),
    'AD_11_RD':  ('AD_8', 'contaminant_rate'),
    'AD_13_PE':  ('TS23', 'contaminant_rate'),
    'AD_13_RD':  ('AD_8', 'contaminant_rate'),
    # mutable_loss group
    'Warren_AS': ('ER6_Hidden', 'mutable_loss'),
    'Warren_PE': ('ER6_Pave_H_Shank', 'mutable_loss'),
    'Warren_RD': ('ER6_Hidden', 'mutable_loss'),
    'AD_1_PE':   ('AD_7', 'mutable_loss'),
    'GS_62_PE':  ('ER6_Solitare', 'mutable_loss'),
    'TS21_PR':   ('N1', 'mutable_loss'),
}

print('PHASE 23A TARGET SHAPES:')
print('%-12s %-8s %-22s %-22s %-8s %s' % ('GROUP', 'SHAPE', 'BEFORE DONOR', 'AFTER DONOR', 'VERDICT', 'FAILING CHECKS'))
print('-' * 110)

converted = []
still_warn = []

for key, (before_donor, group) in sorted(TARGET_23A.items(), key=lambda x: (x[1][1], x[0])):
    if key in results:
        arch, verdict, after_donor, failing = results[key]
        status = 'WARN->PASS' if verdict == 'PASS' else ('WARN->WARN' if verdict == 'WARN' else 'WARN->FAIL')
        fail_str = ', '.join(failing) if failing else '-'
        print('%-12s %-8s %-22s %-22s %-8s %s' % (group, key, before_donor, after_donor, verdict, fail_str))
        if verdict == 'PASS':
            converted.append(key)
        else:
            still_warn.append(key)
    else:
        print('%-12s %-8s %-22s %-22s %-8s %s' % (group, key, before_donor, '(no meta)', '?', ''))

print()
print('WARN->PASS conversions (%d): %s' % (len(converted), ', '.join(converted)))
print('Still WARN/FAIL (%d): %s' % (len(still_warn), ', '.join(still_warn)))
