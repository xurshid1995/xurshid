with open('d:/hisobot/Xurshid/app.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')
marker = "Frontend'dan ortacha narx va asl narx keladi"
found = [(i+1, line) for i, line in enumerate(lines) if marker in line]
print("Jami:", len(found))
for ln, text in found:
    print(ln, ":", repr(text))
