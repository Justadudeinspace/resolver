#!/usr/bin/env bash
set -euo pipefail

# Colors for output
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; NC=''
fi

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

read_env_value() {
    local key="$1"
    local value=""
    if [ ! -f ".env" ]; then
        return 1
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ""|\#*) continue ;;
        esac
        if [[ "$line" == "$key="* ]]; then
            value="${line#*=}"
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            printf "%s" "$value"
            return 0
        fi
    done < ".env"
    return 1
}

# Note: This list is duplicated in app/config.py. Keep them in sync.
PLACEHOLDER_MARKERS=("CHANGE_ME" "YOUR_SECRET_HERE" "REPLACE_ME" "INSERT_SECRET" "EXAMPLE")

is_placeholder_value() {
    local value="$1"
    for marker in "${PLACEHOLDER_MARKERS[@]}"; do
        if [[ "$value" == *"$marker"* ]]; then
            return 0
        fi
    done
    return 1
}

run_with_timeout() {
    if command -v timeout &> /dev/null; then
        timeout 60 "$@"
    else
        "$@"
    fi
}

# Check if we're in the correct directory
if [ ! -f "app/main.py" ]; then
    print_error "Could not find app/main.py"
    print_info "Please run this script from the project root directory"
    exit 1
fi

# Ensure .env exists
if [ ! -f ".env" ]; then
    print_warning ".env file not found"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_info "Created .env from .env.example"
        print_info "Edit .env and set BOT_TOKEN, then re-run ./run_resolver.sh"
        exit 1
    else
        print_error "No .env.example file found"
        exit 1
    fi
fi

BOT_TOKEN="$(read_env_value "BOT_TOKEN" || true)"
if [ -z "$BOT_TOKEN" ] || is_placeholder_value "$BOT_TOKEN"; then
    print_error "BOT_TOKEN is missing or still a placeholder."
    print_info "Edit .env and set BOT_TOKEN to your Telegram bot token, then re-run ./run_resolver.sh"
    exit 1
fi

# Detect Termux
IS_TERMUX=false
if [ -f /system/build.prop ] && [ -d /data/data/com.termux/files/usr ]; then
    IS_TERMUX=true
fi

# Check Python
if command -v python3 &> /dev/null; then
    PYTHON_BIN="python3"
elif command -v python &> /dev/null; then
    PYTHON_BIN="python"
else
    print_error "Python 3 not found"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_BIN --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    print_error "Python 3.9+ is required (found $PYTHON_VERSION)"
    exit 1
fi

print_info "Python version: $PYTHON_VERSION"

# Ensure directories exist
mkdir -p data logs

DB_PATH="$(read_env_value "DB_PATH" || true)"
if [ -z "$DB_PATH" ]; then
    DB_PATH="./data/resolver.sqlite3"
fi
DB_DIR="$(dirname "$DB_PATH")"
if [ -n "$DB_DIR" ]; then
    mkdir -p "$DB_DIR"
fi

# Setup virtual environment on non-Termux
if [ "$IS_TERMUX" = false ]; then
    if [ ! -d ".venv" ]; then
        print_warning "Virtual environment not found"
        print_info "Creating virtual environment..."
        $PYTHON_BIN -m venv .venv
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON_BIN="python"
fi

# Install requirements if needed
if ! $PYTHON_BIN -c "import aiogram, pydantic, pydantic_settings, cachetools, openai, cryptography" &> /dev/null; then
    print_warning "Some dependencies missing"
    print_info "Installing dependencies..."
    if ! run_with_timeout $PYTHON_BIN -m pip install --upgrade pip; then
        print_warning "Pip upgrade failed; continuing"
    fi
    if ! run_with_timeout $PYTHON_BIN -m pip install -r requirements.txt; then
        print_warning "Dependency install failed; retry with network access"
    fi

    if ! $PYTHON_BIN -c "import aiogram, pydantic, pydantic_settings, cachetools, openai, cryptography" &> /dev/null; then
        print_error "Missing dependencies; please install requirements.txt and re-run."
        exit 1
    fi
fi

# Run compileall to validate imports and syntax
print_info "Running compileall..."
if ! $PYTHON_BIN -m compileall app; then
    print_error "Compileall failed"
    exit 1
fi

# Run database health check
print_info "Checking database..."
if $PYTHON_BIN -c "
import sys
sys.path.insert(0, '.')
from app.db import DB
from app.config import settings

try:
    db = DB(settings.db_path)
    ok = db.health_check()
    print('Database OK' if ok else 'Database health check failed')
    sys.exit(0 if ok else 1)
except Exception as exc:
    print(f'Database check error: {exc}')
    sys.exit(1)
"; then
    print_info "Database is healthy"
else
    print_error "Database health check failed"
    exit 1
fi

print_info "Running smoke check..."
if ! PYTHONPATH=. $PYTHON_BIN scripts/smoke_check.py; then
    print_error "Smoke check failed"
    exit 1
fi

print_info "Starting The Resolver bot..."
print_info "Press Ctrl+C to stop"

RUN_PYTHON="python"
if ! command -v "$RUN_PYTHON" &> /dev/null; then
    RUN_PYTHON="$PYTHON_BIN"
fi

PYTHONPATH=. $RUN_PYTHON -u -m app.main
