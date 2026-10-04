param(
    [string]$msg = "update"
)

$ErrorActionPreference = "Stop"

$nativeSchemaCheck = @'
import os
import sys
import psycopg2
from dotenv import load_dotenv
load_dotenv('/var/www/xurshid/.env')
try:
    connection = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'),
        dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'],
        connect_timeout=10)
    connection.set_session(readonly=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM information_schema.columns "
                       "WHERE table_schema='public' AND table_name='products' "
                       "AND column_name IN ('cost_price_original', 'sell_price_original')")
        legacy_columns = cursor.fetchone()[0]
    connection.close()
    if legacy_columns:
        print('STOP: product_prices_native_currency.sql must be applied during maintenance before deployment.')
        sys.exit(1)
except Exception:
    print('STOP: Cannot verify native product price schema; deployment aborted.')
    sys.exit(1)
'@
$nativeSchemaCheck | ssh root@sergeli0606.uz "cd /var/www/xurshid && venv/bin/python -"
if ($LASTEXITCODE -ne 0) {
    throw "Native narx migratsiyasi tekshiruvi o'tmadi. Push va deploy bajarilmadi."
}

Write-Host ""
Write-Host "======================================" -ForegroundColor DarkGray
Write-Host "  PUSH and DEPLOY" -ForegroundColor White
Write-Host "======================================" -ForegroundColor DarkGray
Write-Host ""

# 1. Git add + commit
Write-Host "[1/3] Git add + commit: $msg" -ForegroundColor Cyan
git add .
$commitOutput = git commit -m $msg 2>&1
if ($LASTEXITCODE -ne 0) {
    if ("$commitOutput" -match "nothing to commit") {
        Write-Host "  O'zgarish yoq, commit otkazib yuborildi." -ForegroundColor Yellow
    } else {
        Write-Host "  Commit xatosi: $commitOutput" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  $commitOutput" -ForegroundColor DarkGray
}

# 2. Push
Write-Host ""
Write-Host "[2/3] GitHub'ga push..." -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Push xatosi!" -ForegroundColor Red
    exit 1
}

# 3. Deploy via SSH
Write-Host ""
Write-Host "[3/3] Serverga deploy (sergeli0606.uz)..." -ForegroundColor Cyan
$sshCmd = "cd /var/www/xurshid && git fetch origin && git reset --hard origin/main && source venv/bin/activate && pip install -r requirements.txt -q && sudo cp /var/www/xurshid/nginx_sergeli0606.conf /etc/nginx/sites-enabled/xurshid && sudo nginx -t && sudo nginx -s reload && sudo systemctl restart xurshid && sleep 2 && sudo systemctl status xurshid --no-pager -n 5"
ssh root@sergeli0606.uz $sshCmd
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Deploy xatosi!" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "======================================" -ForegroundColor DarkGray
Write-Host "  Deploy muvaffaqiyatli tugadi!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor DarkGray
Write-Host ""
