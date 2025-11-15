-- Eski savdolar vaqtini UTC dan O'zbekiston vaqtiga o'zgartirish (5 soat qo'shish)

-- Avval nechta record bor ko'ramiz
SELECT 'Sales jadvali - eski vaqtlar:' as info;
SELECT id, sale_date, created_at FROM sales ORDER BY id DESC LIMIT 5;

-- Sales jadvalidagi barcha vaqtlarga 5 soat qo'shamiz
UPDATE sales 
SET sale_date = sale_date + INTERVAL '5 hours',
    created_at = created_at + INTERVAL '5 hours'
WHERE sale_date < '2025-11-11 12:00:00';  -- Faqat 12:00 dan oldingi (UTC) vaqtlarni o'zgartiramiz

-- Yangilangan vaqtlarni ko'ramiz
SELECT 'Yangilangan vaqtlar:' as info;
SELECT id, sale_date, created_at FROM sales ORDER BY id DESC LIMIT 5;

-- Qancha qator o'zgarganini ko'rsatish
SELECT 'Jami o''zgargan:' as info, COUNT(*) as count FROM sales WHERE sale_date >= '2025-11-11 12:00:00';
