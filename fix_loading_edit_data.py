#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tahrirlash yuklanayotganda stock operatsiyalarini skip qilish uchun fix
"""

def fix_sales_html():
    file_path = r"d:\Sayt 2025\templates\sales.html"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # removeFromCart funksiyasida fix qilish (line 4540 atrofida)
    modified = False
    for i in range(len(lines)):
        # "ODDIY MANTIQ" qatorini topish
        if '// ✅ ODDIY MANTIQ: Har doim stock qaytarish' in lines[i]:
            print(f"Topildi line {i+1}: {lines[i].strip()}")
            
            # Keyingi qatorlarni o'zgartirish
            # 4540: comment
            # 4541: comment  
            # 4542: console.log
            # 4543: blank
            # 4544: try {
            
            # Yangi kod blokirovka qo'shish
            new_code = """            
            // Tahrirlash yuklanayotganda stock operatsiyalarini skip qilish
            if (tabData.isLoadingEditData) {
                console.log('Tahrirlash yuklanmoqda - stock qaytarish skip qilindi');
                stockReturnSuccess = true; // Skip qilindi deb belgilash
            } else {
                // ODDIY MANTIQ: Har doim stock qaytarish
                // Korzinadan olib tashlanganda har doim stock qaytariladi
                console.log('Mahsulot korzinadan olib tashlanmoqda - stock qaytarish:', currentQuantity, 'ta');
                
                try {
                    stockReturnSuccess = await addStock(
                        itemToRemove.id,
                        currentQuantity,
                        itemToRemove.location_id,
                        itemToRemove.location_type
                    );
                    
                    if (stockReturnSuccess) {
                        console.log('Stock muvaffaqiyatli qaytarildi:', currentQuantity, 'ta');
                        showAlert('Mahsulot uchun ' + currentQuantity + ' ta stock qaytarildi!', 'success');
                    } else {
                        console.log('Stock qaytarishda muammo');
                    }
                } catch (error) {
                    console.error('Stock qaytarishda xatolik:', error);
                    stockReturnSuccess = false;
                }
            }
"""
            
            # Eski kodlarni o'chirish (11 ta qator: comment, comment, console, blank, try va ichidagi kodlar)
            # 4540-4562 gacha
            end_line = i
            brace_count = 0
            for j in range(i, min(i+30, len(lines))):
                if 'delete tabData.originalQuantities[productId];' in lines[j]:
                    end_line = j
                    break
            
            print(f"O'chirish: line {i+1} dan {end_line+1} gacha")
            
            # Yangi kodni qo'yish
            lines[i:end_line] = [new_code + '\n']
            modified = True
            break
    
    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print("✅ sales.html muvaffaqiyatli o'zgartirildi!")
    else:
        print("❌ O'zgartirish kerak bo'lgan joy topilmadi")

if __name__ == '__main__':
    fix_sales_html()
