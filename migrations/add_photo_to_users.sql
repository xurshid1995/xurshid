-- Foydalanuvchilar jadvaliga rasm ustuni qo'shish
ALTER TABLE users ADD COLUMN IF NOT EXISTS photo VARCHAR(255);
