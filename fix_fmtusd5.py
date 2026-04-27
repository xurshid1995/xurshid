import re

OLD = "        return num.toFixed(5).replace(/(\\.\\.d*?)0+$/, '$1').replace(/\\.$/, '').replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');"
NEW = """        let formatted = num.toFixed(5).replace(/(\\.\\.d*?)0+$/, '$1').replace(/\\.$/, '');
        const parts = formatted.split('.');
        parts[0] = parts[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
        return parts.join('.');"""

# Actual content from repr output:
OLD_ACTUAL = "        return num.toFixed(5).replace(/(\\.\\.d*?)0+$/, '$1').replace(/\\.$/, '').replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');"

files = [
    ('templates/sales.html', '    '),   # 4-space indent
    ('templates/sales-history.html', ''),  # no indent
]

for fpath, indent in files:
    with open(fpath, encoding='utf-8') as f:
        content = f.read()

    # Find exact old return statement
    # From repr: return num.toFixed(5).replace(/(\.\\d*?)0+$/, '$1').replace(/\.$/, '').replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
    old_line = indent + "        return num.toFixed(5).replace(/(\\." + "\\d*?)0+$/, '$1').replace(/\\.$/, '').replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');"
    new_lines = (indent + "        let formatted = num.toFixed(5).replace(/(\\." + "\\d*?)0+$/, '$1').replace(/\\.$/, '');\n" +
                 indent + "        const parts = formatted.split('.');\n" +
                 indent + "        parts[0] = parts[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');\n" +
                 indent + "        return parts.join('.');")
    
    if old_line in content:
        content2 = content.replace(old_line, new_lines, 1)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content2)
        print(f"FIXED: {fpath}")
    else:
        print(f"NOT FOUND: {fpath}")
        # Debug: show actual line
        idx = content.find("function fmtUSD5")
        print(f"  actual: {repr(content[idx:idx+250])}")
