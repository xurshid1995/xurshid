-- Add status workflow columns to pending_transfers table
ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'draft';
ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP;
ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMP;
ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS dispatched_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE pending_transfers ADD COLUMN IF NOT EXISTS receiver_confirmed_at TIMESTAMP;

-- Update existing records to have 'draft' status
UPDATE pending_transfers SET status = 'draft' WHERE status IS NULL;
