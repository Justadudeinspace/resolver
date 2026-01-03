#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# THE RESOLVER BOT - INSTALLATION SCRIPT
# ============================================================================
# Author: Justadudeinspace 
# Email: theoutervoid@outlook.com
# 
# Cross-platform installation for Termux (Android), Linux, macOS, and Windows WSL
# ============================================================================

# Colors for output (Termux-safe colors)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    MAGENTA='\033[0;35m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; MAGENTA=''; BLUE=''; NC=''
fi

# Detect platform
detect_platform() {
    if [ -f /system/build.prop ] && [ -d /data/data/com.termux/files/usr ]; then
        echo "termux"
    elif [ "$(uname -s)" = "Linux" ]; then
        if grep -qi microsoft /proc/version 2>/dev/null || [[ "$(uname -r)" == *microsoft* ]]; then
            echo "wsl"
        else
            echo "linux"
        fi
    elif [ "$(uname -s)" = "Darwin" ]; then
        echo "macos"
    elif [[ "$(uname -s)" =~ MINGW|MSYS|CYGWIN ]]; then
        echo "windows"
    else
        echo "unknown"
    fi
}

PLATFORM=$(detect_platform)

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

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_info() {
    echo -e "${CYAN}➤${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_input() {
    echo -e "${BLUE}?${NC} $1"
}

print_step() {
    echo -e "${MAGENTA}▶${NC} $1"
}

# Interactive input
ask_yes_no() {
    local prompt="$1"
    local default="${2:-}"
    local answer
    
    while true; do
        print_input "$prompt (y/N)"
        if [[ -n "$default" ]]; then
            read -rp " [$default]: " answer
            answer="${answer:-$default}"
        else
            read -rp " " answer
        fi
        
        case "$answer" in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            "" ) if [[ "$default" == "y" ]]; then return 0; else return 1; fi;;
            * ) echo "Please answer yes (y) or no (n).";;
        esac
    done
}

ask_input() {
    local prompt="$1"
    local default="${2:-}"
    local answer
    
    print_input "$prompt"
    if [[ -n "$default" ]]; then
        read -rp " [$default]: " answer
        answer="${answer:-$default}"
    else
        read -rp " " answer
    fi
    
    echo "$answer"
}

# Check Python
check_python() {
    print_step "Checking Python..."
    
    case $PLATFORM in
        termux)
            if ! command -v python3 &> /dev/null; then
                print_info "Installing Python3 in Termux..."
                pkg update -y && pkg upgrade -y
                pkg install -y python3
            fi
            PYTHON_CMD="python3"
            ;;
        *)
            if ! command -v python3 &> /dev/null; then
                print_error "Python3 not found!"
                exit 1
            fi
            PYTHON_CMD="python3"
            ;;
    esac
    
    print_success "Python found"
    export PYTHON_CMD
}

# Install system dependencies
install_system_deps() {
    print_step "Installing system dependencies..."
    
    case $PLATFORM in
        termux)
            pkg update -y && pkg upgrade -y
            pkg install -y python3 sqlite git
            ;;
        linux|wsl)
            if command -v apt-get &> /dev/null; then
                sudo apt-get update
                sudo apt-get install -y python3-pip python3-venv sqlite3 git
            elif command -v yum &> /dev/null; then
                sudo yum install -y python3-pip sqlite git
            elif command -v dnf &> /dev/null; then
                sudo dnf install -y python3-pip sqlite git
            elif command -v pacman &> /dev/null; then
                sudo pacman -S --noconfirm python-pip sqlite git
            fi
            ;;
        macos)
            if command -v brew &> /dev/null; then
                brew install python3 sqlite3 git
            fi
            ;;
    esac
    
    print_success "System dependencies installed"
}

