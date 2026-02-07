#!/bin/bash
# =============================================================================
# EC2 Server Setup Script for TaxPlanAdvisor Backend
# Run this script on a fresh Ubuntu 22.04/24.04 EC2 instance
# =============================================================================

set -e

echo "🚀 Starting TaxPlanAdvisor Server Setup..."

# =============================================================================
# 1. System Updates
# =============================================================================
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# =============================================================================
# 2. Install Required Packages
# =============================================================================
echo "📦 Installing required packages..."
sudo apt install -y \
    python3.12 \
    python3.12-venv \
    python3-pip \
    nginx \
    certbot \
    python3-certbot-nginx \
    redis-server \
    git \
    supervisor

# =============================================================================
# 3. Create Application Directory
# =============================================================================
echo "📁 Setting up application directory..."
sudo mkdir -p /home/ubuntu/taxplanadvisor
sudo mkdir -p /var/log/taxplanadvisor
sudo mkdir -p /var/www/taxplanadvisor/static
sudo chown -R ubuntu:www-data /home/ubuntu/taxplanadvisor
sudo chown -R ubuntu:www-data /var/log/taxplanadvisor
sudo chown -R ubuntu:www-data /var/www/taxplanadvisor

echo "✅ Directory structure created"

# =============================================================================
# 4. Clone Repository (Update with your repo URL)
# =============================================================================
echo "📥 Clone your repository manually:"
echo "   cd /home/ubuntu/taxplanadvisor"
echo "   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git ."
echo ""
echo "Or copy files using SCP:"
echo "   scp -r ./backend ubuntu@YOUR_EC2_IP:/home/ubuntu/taxplanadvisor/"

# =============================================================================
# 5. Python Virtual Environment
# =============================================================================
echo "🐍 Setting up Python virtual environment..."
cd /home/ubuntu/taxplanadvisor/backend
python3.12 -m venv venv
source venv/bin/activate

echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# =============================================================================
# 6. Environment Variables
# =============================================================================
echo "⚙️ Setting up environment variables..."
echo "Copy your .env.production to .env:"
echo "   cp deployment/.env.production.example .env"
echo "   nano .env  # Edit with your actual values"

# =============================================================================
# 7. Database Migrations
# =============================================================================
echo "🗄️ Run database migrations after setting up .env:"
echo "   source venv/bin/activate"
echo "   python manage.py migrate"
echo "   python manage.py collectstatic --noinput"
echo "   python manage.py createsuperuser"

# =============================================================================
# 8. Systemd Services
# =============================================================================
echo "🔧 Installing systemd services..."
sudo cp /home/ubuntu/taxplanadvisor/backend/deployment/gunicorn.service /etc/systemd/system/
sudo cp /home/ubuntu/taxplanadvisor/backend/deployment/daphne.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable gunicorn
sudo systemctl enable daphne

echo "Start services with:"
echo "   sudo systemctl start gunicorn"
echo "   sudo systemctl start daphne"

# =============================================================================
# 9. Nginx Configuration
# =============================================================================
echo "🌐 Setting up Nginx..."
sudo cp /home/ubuntu/taxplanadvisor/backend/deployment/nginx.conf /etc/nginx/sites-available/taxplanadvisor

# Update domain in nginx config
echo "📝 Edit domain in nginx config:"
echo "   sudo nano /etc/nginx/sites-available/taxplanadvisor"
echo "   Replace 'api.yourdomain.com' with your actual domain"

sudo ln -sf /etc/nginx/sites-available/taxplanadvisor /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

# =============================================================================
# 10. SSL Certificate
# =============================================================================
echo "🔒 Setting up SSL certificate..."
echo "Run this after pointing your domain to this server:"
echo "   sudo certbot --nginx -d api.yourdomain.com"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=============================================="
echo "✅ Server Setup Complete!"
echo "=============================================="
echo ""
echo "📋 Remaining Manual Steps:"
echo ""
echo "1. Clone/Copy your code:"
echo "   cd /home/ubuntu/taxplanadvisor && git clone YOUR_REPO ."
echo ""
echo "2. Configure environment:"
echo "   cd backend && cp deployment/.env.production.example .env"
echo "   nano .env  # Add your actual values"
echo ""
echo "3. Setup database:"
echo "   source venv/bin/activate"
echo "   python manage.py migrate"
echo "   python manage.py collectstatic --noinput"
echo ""
echo "4. Start services:"
echo "   sudo systemctl start gunicorn"
echo "   sudo systemctl start daphne"
echo "   sudo systemctl status gunicorn daphne"
echo ""
echo "5. Configure SSL:"
echo "   sudo certbot --nginx -d api.yourdomain.com"
echo ""
echo "6. Verify:"
echo "   curl https://api.yourdomain.com/health"
echo ""
