# -*- coding: utf-8 -*-
"""Fix ALL corrupted emoji in app.py and debt_scheduler.py using cp1254 reverse mapping"""

# Build cp1254 reverse: Unicode codepoint -> original byte
cp1254_rev = {}
for b in range(0x80, 0x100):
    try:
        char = bytes([b]).decode('cp1254')
        cp1254_rev[ord(char)] = b
    except Exception:
        pass
# C1 control chars that cp1254 can decode but not encode
for b in range(0x80, 0x100):
    if b not in cp1254_rev.values():
        cp1254_rev[b] = b  # identity for undefined bytes


def fix_file(fname):
    with open(fname, 'r', encoding='utf-8') as f:
        content = f.read()

    PREFIX = '\u011f\u0178'  # ğŸ (F0 9F when double-encoded via cp1254)
    result = []
    i = 0
    replacements = 0

    while i < len(content):
        if content[i:i+2] == PREFIX:
            # Try to decode 4-byte emoji (4 original bytes via cp1254 reverse)
            orig_bytes = bytearray()
            j = i
            while j < len(content) and len(orig_bytes) < 4:
                ch = content[j]
                cp = ord(ch)
                if cp < 0x80:
                    b = cp
                elif cp in cp1254_rev:
                    b = cp1254_rev[cp]
                elif cp <= 0xFF:
                    b = cp
                else:
                    break
                orig_bytes.append(b)
                j += 1

            # Check if we got a valid 4-byte UTF-8 emoji
            if len(orig_bytes) == 4:
                try:
                    emoji = orig_bytes.decode('utf-8')
                    if len(emoji) == 1:
                        result.append(emoji)
                        i = j
                        replacements += 1
                        continue
                except UnicodeDecodeError:
                    pass

            # Not a valid emoji, keep original char
            result.append(content[i])
            i += 1
        else:
            result.append(content[i])
            i += 1

    fixed = ''.join(result)
    if replacements > 0:
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(fixed)
        print(f'{fname}: {replacements} emoji fixed, saved OK')
    else:
        print(f'{fname}: no changes needed')

    remaining = fixed.count('\u011f\u0178')
    if remaining:
        print(f'  WARNING: {remaining} ğŸ sequences still remain')


fix_file('app.py')
fix_file('debt_scheduler.py')