# Install Python requirements
install_requirements() {
    print_step "Installing Python requirements..."
    
    if [ ! -f "requirements.txt" ]; then
        print_error "requirements.txt not found!"
        exit 1
    fi
    
    # Upgrade pip first
    print_info "Upgrading pip..."
    pip install --upgrade pip
    
    # Install requirements
    print_info "Installing from requirements.txt..."
    
    case $PLATFORM in
        termux)
            # Termux might need special handling for cryptography
            print_info "Installing packages individually for Termux..."
            pip install aiogram python-dotenv pydantic pydantic-settings cachetools openai
            
            # Try cryptography, fallback if fails
            if ! pip install cryptography; then
                print_warning "Cryptography might need rust on Termux"
                print_info "If payments don't work, install rust: pkg install rust"
                print_info "Then run: pip install cryptography"
            fi
            ;;
        *)
            pip install -r requirements.txt
            ;;
    esac
    
    print_success "Python requirements installed"
}

# Setup .env file interactively
setup_env() {
    print_step "Setting up environment..."
    
    if [ ! -f ".env.example" ]; then
        print_error ".env.example not found!"
        exit 1
    fi
    
    # Copy .env.example to .env if it doesn't exist
    if [ ! -f ".env" ]; then
        cp .env.example .env
        print_success "Created .env from template"
    elif ask_yes_no ".env already exists. Overwrite?" "n"; then
        cp .env.example .env
        print_success "Overwrote .env"
    else
        print_info "Keeping existing .env"
        return
    fi
    
    echo ""
    print_info "=== ENVIRONMENT SETUP ==="
    echo ""
    
    # Ask for BOT_TOKEN
    echo -e "${YELLOW}┌──────────────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│ 1. TELEGRAM BOT TOKEN (Required)                     │${NC}"
    echo -e "${YELLOW}└──────────────────────────────────────────────────────┘${NC}"
    echo "Get from @BotFather on Telegram:"
    echo "1. Open Telegram, search @BotFather"
    echo "2. Send /newbot"
    echo "3. Choose name & username (ends with 'bot')"
    echo "4. Copy token (looks like: 1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ)"
    echo ""
    
    BOT_TOKEN=$(ask_input "Enter your bot token:")
    if [ -n "$BOT_TOKEN" ]; then
        sed -i "s|BOT_TOKEN=.*|BOT_TOKEN=$BOT_TOKEN|" .env
        print_success "Bot token saved"
    else
        print_warning "No bot token entered. You must edit .env manually."
    fi
    
    echo ""
    
    # Ask for OPENAI_API_KEY
    echo -e "${YELLOW}┌──────────────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│ 2. OPENAI API KEY (Optional but Recommended)         │${NC}"
    echo -e "${YELLOW}└──────────────────────────────────────────────────────┘${NC}"
    echo "Get from https://platform.openai.com/api-keys"
    echo "Without this, bot uses template responses (no AI)"
    echo "With this, bot uses GPT-4o-mini for personalized responses"
    echo ""
    
    if ask_yes_no "Add OpenAI API key?" "n"; then
        OPENAI_KEY=$(ask_input "Enter OpenAI API key (starts with sk-):")
        if [ -n "$OPENAI_KEY" ]; then
            sed -i "s|OPENAI_API_KEY=.*|OPENAI_API_KEY=$OPENAI_KEY|" .env
            print_success "OpenAI API key saved"
        fi
    else
        print_info "Skipping OpenAI API key. Bot will use template responses."
        # Clear any existing OpenAI key
        sed -i "s|OPENAI_API_KEY=.*|OPENAI_API_KEY=|" .env
    fi
    
    echo ""
    
    # Generate INVOICE_SECRET if needed
    echo -e "${YELLOW}┌──────────────────────────────────────────────────────┐${NC}"
    echo -e "${YELLOW}│ 3. PAYMENT SECURITY                                 │${NC}"
    echo -e "${YELLOW}└──────────────────────────────────────────────────────┘${NC}"
    
    if grep -q "generate_a_secure_random_string" .env; then
        print_info "Generating secure invoice secret..."
        if command -v python3 &> /dev/null; then
            INVOICE_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
            sed -i "s|INVOICE_SECRET=.*|INVOICE_SECRET=$INVOICE_SECRET|" .env
            print_success "Invoice secret generated"
        else
            print_warning "Could not generate invoice secret (python3 not found)"
            print_warning "You must generate one manually in .env"
        fi
    fi
    
    print_success "Environment setup complete"
}

