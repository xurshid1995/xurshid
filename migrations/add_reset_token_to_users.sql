-- Migration: Add reset_token columns to users table
-- Purpose: Store password reset tokens in DB (instead of in-memory dict which breaks with multiple Gunicorn workers)
-- Date: 2026-05-24

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS reset_token VARCHAR(64) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS reset_token_expires_at TIMESTAMP DEFAULT NULL;

-- Index for fast token lookup
CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(reset_token) WHERE reset_token IS NOT NULL;
