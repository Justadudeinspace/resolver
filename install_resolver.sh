#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# ============================================================================
# THE RESOLVER BOT - INSTALLATION SCRIPT
# ============================================================================
# Cross-platform installation for Termux (Android), Linux, macOS, and WSL
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

CURRENT_STEP="startup"

print_step() { echo -e "${MAGENTA}▶${NC} $1"; }
print_info() { echo -e "${CYAN}➤${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

on_error() {
    local line_number=$1
    print_error "Error in step '${CURRENT_STEP}' at line ${line_number}."
    exit 1
}

trap 'on_error $LINENO' ERR

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

get_uname_o() {
    if uname -o >/dev/null 2>&1; then
        uname -o
    else
        echo ""
    fi
}

getprop_value() {
    local prop="$1"
    if command_exists getprop; then
        getprop "$prop"
    elif [ -x /system/bin/getprop ]; then
        /system/bin/getprop "$prop"
    else
        echo ""
    fi
}

is_android() {
    local uname_o
    uname_o="$(get_uname_o)"
    if [ "$uname_o" = "Android" ]; then
        return 0
    fi
    [ -n "$(getprop_value ro.build.version.release)" ]
}

is_termux_native() {
    if ! is_android; then
        return 1
    fi

    if [ -n "${PREFIX-}" ] && [[ "$PREFIX" == /data/data/com.termux/files/usr* ]]; then
        return 0
    fi
    if [ -n "${TERMUX_VERSION-}" ]; then
        return 0
    fi
    if [ -x /data/data/com.termux/files/usr/bin/pkg ]; then
        local uname_o
        uname_o="$(get_uname_o)"
        if [ "$uname_o" = "Android" ]; then
            return 0
        fi
    fi
    return 1
}

is_proot_distro() {
    if [ -n "${PREFIX-}" ] && [[ "$PREFIX" == /data/data/com.termux/files/usr* ]]; then
        return 1
    fi
    if [ ! -f /etc/os-release ]; then
        return 1
    fi
    if ! grep -Eq "^ID=(debian|ubuntu)$" /etc/os-release; then
        return 1
    fi
    if uname -r 2>/dev/null | grep -q "Android"; then
        return 0
    fi
    if grep -Eq "Android" /proc/version 2>/dev/null; then
        return 0
    fi
    if [ -x /usr/bin/proot ] || grep -q "TracerPid" /proc/self/status 2>/dev/null; then
        return 0
    fi
    return 1
}

is_wsl() {
    [ -n "${WSL_INTEROP-}" ] || grep -qi microsoft /proc/version 2>/dev/null
}

is_macos() {
    [ "$(uname -s)" = "Darwin" ]
}

print_banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║            THE RESOLVER BOT - INSTALLATION                   ║"
    echo "║            Say the right thing without escalating            ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

detect_env() {
    local env_reason=""
    local android="false"
    if is_android; then
        android="true"
    fi

    if is_termux_native; then
        ENVIRONMENT="TERMUX"
        env_reason="Termux app detected (PREFIX/TERMUX_VERSION/pkg + Android)"
    elif is_proot_distro; then
        ENVIRONMENT="PROOT"
        env_reason="Android host with Debian/Ubuntu userland (/etc/os-release) inside proot"
    elif is_macos; then
        ENVIRONMENT="MACOS"
        env_reason="uname=Darwin"
    else
        ENVIRONMENT="LINUX"
        env_reason="defaulting to Linux"
    fi

    if [ "$android" = "true" ] && [ "$ENVIRONMENT" = "LINUX" ]; then
        print_error "Android detected but not Termux native or a supported proot Debian/Ubuntu."
        print_error "Run this script inside the Termux app or a Termux proot-distro Debian/Ubuntu container."
        print_error "Debug: uname -a -> $(uname -a 2>/dev/null || echo 'unavailable')"
        if [ -f /etc/os-release ]; then
            print_error "Debug: /etc/os-release ->"
            sed 's/^/  /' /etc/os-release >&2
        else
            print_error "Debug: /etc/os-release -> missing"
        fi
        print_error "Debug: PREFIX='${PREFIX-}'"
        exit 1
    fi

    WSL="false"
    if is_wsl; then
        WSL="true"
    fi

    print_info "Detected environment: ${ENVIRONMENT} because ${env_reason}"
    if [ "$WSL" = "true" ]; then
        print_info "Environment detail: WSL"
    fi
}

require_command() {
    local cmd="$1"
    local message="$2"
    if ! command_exists "$cmd"; then
        print_error "$message"
        exit 1
    fi
}

package_installed() {
    local pkg_name="$1"
    if command_exists dpkg; then
        dpkg -s "$pkg_name" >/dev/null 2>&1
    else
        return 1
    fi
}

get_sudo_cmd() {
    if [ "$(id -u)" -eq 0 ]; then
        echo ""
        return
    fi

    if command_exists sudo; then
        echo "sudo"
        return
    fi

    print_warning "sudo not found and not running as root; package installs may fail."
    echo ""
}

update_system() {
    CURRENT_STEP="update_system"
    print_step "Updating system packages..."

    case "$ENVIRONMENT" in
        TERMUX)
            pkg update -y
            ;;
        PROOT|LINUX)
            local sudo_cmd
            sudo_cmd="$(get_sudo_cmd)"
            if command_exists apt-get; then
                ${sudo_cmd} apt-get update -y
            else
                print_error "apt-get not found. Install python3, python3-venv, python3-pip, git, sqlite3, ca-certificates."
                exit 1
            fi
            ;;
        MACOS)
            print_info "Skipping automatic updates on macOS."
            ;;
        *)
            print_error "Unsupported environment. Exiting."
            exit 1
            ;;
    esac

    print_success "System update complete"
}

