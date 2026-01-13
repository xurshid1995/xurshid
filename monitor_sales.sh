#!/bin/bash
echo "=== DOIMIY KUZATUV BOSHLANMOQDA ==="
echo "Ctrl+C bilan to'xtatish mumkin"
echo ""

while true; do
    clear
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo ""
    
    echo "SAVDOLAR JADVALI (oxirgi 3 ta):"
    sudo -u postgres psql sayt_db -t -c "SELECT id, customer_id, payment_status, to_char(sale_date, 'HH24:MI:SS') as time, cash_usd, debt_usd FROM sales ORDER BY id DESC LIMIT 3;" 2>/dev/null
    
    echo ""
    echo "JAMI:"
    sudo -u postgres psql sayt_db -t -c "SELECT payment_status, COUNT(*) as count FROM sales GROUP BY payment_status;" 2>/dev/null
    
    echo ""
    echo "OXIRGI LOG (3 qator):"
    sudo journalctl -u jamshid.service -n 3 --no-pager 2>/dev/null | tail -3
    
    sleep 2
done