# Create run.sh if it doesn't exist
create_run_sh() {
    print_step "Creating run script..."
    
    if [ ! -f "run.sh" ]; then
        cat > run.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check directory
if [ ! -f "app/main.py" ]; then
    print_error "Could not find app/main.py"
    exit 1
fi

# Check .env
if [ ! -f ".env" ]; then
    print_error ".env not found. Run ./install_resolver.sh first"
    exit 1
fi

# Check bot token
if grep -q "your_bot_token_here" .env; then
    print_error "You need to set BOT_TOKEN in .env file"
    exit 1
fi

# Generate invoice secret if needed
if grep -q "generate_a_secure_random_string" .env; then
    print_info "Generating invoice secret..."
    INVOICE_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || echo "")
    if [ -n "$INVOICE_SECRET" ]; then
        sed -i "s|INVOICE_SECRET=.*|INVOICE_SECRET=$INVOICE_SECRET|" .env
    fi
fi

# Run the bot
print_info "Starting The Resolver Bot..."
print_info "Press Ctrl+C to stop"
echo ""

python3 -m app.main
EOF
        
        chmod +x run.sh
        print_success "Created run.sh"
    else
        print_info "run.sh already exists"
    fi
}

# Verify installation
verify_installation() {
    print_step "Verifying installation..."
    
    # Check Python imports
    if python3 -c "import aiogram, pydantic, openai, cryptography" &> /dev/null; then
        print_success "All Python packages imported successfully"
    else
        print_warning "Some packages failed to import"
    fi
    
    # Check .env
    if [ -f ".env" ]; then
        print_success ".env file exists"
    else
        print_error ".env file missing"
    fi
    
    # Check app files
    if [ -f "app/main.py" ]; then
        print_success "Bot source code found"
    else
        print_error "app/main.py not found - you need to copy bot files to app/"
    fi
}

# Main installation
main() {
    print_banner
    
    print_info "Installing The Resolver Bot..."
    echo ""
    
    # Step 1: Check Python
    check_python
    
    # Step 2: Install system dependencies
    install_system_deps
    
    # Step 3: Install Python requirements
    install_requirements
    
    # Step 4: Setup .env interactively
    setup_env
    
    # Step 5: Create run.sh
    create_run_sh
    
    # Step 6: Verify
    verify_installation
    
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    print_success "INSTALLATION COMPLETE!"
    echo ""
    
    case $PLATFORM in
        termux)
            echo "To start the bot:"
            echo "  ${GREEN}./run.sh${NC}"
            echo ""
            echo "To run in background:"
            echo "  ${GREEN}nohup ./run.sh > bot.log 2>&1 &${NC}"
            echo ""
            echo "View logs:"
            echo "  ${GREEN}tail -f bot.log${NC}"
            ;;
        *)
            echo "To start the bot:"
            echo "  ${GREEN}./run.sh${NC}"
            echo ""
            echo "For production (with PM2):"
            echo "  ${GREEN}npm install -g pm2${NC}"
            echo "  ${GREEN}pm2 start run.sh --name resolver-bot${NC}"
            ;;
    esac
    
    echo ""
    echo "Next steps:"
    echo "1. Test bot: ${GREEN}./run.sh${NC}"
    echo "2. Edit .env: ${GREEN}nano .env${NC} (if needed)"
    echo "3. Monitor: Check logs for errors"
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Run main
main "$@"
EOF
