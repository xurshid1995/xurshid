#!/usr/bin/env python3

"""
Fix customer name and phone display in sales history
Removes '👤 Noma'lum' and 'Telefon kiritilmagan' for sales without customers
"""

import sys

def fix_customer_display():
    file_path = 'd:/hisobot/Xurshid/app.py'
    
    # Read file with UTF-8 encoding
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Old code to replace
    old_code = """        # Mijoz nomini aniqlash
        if self.customer:
            customer_name = self.customer.name
        elif self.customer_id is None:
            customer_name = '👤 Noma\\'lum'  # Mijoz tanlanmagan
        else:
            customer_name = '🚫 O\\'chirilgan mijoz'  # Mijoz o'chirilgan

        result = {
            'id': self.id,
            'customer_id': self.customer_id,
            'customer_name': customer_name,
            'customer_phone': self.customer.phone if self.customer and self.customer.phone else DEFAULT_PHONE_PLACEHOLDER,"""
    
    # New code
    new_code = """        # Mijoz nomini va telefon raqamini aniqlash
        if self.customer:
            # Mijoz mavjud
            customer_name = self.customer.name
            customer_phone = self.customer.phone if self.customer.phone else DEFAULT_PHONE_PLACEHOLDER
        elif self.customer_id is None:
            # Mijoz tanlanmagan (naqd savdo)
            customer_name = ''  # Bo'sh qoldirish
            customer_phone = ''  # Bo'sh qoldirish
        else:
            # Mijoz o'chirilgan
            customer_name = '🚫 O\\'chirilgan mijoz'
            customer_phone = ''

        result = {
            'id': self.id,
            'customer_id': self.customer_id,
            'customer_name': customer_name,
            'customer_phone': customer_phone,"""
    
    # Check if old code exists
    if old_code in content:
        print("✓ Found old code, replacing...")
        content = content.replace(old_code, new_code)
        
        # Write back
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print("✅ Successfully fixed customer display logic!")
        print("")
        print("Changes:")
        print("  - Mijoz tanlanmagan: '' (bo'sh) instead of '👤 Noma'lum'")
        print("  - Telefon: '' (bo'sh) instead of 'Telefon kiritilmagan'")
        return 0
    else:
        print("❌ Could not find old code to replace")
        return 1

if __name__ == '__main__':
    sys.exit(fix_customer_display())
