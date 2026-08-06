#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Barcha template fayllaridagi #f5f6fa fon rangini transparent ga o'zgartirish
UTF-8 encoding saqlanadi
"""

import os
from pathlib import Path

templates_dir = Path("templates")

# O'zgartirishlar soni
total_changes = 0

for template_file in templates_dir.glob("*.html"):
    try:
        # UTF-8 da o'qish
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # O'zgartirish
        new_content = content.replace('background: #f5f6fa;', 'background: transparent;')
        
        # Agar o'zgarish bo'lgan bo'lsa, faylga yozish
        if new_content != content:
            with open(template_file, 'w', encoding='utf-8', newline='\n') as f:
                f.write(new_content)
            changes = content.count('background: #f5f6fa;')
            total_changes += changes
            print(f"✅ {template_file.name}: {changes} o'zgarish")
    
    except Exception as e:
        print(f"❌ {template_file.name}: {e}")

print(f"\n📊 Jami: {total_changes} o'zgarish")
