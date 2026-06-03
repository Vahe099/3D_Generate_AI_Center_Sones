"""Find donors with RA stone_cz closest to ER3_Halo (13.18mm) and ER9_Solitare (11.26mm).
Also show the expected translate_z for each if scale_z were 1.0 (best-case approximation).
"""
import json
from pathlib import Path

cache = json.loads(Path('_cf_frame_cache.json').read_text(encoding='utf-8'))

target_er3  = 13.18  # ER3_Halo expected RA stone_cz
target_er9  = 11.26  # ER9_Solitare expected RA stone_cz

# Exclude the target families themselves and already-known-bad donors
exclude_families = {'ER3_Halo', 'ER9_Solitare'}

rows = []
for key, val in cache.items():
    fam, shape = key.split('|', 1)
    if shape != 'RA':
        continue
    if fam in exclude_families:
        continue
    cz = val.get('stone_cz')
    ar = val.get('stone_ar')
    cnt = val.get('count')
    if cz is None:
        continue
    delta3 = abs(cz - target_er3)
    delta9 = abs(cz - target_er9)
    rows.append((fam, cz, ar, cnt, delta3, delta9))

rows.sort(key=lambda r: r[4])  # sort by distance to ER3_Halo target

print(f'{"Donor":<22}  {"RA_cz":>6}  {"AR":>5}  {"cnt":>4}  {"d(er3)":>7}  {"d(er9)":>7}')
print('-' * 65)
for fam, cz, ar, cnt, d3, d9 in rows[:30]:
    print(f'{fam:<22}  {cz:>6.2f}  {ar if ar else "?":>5}  {cnt if cnt else "?":>4}  '
          f'{d3:>7.2f}  {d9:>7.2f}')

print()
print('Top donors for ER9_Solitare (sorted by d_er9):')
rows.sort(key=lambda r: r[5])
print(f'{"Donor":<22}  {"RA_cz":>6}  {"d(er9)":>7}')
print('-' * 40)
for fam, cz, ar, cnt, d3, d9 in rows[:15]:
    print(f'{fam:<22}  {cz:>6.2f}  {d9:>7.2f}')
