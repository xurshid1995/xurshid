-- Migration: Add completed_at column to stock_check_sessions table
-- Date: 2026-02-08
-- Description: Qoldiq tekshiruvi tugatilgan vaqtni saqlash uchun completed_at maydoni qo'shiladi

-- completed_at ustunini qo'shish
ALTER TABLE stock_check_sessions 
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL;

-- Mavjud completed sessiyalar uchun completed_at ni updated_at dan olish
UPDATE stock_check_sessions 
SET completed_at = updated_at 
WHERE status = 'completed' AND completed_at IS NULL;

-- Index qo'shish (tezlikni oshirish uchun)
CREATE INDEX IF NOT EXISTS idx_stock_check_sessions_completed_at 
ON stock_check_sessions(completed_at) WHERE status = 'completed';

-- Comment qo'shish
COMMENT ON COLUMN stock_check_sessions.completed_at IS 'Tekshiruv tugatilgan vaqt (completed yoki cancelled status uchun)';
