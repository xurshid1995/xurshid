import sys

def fix(path):
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    
    in_fmt = False
    result = []
    fixed = 0
    for i in range(len(lines)):
        line = lines[i]
        if 'function fmtUSD5' in line:
            in_fmt = True
        if in_fmt and 'return num.toFixed(5).replace' in line and '\\B' in line:
            # Get indentation
            indent = line[:len(line) - len(line.lstrip())]
            # Build replacement (same regex but split on dot)
            p1 = line.rstrip().replace('return num.', 'let formatted = num.')
            # Remove last .replace(/\\B.../, ' ') part
            idx_b = p1.rfind('.replace(/\\B')
            if idx_b >= 0:
                p1 = p1[:idx_b] + ';'
            result.append(p1 + '\n')
            result.append(indent + "const parts = formatted.split('.');\n")
            result.append(indent + "parts[0] = parts[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');\n")
            result.append(indent + "return parts.join('.');\n")
            fixed += 1
            in_fmt = False
            continue
        result.append(line)
    
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(result)
    print(f"{{fixed}} line(s) fixed in {path}")

fix('d:/hisobot/Xurshid/templates/sales.html')
fix('d:/hisobot/Xurshid/templates/sales-history.html')