install_system_deps() {
    CURRENT_STEP="install_system_deps"
    print_step "Installing system dependencies..."

    case "$ENVIRONMENT" in
        TERMUX)
            local missing=()
            local termux_packages=(python3 git openssl libffi sqlite)
            for pkg_name in "${termux_packages[@]}"; do
                if ! package_installed "$pkg_name"; then
                    missing+=("$pkg_name")
                fi
            done
            if [ "${#missing[@]}" -gt 0 ]; then
                pkg install -y "${missing[@]}"
            else
                print_info "System dependencies already installed."
            fi
            ;;
        PROOT|LINUX)
            local sudo_cmd
            sudo_cmd="$(get_sudo_cmd)"
            local deb_packages=(python3 python3-venv python3-pip git sqlite3 ca-certificates)
            local missing=()
            for pkg_name in "${deb_packages[@]}"; do
                if ! package_installed "$pkg_name"; then
                    missing+=("$pkg_name")
                fi
            done
            if [ "${#missing[@]}" -gt 0 ]; then
                if command_exists apt-get; then
                    ${sudo_cmd} apt-get install -y "${missing[@]}"
                else
                    print_error "apt-get not found. Install: ${missing[*]}"
                    exit 1
                fi
            else
                print_info "System dependencies already installed."
            fi
            ;;
        MACOS)
            if ! command_exists python3; then
                print_error "python3 not found. Install Python 3.9+ and re-run."
                exit 1
            fi
            if ! command_exists pip3; then
                print_error "pip3 not found. Install pip for Python 3 and re-run."
                exit 1
            fi
            if ! command_exists git; then
                print_error "git not found. Install git and re-run."
                exit 1
            fi
            ;;
        *)
            print_error "Unsupported environment. Exiting."
            exit 1
            ;;
    esac

    print_success "System dependencies installed"
}

select_python() {
    if command_exists python3; then
        PY_BIN="python3"
        return
    fi

    print_error "python3 is not installed. Please install Python 3.9+."
    exit 1
}

ensure_python_version() {
    local python_cmd="$1"

    local version_check
    version_check=$($python_cmd - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)

    local major=${version_check%%.*}
    local minor=${version_check##*.}

    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 9 ]; }; then
        print_error "Python 3.9+ is required. Detected ${version_check}."
        exit 1
    fi
}

