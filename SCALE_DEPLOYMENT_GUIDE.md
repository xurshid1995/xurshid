# 5 TA DO'KON + 5 TA SKLAD UCHUN SERVER SOZLASH
## Server: 164.92.177.172

## 📋 CURRENT STATUS

### ✅ Tayyor:
- Database struktura ready
- Indekslar optimallangan (44 ta index)
- Connection pool configured
- 3 Gunicorn workers
- Nginx konfiguratsiyalangan

### 🔧 Talab qilinadigan o'zgarishlar:
1. PostgreSQL optimization
2. Monitoring qo'shish
3. Backup strategiyasi
4. Performance tuning

---

## 🚀 DEPLOYMENT STEPS

### 1️⃣ PostgreSQL Optimizatsiya

```bash
# Serverga ulanish
ssh root@164.92.177.172

# Fayl yuklash (mahalliy kompyuterdan)
scp d:\hisobot\Xurshid\postgresql_optimization_2gb.sql root@164.92.177.172:/tmp/

# Serverda qo'llash
sudo -u postgres psql -d xurshid_db -f /tmp/postgresql_optimization_2gb.sql

# PostgreSQL restart
sudo systemctl restart postgresql

# Tekshirish
sudo -u postgres psql -c "SHOW shared_buffers;"
sudo -u postgres psql -c "SHOW effective_cache_size;"
```

### 2️⃣ Monitoring Script O'rnatish

```bash
# Script yuklash
scp d:\hisobot\Xurshid\server_monitoring.sh root@164.92.177.172:/root/

# Ruxsat berish
ssh root@164.92.177.172 "chmod +x /root/server_monitoring.sh"

# Ishga tushirish
ssh root@164.92.177.172 "/root/server_monitoring.sh"

# Cron job qo'shish (har kuni soat 9:00 da)
ssh root@164.92.177.172 "echo '0 9 * * * /root/server_monitoring.sh > /var/log/server_monitoring.log 2>&1' | crontab -"
```

### 3️⃣ pg_stat_statements Extension (opsional, lekin tavsiya etiladi)

```bash
ssh root@164.92.177.172

# postgresql.conf edit qilish
sudo nano /etc/postgresql/16/main/postgresql.conf

# Quyidagini qo'shing yoki uncommment qiling:
# shared_preload_libraries = 'pg_stat_statements'

# PostgreSQL restart
sudo systemctl restart postgresql

# Extension yaratish
sudo -u postgres psql -d xurshid_db -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

# Tekshirish
sudo -u postgres psql -d xurshid_db -c "SELECT * FROM pg_stat_statements LIMIT 1;"
```

### 4️⃣ Backup Strategiyasi

```bash
# Backup script yaratish
cat > /root/backup_database.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/var/backups/postgresql"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# Database backup
sudo -u postgres pg_dump xurshid_db > $BACKUP_DIR/xurshid_db_$DATE.sql

# Compress
gzip $BACKUP_DIR/xurshid_db_$DATE.sql

# Eski backuplarni o'chirish (7 kundan eski)
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "Backup completed: xurshid_db_$DATE.sql.gz"
EOF

# Ruxsat berish
chmod +x /root/backup_database.sh

# Test qilish
/root/backup_database.sh

# Cron job (har kuni soat 02:00 da)
echo "0 2 * * * /root/backup_database.sh >> /var/log/backup.log 2>&1" | crontab -
```

---

## 📊 EXPECTED PERFORMANCE

### Current (1 do'kon + 2 sklad):
- RAM usage: ~750MB (37%)
- DB connections: 5-10 active
- Response time: <100ms

### After scaling (5 do'kon + 5 sklad):
- RAM usage: ~950-1100MB (47-55%)
- DB connections: 10-20 active
- Response time: 100-200ms (acceptable)
- Concurrent users: 30-50 (with 3 workers)

### Warning thresholds:
- RAM usage >75%: Upgrade needed
- DB connections >30: Check for connection leaks
- Response time >500ms: Query optimization needed

---

## 🎯 SCALING PLAN

### Phase 1: Current Setup (DONE ✅)
- 2GB RAM
- 2 CPU cores
- 3 Gunicorn workers
- PostgreSQL default config
- **Capacity:** 3-5 locations

