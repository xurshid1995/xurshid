-- PostgreSQL Optimization for 2GB RAM Server
-- 5 do'kon + 5 sklad uchun sozlash
-- Server: 164.92.177.172

-- ============================================
-- MEMORY SETTINGS (2GB RAM)
-- ============================================

-- Shared Buffers: RAM ning 25% (128MB → 256MB)
ALTER SYSTEM SET shared_buffers = '256MB';

-- Effective Cache: RAM ning 50-75% (1GB)
ALTER SYSTEM SET effective_cache_size = '1GB';

-- Work Memory: Per Connection (4MB default yaxshi)
ALTER SYSTEM SET work_mem = '4MB';

-- Maintenance Work Memory: Index yaratish uchun
ALTER SYSTEM SET maintenance_work_mem = '64MB';

-- ============================================
-- CONNECTION SETTINGS
-- ============================================

-- Max Connections: 100 (yetarli)
-- ALTER SYSTEM SET max_connections = 100;  -- default

-- ============================================
-- QUERY OPTIMIZATION
-- ============================================

-- Random Page Cost: SSD uchun optimallashtirish
ALTER SYSTEM SET random_page_cost = 1.1;  -- Default 4.0, SSD uchun past

-- Effective IO Concurrency: SSD uchun
ALTER SYSTEM SET effective_io_concurrency = 200;

-- ============================================
-- WRITE AHEAD LOG (WAL) SETTINGS
-- ============================================

-- WAL Buffers: Shared buffers ning 1/32
ALTER SYSTEM SET wal_buffers = '8MB';

-- Checkpoint Settings: Yozish operatsiyalari uchun
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET max_wal_size = '1GB';
ALTER SYSTEM SET min_wal_size = '256MB';

-- ============================================
-- AUTOVACUUM SETTINGS (10 ta location uchun)
-- ============================================

-- Autovacuum: Aktiv saqlash
ALTER SYSTEM SET autovacuum = on;
ALTER SYSTEM SET autovacuum_max_workers = 3;
ALTER SYSTEM SET autovacuum_naptime = '1min';

-- ============================================
-- STATISTICS & MONITORING
-- ============================================

-- Statistics: Query planlar uchun
ALTER SYSTEM SET default_statistics_target = 100;

-- Slow query logging: 1 sekunddan sekin querylar
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- milliseconds

-- ============================================
-- CONNECTION TUNING
-- ============================================

-- Statement Timeout: 30 sekund (API timeout bilan mos)
ALTER SYSTEM SET statement_timeout = '30s';

-- Lock Timeout: 10 sekund
ALTER SYSTEM SET lock_timeout = '10s';

-- Idle in Transaction Timeout: 10 minut
ALTER SYSTEM SET idle_in_transaction_session_timeout = '10min';

-- ============================================
-- QANDAY QO'LLASH:
-- ============================================
-- 1. Serverga ulanish:
--    ssh root@164.92.177.172
--
-- 2. Scriptni yuklash:
--    sudo -u postgres psql -d xurshid_db -f postgresql_optimization_2gb.sql
--
-- 3. PostgreSQL restart:
--    sudo systemctl restart postgresql
--
-- 4. Tekshirish:
--    sudo -u postgres psql -c "SHOW shared_buffers;"
--    sudo -u postgres psql -c "SHOW effective_cache_size;"
--
-- ============================================
-- MONITORING QUERIES
-- ============================================

-- Active connections ko'rish:
-- SELECT count(*) FROM pg_stat_activity WHERE state = 'active';

-- Slow queries:
-- SELECT query, calls, total_time, mean_time 
-- FROM pg_stat_statements 
-- ORDER BY mean_time DESC LIMIT 10;

-- Database size:
-- SELECT pg_size_pretty(pg_database_size('xurshid_db'));

-- Table sizes:
-- SELECT schemaname, tablename, 
--        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
-- FROM pg_tables 
-- WHERE schemaname = 'public' 
-- ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
