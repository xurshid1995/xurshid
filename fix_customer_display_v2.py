#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix customer display by line replacement"""

file_path = 'd:/hisobot/Xurshid/app.py'

# Read all lines
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and replace
modified = False
i = 0
while i < len(lines):
    line = lines[i]
    
    # Find the comment "# Mijoz nomini aniqlash"
    if '# Mijoz nomini aniqlash' in line:
        print(f"Found at line {i+1}: {line.strip()}")
        print("Replacing next 8 lines...")
        
        # Replace the next 8 lines
        new_lines = [
            "        # Mijoz nomini va telefon raqamini aniqlash\n",
            "        if self.customer:\n",
            "            # Mijoz mavjud\n",
            "            customer_name = self.customer.name\n",
            "            customer_phone = self.customer.phone if self.customer.phone else DEFAULT_PHONE_PLACEHOLDER\n",
            "        elif self.customer_id is None:\n",
            "            # Mijoz tanlanmagan (naqd savdo)\n",
            "            customer_name = ''  # Bo'sh qoldirish\n",
            "            customer_phone = ''  # Bo'sh qoldirish\n",
            "        else:\n",
            "            # Mijoz o'chirilgan\n",
            "            customer_name = '🚫 O\\'chirilgan mijoz'\n",
            "            customer_phone = ''\n",
        ]
        
        # Replace current line and next 7 lines (total 8 lines to 13 lines)
        lines[i:i+8] = new_lines
        modified = True
        break
    
    i += 1

if modified:
    # Also fix the customer_phone line in result dict
    for i, line in enumerate(lines):
        if "'customer_phone': self.customer.phone if self.customer and self.customer.phone else DEFAULT_PHONE_PLACEHOLDER," in line:
            print(f"Found customer_phone at line {i+1}")
            lines[i] = "            'customer_phone': customer_phone,\n"
            print("Fixed customer_phone line")
            break
    
    # Write back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print("\n✅ Successfully fixed customer display!")
    print("\nChanges:")
    print("  - Mijoz tanlanmagan: '' (bo'sh)")
    print("  - Telefon tanlanmagan: '' (bo'sh)")
    print("  - Removed '👤 Noma'lum' for empty customers")
    print("  - Removed 'Telefon kiritilmagan' for empty customers")
else:
    print("❌ Could not find the code to replace")