### Phase 2: After Optimization (IN PROGRESS 🔄)
- 2GB RAM
- PostgreSQL tuned
- Monitoring active
- Backup automated
- **Capacity:** 5-10 locations

### Phase 3: Hardware Upgrade (FUTURE 📅)
- 4GB RAM
- 4-5 Gunicorn workers
- Enhanced monitoring
- **Capacity:** 10-20 locations

---

## ⚠️ POTENTIAL ISSUES & SOLUTIONS

### Issue 1: Sekin query'lar
**Symptoms:** Response time >500ms
**Solution:**
```bash
# Slow queries topish
sudo -u postgres psql -d xurshid_db << 'EOF'
SELECT query, calls, mean_exec_time, max_exec_time
FROM pg_stat_statements 
ORDER BY mean_exec_time DESC LIMIT 10;
EOF

# Missing indexes topish
sudo -u postgres psql -d xurshid_db << 'EOF'
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats 
WHERE schemaname = 'public' 
AND n_distinct > 100 
AND correlation < 0.1;
EOF
```

### Issue 2: RAM to'lishi
**Symptoms:** Free RAM <200MB
**Solution:**
```bash
# Worker count kamaytirish
# gunicorn_config.py da:
workers = 2  # 3 o'rniga

# Service restart
sudo systemctl restart xurshid_app

# Yoki RAM upgrade:
# DigitalOcean droplet resize: 2GB → 4GB
```

### Issue 3: Database connection leak
**Symptoms:** Idle connections oshib ketadi
**Solution:**
```bash
# Idle connections ko'rish
sudo -u postgres psql -d xurshid_db -c "
SELECT pid, usename, application_name, state, state_change
FROM pg_stat_activity 
WHERE state = 'idle' 
AND state_change < now() - interval '10 minutes';
"

# Ularni o'chirish (ehtiyotkorlik bilan!)
# sudo -u postgres psql -d xurshid_db -c "
# SELECT pg_terminate_backend(pid) 
# FROM pg_stat_activity 
# WHERE state = 'idle' 
# AND state_change < now() - interval '30 minutes';
# "
```

---

## 📈 MONITORING CHECKLIST

### Kundalik (Automated):
- [ ] Server monitoring script ishga tushdi
- [ ] Database backup olindi
- [ ] Error loglar tekshirildi

### Haftalik (Manual):
- [ ] RAM usage trend tahlili
- [ ] Slow queries tekshiruv
- [ ] Database size o'sishi
- [ ] Backup restore test

### Oylik (Manual):
- [ ] Performance comparison
- [ ] Capacity planning review
- [ ] Security updates
- [ ] Optimization opportunities

---

## 🔗 USEFUL COMMANDS

```bash
# Server monitoringni ishga tushirish
ssh root@164.92.177.172 "/root/server_monitoring.sh"

# Real-time server ko'rish
ssh root@164.92.177.172 "htop"

# PostgreSQL live activity
ssh root@164.92.177.172 "watch -n 2 'sudo -u postgres psql -d xurshid_db -c \"SELECT count(*), state FROM pg_stat_activity GROUP BY state;\"'"

# Application logs
ssh root@164.92.177.172 "tail -f /var/www/xurshid/logs/error.log"

# Nginx access log
ssh root@164.92.177.172 "tail -f /var/www/xurshid/logs/access.log"

# System resources
ssh root@164.92.177.172 "free -h && df -h && uptime"
```

---

## 📞 SUPPORT

Agar quyidagi holatlar yuz bersa darhol tekshiring:

1. **RAM usage >80%**: Worker count kamaytiring yoki RAM upgrade
2. **Response time >1000ms**: Database slow queries tekshiring
3. **Disk usage >80%**: Eski loglar va backuplar tozalang
4. **DB connections >50**: Connection leak tekshiring

---

## ✅ DEPLOYMENT CHECKLIST

- [ ] PostgreSQL optimizatsiya qo'llanildi
- [ ] Monitoring script o'rnatildi
- [ ] Backup automation sozlandi
- [ ] pg_stat_statements enabled
- [ ] Performance baseline o'lchandi
- [ ] Alert thresholds configured
- [ ] Documentation updated
- [ ] Team trained

---

**Last updated:** 2026-02-06
**Server:** 164.92.177.172
**Target:** 5 do'kon + 5 sklad
**Status:** ✅ Ready for deployment
