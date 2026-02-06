#!/bin/bash
# Enable pg_stat_statements for PostgreSQL performance monitoring
# Server: 164.92.177.172

echo "📊 Enabling pg_stat_statements..."
echo ""

# 1. Backup config
echo "1. Creating backup..."
sudo cp /etc/postgresql/16/main/postgresql.conf /etc/postgresql/16/main/postgresql.conf.backup-$(date +%Y%m%d)
echo "   ✓ Backup created"

# 2. Add configuration (proper format)
echo "2. Adding configuration..."
sudo bash << 'EOF'
cat >> /etc/postgresql/16/main/postgresql.conf << 'PGCONF'

# ============================================
# Performance Monitoring - Added 2026-02-06
# ============================================
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.track = all
pg_stat_statements.max = 10000
PGCONF
EOF
echo "   ✓ Configuration added"

# 3. Verify
echo "3. Verifying configuration..."
sudo tail -6 /etc/postgresql/16/main/postgresql.conf
echo ""

# 4. Restart PostgreSQL
echo "4. Restarting PostgreSQL..."
sudo systemctl restart postgresql
sleep 3
echo "   ✓ PostgreSQL restarted"

# 5. Create extension
echo "5. Creating pg_stat_statements extension..."
sudo -u postgres psql -d xurshid_db -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
echo "   ✓ Extension created"

# 6. Test
echo "6. Testing extension..."
RESULT=$(sudo -u postgres psql -d xurshid_db -tAc "SELECT COUNT(*) FROM pg_stat_statements;")
echo "   ✓ Query count: $RESULT"

echo ""
echo "✅ pg_stat_statements successfully enabled!"
echo ""
echo "Usage:"
echo "  sudo -u postgres psql -d xurshid_db -c \"SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;\""
