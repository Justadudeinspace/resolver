#!/usr/bin/env bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're in the correct directory
if [ ! -f "app/main.py" ]; then
    print_error "Could not find app/main.py"
    print_info "Please run this script from the project root directory"
    exit 1
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    print_warning ".env file not found"
    print_info "Creating .env from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_info "Please edit .env file with your credentials"
        exit 1
    else
        print_error "No .env.example file found either"
        exit 1
    fi
fi

# Generate invoice secret if not exists
if ! grep -q "INVOICE_SECRET=" .env 2>/dev/null || [ "$(grep 'INVOICE_SECRET=' .env | cut -d= -f2)" = "generate_a_secure_random_string_here_at_least_32_chars" ]; then
    print_info "Generating secure invoice secret..."
    NEW_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    if grep -q "INVOICE_SECRET=" .env 2>/dev/null; then
        sed -i "s|INVOICE_SECRET=.*|INVOICE_SECRET=$NEW_SECRET|" .env
    else
        echo "INVOICE_SECRET=$NEW_SECRET" >> .env
    fi
    print_info "Invoice secret generated and saved to .env"
fi

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    print_error "Python 3.9+ is required (found $PYTHON_VERSION)"
    exit 1
fi

print_info "Python version: $PYTHON_VERSION"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    print_warning "Virtual environment not found"
    print_info "Creating virtual environment..."
    python -m venv .venv
    print_info "Activating virtual environment..."
    # shellcheck disable=SC1091
    source .venv/bin/activate
    print_info "Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    print_info "Activating virtual environment..."
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Check if requirements are installed
if ! python -c "import aiogram, pydantic, cachetools, cryptography" &> /dev/null; then
    print_warning "Some dependencies missing"
    print_info "Installing dependencies..."
    pip install -r requirements.txt
fi

# Run database health check first
print_info "Checking database..."
if python -c "
import sys
sys.path.insert(0, '.')
from app.db import DB
from app.config import settings

db = DB(settings.db_path)
if db.health_check():
    print('Database OK')
else:
    print('Database health check failed')
    sys.exit(1)
"; then
    print_info "Database is healthy"
else
    print_error "Database health check failed"
    exit 1
fi

# Run the bot
print_info "Starting The Resolver bot..."
print_info "Press Ctrl+C to stop"

# Run with unbuffered output for better logging
PYTHONPATH=. python -u -m app.main
