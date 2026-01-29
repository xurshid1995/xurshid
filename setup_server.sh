#!/bin/bash
# Jamshid Dokon - Server Setup Script
# Run this script on your server: bash setup_server.sh

set -e  # Stop on error

echo "🚀 Starting Xurshid Server Setup..."
echo "================================"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Step 1: Update system
echo -e "${BLUE}📦 Step 1: Updating system...${NC}"
apt update && apt upgrade -y

# Step 2: Install required packages
echo -e "${BLUE}📦 Step 2: Installing packages...${NC}"
apt install python3 python3-pip python3-venv postgresql postgresql-contrib nginx git curl libpq-dev -y

# Step 3: Setup PostgreSQL
echo -e "${BLUE}🗄️  Step 3: Setting up PostgreSQL...${NC}"
sudo -u postgres psql -c "CREATE DATABASE xurshid_db;" 2>/dev/null || echo "Database already exists"
sudo -u postgres psql -c "CREATE USER xurshid_user WITH PASSWORD 'Xurshid2025!Strong';" 2>/dev/null || echo "User already exists"
sudo -u postgres psql -c "ALTER DATABASE xurshid_db OWNER TO xurshid_user;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE xurshid_db TO xurshid_user;"

# Step 4: Create project directory
echo -e "${BLUE}📁 Step 4: Setting up project directory...${NC}"
mkdir -p /var/www/xurshid
cd /var/www/xurshid

# Step 5: Clone from GitHub
echo -e "${BLUE}📥 Step 5: Cloning project from GitHub...${NC}"
if [ -d ".git" ]; then
    echo "Git repository already exists, pulling latest changes..."
    git pull origin main
else
    git clone https://github.com/xurshid1995/xurshid.git .
fi

# Step 6: Setup Python virtual environment
echo -e "${BLUE}🐍 Step 6: Setting up Python environment...${NC}"
python3 -m venv venv
source venv/bin/activate

# Step 7: Install Python packages
echo -e "${BLUE}📦 Step 7: Installing Python packages...${NC}"
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn psycopg2-binary

# Step 8: Generate SECRET_KEY
echo -e "${BLUE}🔑 Step 8: Generating SECRET_KEY...${NC}"
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Step 9: Create .env file
echo -e "${BLUE}⚙️  Step 9: Creating .env file...${NC}"
cat > .env << EOF
SECRET_KEY=$SECRET_KEY
DB_HOST=localhost
DB_PORT=5432
DB_NAME=xurshid_db
DB_USER=xurshid_user
DB_PASSWORD=Xurshid2025!Strong
FLASK_ENV=production
FLASK_APP=app.py
EOF

echo -e "${GREEN}✅ SECRET_KEY generated: $SECRET_KEY${NC}"

# Step 10: Create logs directory
echo -e "${BLUE}📝 Step 10: Creating logs directory...${NC}"
mkdir -p logs
chmod 755 logs

# Step 11: Setup Gunicorn systemd service
echo -e "${BLUE}🔧 Step 11: Setting up Gunicorn service...${NC}"
cat > /etc/systemd/system/xurshid.service << 'EOF'
[Unit]
Description=Xurshid Gunicorn Application
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/xurshid
Environment="PATH=/var/www/xurshid/venv/bin"
ExecStart=/var/www/xurshid/venv/bin/gunicorn -c gunicorn_config.py app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Step 12: Setup Nginx
echo -e "${BLUE}🌐 Step 12: Setting up Nginx...${NC}"
cat > /etc/nginx/sites-available/xurshid << 'EOF'
server {
    listen 80;
    server_name 164.92.177.172;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /var/www/xurshid/static;
        expires 30d;
    }
}
EOF

# Enable Nginx site
ln -sf /etc/nginx/sites-available/xurshid /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t

# Step 13: Set proper permissions
echo -e "${BLUE}🔒 Step 13: Setting permissions...${NC}"
chown -R www-data:www-data /var/www/xurshid
chmod -R 755 /var/www/xurshid

# Step 14: Start services
echo -e "${BLUE}🚀 Step 14: Starting services...${NC}"
systemctl daemon-reload
systemctl start xurshid
systemctl enable xurshid
systemctl restart nginx

echo ""
echo "================================"
echo -e "${GREEN}✅ ✅ ✅  INSTALLATION COMPLETE!  ✅ ✅ ✅${NC}"
echo "================================"
echo ""
echo "🌐 Your application is now running at:"
echo "   http://164.92.177.172"
echo ""
echo "📊 Check service status:"
echo "   systemctl status xurshid"
echo ""
echo "📝 View logs:"
echo "   tail -f logs/error.log"
echo "   tail -f logs/access.log"
echo ""
echo "🔑 Your SECRET_KEY has been saved to .env file"
echo ""
echo "🎉 Enjoy your application!"
