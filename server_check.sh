#!/bin/bash

echo "=============================================="
echo "SERVERDAGI BUGUNGI SAVDOLAR TAHLILI"
echo "=============================================="

# 1. Umumiy statistika
echo ""
echo "1. UMUMIY STATISTIKA (barcha savdolar):"
sudo -u postgres psql xurshid_db -c "
SELECT 
    COUNT(*) as savdolar_soni,
    ROUND(SUM(total_amount), 2) as total_amount,
    ROUND(SUM(cash_usd), 2) as naqd,
    ROUND(SUM(click_usd), 2) as click,
    ROUND(SUM(terminal_usd), 2) as terminal,
    ROUND(SUM(debt_usd), 2) as qarz,
    ROUND(SUM(cash_usd + click_usd + terminal_usd + debt_usd), 2) as tolov_jami
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE;
"

# 2. Dashboard query (faqat to'lov bor savdolar)
echo ""
echo "2. DASHBOARD QUERY (faqat to'lov > 0):"
sudo -u postgres psql xurshid_db -c "
SELECT 
    COUNT(*) as savdolar_soni,
    ROUND(SUM(total_amount), 2) as total_amount,
    ROUND(SUM(cash_usd), 2) as naqd,
    ROUND(SUM(click_usd), 2) as click,
    ROUND(SUM(terminal_usd), 2) as terminal,
    ROUND(SUM(debt_usd), 2) as qarz,
    ROUND(SUM(cash_usd + click_usd + terminal_usd + debt_usd), 2) as tolov_jami
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE
AND (cash_usd > 0 OR click_usd > 0 OR terminal_usd > 0 OR debt_usd > 0);
"

# 3. total != to'lovlar jami bo'lgan savdolarni topish
echo ""
echo "3. MUAMMOLI SAVDOLAR (total != to'lovlar):"
sudo -u postgres psql xurshid_db -c "
SELECT 
    id,
    TO_CHAR(sale_date, 'HH24:MI:SS') as vaqt,
    ROUND(total_amount, 2) as total,
    ROUND(cash_usd, 2) as naqd,
    ROUND(click_usd, 2) as click,
    ROUND(terminal_usd, 2) as terminal,
    ROUND(debt_usd, 2) as qarz,
    ROUND(cash_usd + click_usd + terminal_usd + debt_usd, 2) as tolov_jami,
    ROUND(total_amount - (cash_usd + click_usd + terminal_usd + debt_usd), 2) as farq
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE
AND ABS(total_amount - (cash_usd + click_usd + terminal_usd + debt_usd)) > 0.01
ORDER BY sale_date DESC
LIMIT 10;
"

# 4. Eng oxirgi 5 ta savdo
echo ""
echo "4. ENG OXIRGI 5 TA SAVDO:"
sudo -u postgres psql xurshid_db -c "
SELECT 
    id,
    TO_CHAR(sale_date, 'HH24:MI:SS') as vaqt,
    ROUND(total_amount, 2) as total,
    ROUND(cash_usd, 2) as naqd,
    ROUND(click_usd, 2) as click,
    ROUND(terminal_usd, 2) as terminal,
    ROUND(debt_usd, 2) as qarz
FROM sales 
WHERE DATE(sale_date) = CURRENT_DATE
ORDER BY sale_date DESC
LIMIT 5;
"

echo ""
echo "=============================================="
echo "TAHLIL TUGADI"
echo "=============================================="