setup_python_env() {
    CURRENT_STEP="setup_python_env"
    print_step "Setting up Python environment..."

    select_python
    ensure_python_version "$PY_BIN"
    if ! "$PY_BIN" -m pip --version >/dev/null 2>&1; then
        print_error "pip is not available for $PY_BIN."
        exit 1
    fi
    if [ "$ENVIRONMENT" = "TERMUX" ]; then
        print_info "Using Python executable: $PY_BIN"
    fi

    VENV_DIR=".venv"
    VENV_PYTHON="$PY_BIN"
    USE_VENV="false"

    if [ "$ENVIRONMENT" = "TERMUX" ]; then
        if [ -d "$VENV_DIR" ]; then
            if [ -x "$VENV_DIR/bin/python" ]; then
                USE_VENV="true"
                VENV_PYTHON="$VENV_DIR/bin/python"
            else
                print_warning "Existing virtual environment is invalid; removing."
                rm -rf "$VENV_DIR"
            fi
        fi

        if [ "$USE_VENV" = "false" ]; then
            if "$PY_BIN" -m venv "$VENV_DIR" >/dev/null 2>&1; then
                if [ -x "$VENV_DIR/bin/python" ]; then
                    USE_VENV="true"
                    VENV_PYTHON="$VENV_DIR/bin/python"
                    print_info "Virtual environment created in Termux."
                else
                    rm -rf "$VENV_DIR"
                fi
            else
                rm -rf "$VENV_DIR"
                print_warning "Termux venv unavailable; falling back to user installs."
            fi
        fi
    else
        if [ ! -d "$VENV_DIR" ]; then
            "$PY_BIN" -m venv "$VENV_DIR"
        fi
        if [ -x "$VENV_DIR/bin/python" ]; then
            USE_VENV="true"
            VENV_PYTHON="$VENV_DIR/bin/python"
        else
            print_error "Virtual environment creation failed."
            exit 1
        fi
    fi

    print_success "Python environment ready"
}

requirements_hash() {
    if command_exists sha256sum; then
        sha256sum requirements.txt | awk '{print $1}'
    elif command_exists shasum; then
        shasum -a 256 requirements.txt | awk '{print $1}'
    else
        echo ""
    fi
}

install_python_deps() {
    CURRENT_STEP="install_python_deps"
    print_step "Installing Python requirements..."

    if [ ! -f "requirements.txt" ]; then
        print_error "requirements.txt not found."
        exit 1
    fi

    local pip_cmd
    if [ "$USE_VENV" = "true" ]; then
        "$VENV_PYTHON" -m pip install -U pip
        pip_cmd=("$VENV_PYTHON" -m pip install -r requirements.txt)
    else
        pip_cmd=("$PY_BIN" -m pip install --user -r requirements.txt)
    fi

    local marker_file=".requirements.hash"
    local current_hash
    current_hash="$(requirements_hash)"

    if [ -n "$current_hash" ] && [ -f "$marker_file" ]; then
        local previous_hash
        previous_hash="$(cat "$marker_file")"
        if [ "$current_hash" = "$previous_hash" ]; then
            print_info "Python requirements unchanged; skipping install."
            print_success "Python requirements ready"
            return
        fi
    fi

    "${pip_cmd[@]}"

    if [ -n "$current_hash" ]; then
        echo "$current_hash" > "$marker_file"
    fi

    print_success "Python requirements installed"
}

setup_project_files() {
    CURRENT_STEP="setup_project_files"
    print_step "Setting up project files..."

    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_success "Created .env from template"
            print_info "Edit .env with BOT_TOKEN and optional OPENAI_API_KEY"
        else
            print_error ".env.example not found."
            exit 1
        fi
    else
        print_info ".env already exists; leaving as-is"
    fi

    mkdir -p data logs

    chmod +x install_resolver.sh

    if [ -f "run_resolver.sh" ]; then
        chmod +x run_resolver.sh
        RUN_SCRIPT="./run_resolver.sh"
    elif [ -f "run.sh" ]; then
        chmod +x run.sh
        RUN_SCRIPT="./run.sh"
    else
        print_error "No run script found (run_resolver.sh or run.sh)."
        exit 1
    fi

    print_success "Project files ready"
}

verify_install() {
    CURRENT_STEP="verify_install"
    print_step "Verifying installation..."

    "$VENV_PYTHON" -m compileall app
    "$VENV_PYTHON" - <<'PY'
from app.config import settings
from app.db import DB
print(DB(settings.db_path).health_check())
PY

    print_success "Verification complete"
}

main() {
    local detect_only="false"
    for arg in "$@"; do
        if [ "$arg" = "--detect-only" ]; then
            detect_only="true"
        fi
    done

    print_banner
    detect_env
    if [ "$detect_only" = "true" ]; then
        return 0
    fi

    update_system
    install_system_deps
    setup_python_env
    install_python_deps
    setup_project_files
    verify_install

    print_success "INSTALLATION COMPLETE"
    echo ""
    echo "Next steps:"
    echo "1) Edit .env with your BOT_TOKEN"
    echo "2) Start the bot: ${RUN_SCRIPT}"
}

main "$@"
