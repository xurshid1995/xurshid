
with open(r'd:\hisobot\Xurshid\app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

OLD = "@role_required('admin', 'kassir', 'sotuvchi')"
NEW = "@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')"

# Target line numbers from grep (1-indexed); check +-2 for safety
targets = [5770, 5937, 5970, 6931, 6978, 7024, 7099,
           8811, 8889, 8961, 9034, 9094, 9134, 9166,
           9601, 9899, 9985, 10080, 10274, 10341]

updated = []
not_found = []

for t in targets:
    found = False
    for delta in [0, -1, 1, -2, 2]:
        idx = t - 1 + delta
        if 0 <= idx < len(lines) and OLD in lines[idx]:
            lines[idx] = lines[idx].replace(OLD, NEW)
            updated.append(t)
            print(f'[OK] Line {t} (delta {delta:+d}): updated')
            found = True
            break
    if not found:
        not_found.append(t)
        print(f'[WARN] Line {t}: NOT MATCHED -> {lines[t-1].strip()}')

print(f'\nTotal updated: {len(updated)}, Not found: {len(not_found)}')

with open(r'd:\hisobot\Xurshid\app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('File saved successfully.')
