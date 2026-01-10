#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# THE RESOLVER BOT - INSTALLATION SCRIPT
# ============================================================================
# Author: Justadudeinspace
# Cross-platform installation for Termux (Android), Linux, macOS, and Windows WSL
# ============================================================================

if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    MAGENTA='\033[0;35m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; MAGENTA=''; NC=''
fi

print_step() { echo -e "${MAGENTA}▶${NC} $1"; }
print_info() { echo -e "${CYAN}➤${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

# Detect platform
if [ -f /system/build.prop ] && [ -d /data/data/com.termux/files/usr ]; then
    PLATFORM="termux"
elif [ "$(uname -s)" = "Linux" ]; then
    if grep -qi microsoft /proc/version 2>/dev/null || [[ "$(uname -r)" == *microsoft* ]]; then
        PLATFORM="wsl"
    else
        PLATFORM="linux"
    fi
elif [ "$(uname -s)" = "Darwin" ]; then
    PLATFORM="macos"
else
    PLATFORM="unknown"
fi

print_banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║            THE RESOLVER BOT - INSTALLATION                   ║"
    echo "║            Say the right thing without escalating            ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo -e "${YELLOW}Platform: ${PLATFORM^^} | Directory: $(pwd)${NC}"
    echo ""
}

print_banner

print_step "Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON_BIN="python3"
elif command -v python &> /dev/null; then
    PYTHON_BIN="python"
else
    case $PLATFORM in
        termux)
            print_info "Installing Python3 in Termux..."
            pkg update -y && pkg upgrade -y
            pkg install -y python3
            PYTHON_BIN="python3"
            ;;
        *)
            print_error "Python3 not found!"
            exit 1
            ;;
    esac
fi
print_success "Python found"

print_step "Installing system dependencies..."
run_optional() {
    if command -v timeout &> /dev/null; then
        if ! timeout 30 "$@"; then
            print_warning "Command failed (continuing): $*"
            return 0
        fi
    else
        if ! "$@"; then
            print_warning "Command failed (continuing): $*"
            return 0
        fi
    fi
}

case $PLATFORM in
    termux)
        run_optional pkg update -y
        run_optional pkg upgrade -y
        run_optional pkg install -y python3 sqlite git
        ;;
    linux|wsl)
        SUDO_CMD=""
        if command -v sudo &> /dev/null; then
            SUDO_CMD="sudo"
        fi

        if command -v apt-get &> /dev/null; then
            run_optional $SUDO_CMD apt-get update
            run_optional $SUDO_CMD apt-get install -y python3-pip python3-venv sqlite3 git
        elif command -v yum &> /dev/null; then
            run_optional $SUDO_CMD yum install -y python3-pip sqlite git
        elif command -v dnf &> /dev/null; then
            run_optional $SUDO_CMD dnf install -y python3-pip sqlite git
        elif command -v pacman &> /dev/null; then
            run_optional $SUDO_CMD pacman -S --noconfirm python-pip sqlite git
        else
            print_warning "No supported package manager found; skipping system dependency install"
        fi
        ;;
    macos)
        if command -v brew &> /dev/null; then
            run_optional brew install python3 sqlite3 git
        else
            print_warning "Homebrew not found; skipping system dependency install"
        fi
        ;;
    *)
        print_warning "Unknown platform; skipping system dependency install"
        ;;
esac
print_success "System dependency step completed"

print_step "Installing Python requirements..."
if [ ! -f "requirements.txt" ]; then
    print_error "requirements.txt not found!"
    exit 1
fi

VENV_DIR=".venv"
VENV_PYTHON="$PYTHON_BIN"

if [ "$PLATFORM" != "termux" ]; then
    if [ ! -d "$VENV_DIR" ]; then
        print_info "Creating virtual environment..."
        run_optional "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    if [ -x "$VENV_DIR/bin/python" ]; then
        VENV_PYTHON="$VENV_DIR/bin/python"
    else
        print_error "Virtual environment creation failed"
        exit 1
    fi
fi

run_optional "$VENV_PYTHON" -m pip install --upgrade pip

if [ "$PLATFORM" = "termux" ]; then
    print_info "Installing packages individually for Termux..."
    run_optional "$VENV_PYTHON" -m pip install aiogram python-dotenv pydantic pydantic-settings cachetools openai cryptography
else
    run_optional "$VENV_PYTHON" -m pip install -r requirements.txt
fi
print_success "Python requirements installed"

print_step "Setting up environment..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_success "Created .env from template"
        print_info "Edit .env with BOT_TOKEN, OPENAI_API_KEY, and INVOICE_SECRET"
    else
        print_error ".env.example not found!"
        exit 1
    fi
else
    print_info ".env already exists; leaving as-is"
fi

if [ -f "run_resolver.sh" ]; then
    chmod +x run_resolver.sh
    print_success "Run script is ready"
else
    print_error "run_resolver.sh not found!"
    exit 1
fi

print_success "INSTALLATION COMPLETE"

echo ""
echo "Next steps:"
echo "1) Edit .env with your BOT_TOKEN and INVOICE_SECRET"
echo "2) Start the bot: ./run_resolver.sh"
