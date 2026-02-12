# -*- coding: utf-8 -*-
"""
PDF Generator - Savdo cheklari uchun
"""
import os
import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
import qrcode
from reportlab.lib.utils import ImageReader

def generate_sale_receipt_pdf(
    sale_data: dict,
    output_path: str = None,
    currency: str = 'uzs'  # 'usd' yoki 'uzs'
) -> str:
    """
    Printer formatidagi savdo cheki PDF yaratish
    
    Args:
        sale_data: Savdo ma'lumotlari
        output_path: PDF saqlash yo'li (agar None bo'lsa, temp file yaratiladi)
        currency: Valyuta turi ('usd' yoki 'uzs')
    
    Returns:
        str: PDF fayl yo'li
    """
    if output_path is None:
        output_path = f"/tmp/sale_{sale_data['sale_id']}_{currency}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    # PDF yaratish - 80mm kenglikda (printer chek formati)
    from reportlab.lib.pagesizes import landscape
    # 80mm = 226.77 points, balandlik A4
    page_width = 80 * mm
    page_height = A4[1]  # Uzun sahifa
    
    c = canvas.Canvas(output_path, pagesize=(page_width, page_height))
    
    # Y pozitsiyasi
    y = page_height - 10*mm
    
    # Valyuta belgisi va formatlash
    currency_symbol = "$" if currency == 'usd' else "so'm"
    currency_label = "USD" if currency == 'usd' else "UZS"
    
    # Do'kon nomi (markazda, katta)
    c.setFont("Helvetica-Bold", 14)
    store_name = sale_data.get('location', 'Do\'kon')
    c.drawCentredString(page_width/2, y, store_name)
    y -= 8*mm
    
    # Chiziq
    c.setLineWidth(0.5)
    c.line(5*mm, y, page_width-5*mm, y)
    y -= 5*mm
    
    # Chek ma'lumotlari (bordersiz)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(5*mm, y, f"Chek #{sale_data['sale_id']}")
    c.setFont("Helvetica", 9)
    c.drawRightString(page_width-5*mm, y, f"{sale_data['date']}")
    y -= 5*mm
    
    # Sotuvchi va Mijoz ma'lumotlari (ikki ustunda)
    info_y = y  # Y koordinatasini saqlash
    left_x = 5*mm  # Chap ustun
    right_x = page_width / 2  # O'ng ustun
    
    # Mijoz ma'lumotlari (CHAP tomonda)
    if sale_data.get('customer_name'):
        c.setFont("Helvetica-Bold", 8)
        c.drawString(left_x, info_y, "Mijoz:")
        c.setFont("Helvetica", 8)
        c.drawString(left_x, info_y - 3*mm, sale_data['customer_name'])
        
        # Mijoz telefoni
        if sale_data.get('customer_phone'):
            c.drawString(left_x, info_y - 6*mm, sale_data['customer_phone'])
    
    # Sotuvchi ma'lumotlari (O'NG tomonda)
    if sale_data.get('seller_name'):
        c.setFont("Helvetica-Bold", 8)
        c.drawString(right_x, info_y, "Sotuvchi:")
        c.setFont("Helvetica", 8)
        c.drawString(right_x, info_y - 3*mm, sale_data['seller_name'])
        
        # Sotuvchi telefoni
        if sale_data.get('seller_phone'):
            c.drawString(right_x, info_y - 6*mm, sale_data['seller_phone'])
    
    # Y ni pastga siljitish (maksimal balandlikka qarab)
    y = info_y - 9*mm
    
    y -= 2*mm
    
    # Mahsulotlar jadvali
    # Jadval sarlavhasi
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.5)
    
    # Jadval chegaralari
    table_top = y
    table_left = 5*mm
    table_right = page_width - 5*mm
    table_width = table_right - table_left
    
    # Ustun kengliklari
    col1_width = table_width * 0.50  # Mahsulot - 50%
    col2_width = table_width * 0.20  # Miqdor - 20%
    col3_width = table_width * 0.30  # Narx - 30%
    
    # Sarlavha qatori
    c.rect(table_left, y - 6*mm, table_width, 6*mm, stroke=1, fill=0)
    c.line(table_left + col1_width, y - 6*mm, table_left + col1_width, y)
    c.line(table_left + col1_width + col2_width, y - 6*mm, table_left + col1_width + col2_width, y)
    
    c.setFillColor(colors.black)  # Matn uchun qora rang
    c.setFont("Helvetica-Bold", 9)
    c.drawString(table_left + 2*mm, y - 4*mm, "Mahsulot")
    c.drawCentredString(table_left + col1_width + col2_width/2, y - 4*mm, "Miqdor")
    c.drawRightString(table_right - 2*mm, y - 4*mm, "Narx")
    
    y -= 6*mm
    
    # Mahsulotlar ro'yxati
    c.setFont("Helvetica-Bold", 8)
    for item in sale_data.get('items', []):
        # Mahsulot nomini qatorlarga bo'lish (uzun bo'lsa)
        product_name = item['name']
        max_width = col1_width - 4*mm  # 2mm padding har tarafdan
        
        # Matn kengligini tekshirish va qatorlarga bo'lish
        name_lines = []
        words = product_name.split()
        current_line = ""
        
        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            # Matn kengligini o'lchash (8 pt font)
            text_width = pdfmetrics.stringWidth(test_line, "Helvetica-Bold", 8)
            
            if text_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    name_lines.append(current_line)
                    current_line = word
                else:
                    # Bitta so'z juda uzun bo'lsa, uni qisqartirish
                    name_lines.append(word[:30])
                    current_line = ""
        
        if current_line:
            name_lines.append(current_line)
        
        # Agar qator bo'lmasa, kamida bitta qator qo'shish
        if not name_lines:
            name_lines = [product_name[:30]]
        
        # Row height - mahsulot nomi qatorlari soniga qarab
        lines_count = len(name_lines)
        row_height = max(6*mm, (3 + lines_count * 3) * mm)
        
        # Mahsulot nomini chizish (bir necha qator bo'lishi mumkin)
        name_y = y - 4*mm
        for line in name_lines:
            c.drawString(table_left + 2*mm, name_y, line)
            name_y -= 3*mm
        
        # Miqdor (yuqori qatorda)
        quantity = item['quantity']
        c.drawCentredString(table_left + col1_width + col2_width/2, y - 4*mm, str(int(quantity)))
        
        # Narx ustuni uchun sariq fon
        c.setFillColor(colors.Color(1, 1, 0.7))  # Och sariq rang
        c.rect(table_left + col1_width + col2_width, y - row_height, col3_width, row_height, stroke=0, fill=1)
        
        # Narx (valyutaga qarab, yuqori qatorda)
        c.setFillColor(colors.black)  # Matn uchun qora rang
        if currency == 'usd':
            unit_price = item.get('unit_price_usd', item.get('unit_price', 0))
            price_str = f"${unit_price:.2f}"
        else:
            unit_price = item.get('unit_price_uzs', item.get('unit_price', 0))
            price_str = f"{unit_price:,.0f}"
        
        c.drawRightString(table_right - 2*mm, y - 4*mm, price_str)
        
        # Qator borderlari
        c.setFillColor(colors.white)  # Border uchun to'ldirmaslik
        c.rect(table_left, y - row_height, table_width, row_height, stroke=1, fill=0)
        c.line(table_left + col1_width, y - row_height, table_left + col1_width, y)
        c.line(table_left + col1_width + col2_width, y - row_height, table_left + col1_width + col2_width, y)
        
        y -= row_height
        
        if y < 40*mm:  # Sahifa tugashidan oldin
            c.showPage()
            y = page_height - 10*mm
            c.setFont("Helvetica-Bold", 8)
    
    y -= 8*mm  # Jadval va jami summa orasidagi masofa
    
    # Jami summa (valyutaga qarab)
    c.setFillColor(colors.black)  # Matn uchun qora rangni qayta o'rnatish
    c.setFont("Helvetica-Bold", 11)
    c.drawString(table_left, y, "Jami summa:")
    if currency == 'usd':
        total_amount = sale_data.get('total_amount_usd', sale_data.get('total_amount', 0))
        c.drawRightString(table_right, y, f"${total_amount:.2f}")
    else:
        total_amount = sale_data.get('total_amount_uzs', sale_data.get('total_amount', 0))
        c.drawRightString(table_right, y, f"{total_amount:,.0f} {currency_symbol}")
    y -= 8*mm
    
    # To'lov ma'lumotlari (valyutaga qarab)
    paid_key = 'paid_amount_usd' if currency == 'usd' else 'paid_amount_uzs'
    paid_amount = sale_data.get(paid_key, sale_data.get('paid_amount', 0))
    
    if paid_amount > 0:
        c.setFillColor(colors.black)  # Matn uchun qora rang
        c.setFont("Helvetica-Bold", 9)
        c.drawString(table_left, y, "To'lov:")
        y -= 4*mm
        
        # To'lov turlari
        c.setFont("Helvetica", 8)
        cash_key = 'cash_usd' if currency == 'usd' else 'cash_uzs'
        click_key = 'click_usd' if currency == 'usd' else 'click_uzs'
        terminal_key = 'terminal_usd' if currency == 'usd' else 'terminal_uzs'
        
        if sale_data.get(cash_key, 0) > 0:
            c.drawString(table_left + 3*mm, y, "Naqd:")
            if currency == 'usd':
                c.drawRightString(table_right, y, f"${sale_data[cash_key]:,.2f}")
            else:
                c.drawRightString(table_right, y, f"{sale_data[cash_key]:,.0f} {currency_symbol}")
            y -= 4*mm
        if sale_data.get(click_key, 0) > 0:
            c.drawString(table_left + 3*mm, y, "Click:")
            if currency == 'usd':
                c.drawRightString(table_right, y, f"${sale_data[click_key]:,.2f}")
            else:
                c.drawRightString(table_right, y, f"{sale_data[click_key]:,.0f} {currency_symbol}")
            y -= 4*mm
        if sale_data.get(terminal_key, 0) > 0:
            c.drawString(table_left + 3*mm, y, "Terminal:")
            if currency == 'usd':
                c.drawRightString(table_right, y, f"${sale_data[terminal_key]:,.2f}")
            else:
                c.drawRightString(table_right, y, f"{sale_data[terminal_key]:,.0f} {currency_symbol}")
            y -= 4*mm
    
    # Qarz (valyutaga qarab)
    debt_key = 'debt_usd' if currency == 'usd' else 'debt_uzs'
    debt_amount = sale_data.get(debt_key, sale_data.get('debt', 0))
    
    if debt_amount > 0:
        y -= 2*mm
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.red)
        c.drawString(table_left, y, "QARZ:")
        if currency == 'usd':
            c.drawRightString(table_right, y, f"${debt_amount:,.2f}")
        else:
            c.drawRightString(table_right, y, f"{debt_amount:,.0f} {currency_symbol}")
        c.setFillColor(colors.black)
        y -= 6*mm
    
    # Footer
    y -= 5*mm
    c.setLineWidth(0.5)
    c.line(5*mm, y, page_width-5*mm, y)
    y -= 4*mm
    
    c.setFont("Helvetica", 8)
    c.drawCentredString(page_width/2, y, "Rahmat!")
    y -= 3*mm
    c.drawCentredString(page_width/2, y, "Yana tashrif buyuring!")
    y -= 8*mm
    
    # QR code - Telegram guruh linki
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=1,
        )
        qr.add_data('https://t.me/DIAMONDCARAccesories')
        qr.make(fit=True)
        
        # QR code rasmini yaratish
        qr_img = qr.make_image(fill_color="black", back_color="white")
        
        # PIL Image'ni bytes'ga o'girish
        img_buffer = io.BytesIO()
        qr_img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        
        # QR code o'lchami (25mm x 25mm)
        qr_size = 25*mm
        qr_x = (page_width - qr_size) / 2  # Markazda
        
        # QR code'ni chizish
        c.drawImage(ImageReader(img_buffer), qr_x, y - qr_size, width=qr_size, height=qr_size)
        y -= (qr_size + 3*mm)
        
        # QR code ostida matn
        c.setFont("Helvetica", 6)
        c.drawCentredString(page_width/2, y, "Telegram: @DIAMONDCARAccesories")
    except Exception as e:
        # QR code xato bo'lsa, oddiy matn qo'shish
        c.setFont("Helvetica", 6)
        c.drawCentredString(page_width/2, y, "Telegram: @DIAMONDCARAccesories")
        y -= 3*mm
    
    # PDF ni saqlash
    c.save()
    
    return output_path
