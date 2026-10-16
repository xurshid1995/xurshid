-- Hosting mijozlar balans tugab bloklangan sanasini saqlash uchun ustun qo'shish
ALTER TABLE hosting_clients
ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMP;

-- Hozirda bloklangan (suspended) mijozlar uchun taxminiy blok sanasini
-- updated_at'dan tiklash (bu maydon aynan server_status o'zgargan paytda yangilangan)
UPDATE hosting_clients
SET blocked_at = updated_at
WHERE server_status = 'suspended' AND blocked_at IS NULL;
