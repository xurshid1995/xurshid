import sys
files = [
    ('d:/hisobot/Xurshid/templates/sales.html', 8),
    ('d:/hisobot/Xurshid/templates/sales-history.html', 4),
]
for fpath, sp in files:
    indent = ' ' * sp
    with open(fpath, encoding='utf-8') as f:
        content = f.read()
    # Build old string using raw characters
    bs = chr(92)  # backslash
    old_ret = indent + f"return num.toFixed(5).replace(/({bs}.{bs}d*?)0+$/, '$1').replace(/{bs}.$/, '').replace(/{bs}B(?=({bs}d{{3}})+(?!{bs}d))/g, ' ');"
    if old_ret not in content:
        print(f"NOT FOUND: {fpath}")
        idx = content.find("return num.toFixed(5)")
        if idx>=0: print("  actual:", repr(content[idx:idx+130]))
        continue
    new_ret = (indent + f"let formatted = num.toFixed(5).replace(/({bs}.{bs}d*?)0+$/, '$1').replace(/{bs}.$/, '');\n"
               + indent + "const parts = formatted.split('.');\n"
               + indent + f"parts[0] = parts[0].replace(/{bs}B(?=({bs}d{{3}})+(?!{bs}d))/g, ' ');\n"
               + indent + "return parts.join('.');")
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content.replace(old_ret, new_ret, 1))
    print(f"FIXED: {fpath}")
