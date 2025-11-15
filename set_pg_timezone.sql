-- PostgreSQL timezone'ni O'zbekiston vaqtiga o'zgartirish
ALTER DATABASE dokon_db SET timezone TO 'Asia/Tashkent';

-- Joriy connection'ni yangilash
SET timezone TO 'Asia/Tashkent';

-- Tekshirish
SHOW timezone;
SELECT NOW();
