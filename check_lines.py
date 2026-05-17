"""Line-number based replacement - no string matching needed"""
with open('d:/hisobot/Xurshid/app.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')

total = len(lines)
print(f"Total lines: {total}")

# Verify key lines before replacement
print("=== 1-chi blok (api_add_product) ===")
for i in range(3002, 3017):
    print(f"  {i+1}: {repr(lines[i])}")

print("\n=== 2-chi blok marker ===")
for i, line in enumerate(lines):
    if "Frontend'dan ortacha narx va asl narx keladi" in line:
        print(f"  {i+1}: {repr(line)}")
        for j in range(i, i+10):
            print(f"    {j+1}: {repr(lines[j])}")
        break
