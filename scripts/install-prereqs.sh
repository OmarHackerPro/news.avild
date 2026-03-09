#!/usr/bin/env bash
# news.avild.com — Prerequisites Installer (macOS / Linux)
# Usage: bash install-prereqs.sh

set -euo pipefail

GREEN="\033[0;32m"; CYAN="\033[0;36m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; RESET="\033[0m"

step() { echo -e "\n${CYAN}>> $1${RESET}"; }
ok()   { echo -e "   ${GREEN}[OK] $1${RESET}"; }
skip() { echo -e "   [SKIP] $1 already installed."; }
fail() { echo -e "   ${RED}[FAIL] $1${RESET}"; exit 1; }

echo ""
echo -e "${CYAN}======================================${RESET}"
echo -e "${CYAN}  news.avild.com  — prereqs installer${RESET}"
echo -e "${CYAN}======================================${RESET}"

OS="$(uname -s)"

# ─────────────────────────────────────────────────────────────────────────────
# macOS
# ─────────────────────────────────────────────────────────────────────────────
install_macos() {
    # Homebrew
    step "Homebrew"
    if ! command -v brew &>/dev/null; then
        echo "   Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to PATH for Apple Silicon
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
        ok "Homebrew installed."
    else
        skip "Homebrew ($(brew --version | head -1))"
    fi

    # Git
    step "Git"
    if command -v git &>/dev/null; then
        skip "git ($(git --version))"
    else
        brew install git && ok "Git installed."
    fi

    # Docker Desktop
    step "Docker Desktop"
    if command -v docker &>/dev/null; then
        skip "Docker ($(docker --version))"
    else
        brew install --cask docker && ok "Docker Desktop installed."
        echo -e "   ${YELLOW}Open Docker Desktop from Applications and wait for the engine to start.${RESET}"
    fi

    # Python
    step "Python 3"
    if command -v python3 &>/dev/null && python3 -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
        skip "Python ($(python3 --version))"
    else
        brew install python@3.13 && ok "Python 3.13 installed."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Linux
# ─────────────────────────────────────────────────────────────────────────────
install_linux() {
    if command -v apt-get &>/dev/null; then
        install_debian
    elif command -v dnf &>/dev/null; then
        install_fedora
    else
        fail "Unsupported package manager. Install Git, Docker, and Python 3.12+ manually, then continue with README.md."
    fi
}

install_debian() {
    sudo apt-get update -qq

    # Git
    step "Git"
    if command -v git &>/dev/null; then
        skip "git ($(git --version))"
    else
        sudo apt-get install -y git && ok "Git installed."
    fi

    # Docker
    step "Docker"
    if command -v docker &>/dev/null; then
        skip "Docker ($(docker --version))"
    else
        echo "   Installing Docker via official install script..."
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        ok "Docker installed."
        echo -e "   ${YELLOW}Log out and back in so the docker group takes effect (or run: newgrp docker).${RESET}"
    fi

    # Python
    step "Python 3.13"
    if command -v python3 &>/dev/null && python3 -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
        skip "Python ($(python3 --version))"
    else
        echo "   Adding deadsnakes PPA for Python 3.13..."
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt-get update -qq
        sudo apt-get install -y python3.13 python3.13-venv python3.13-dev
        ok "Python 3.13 installed."
    fi
}

install_fedora() {
    # Git
    step "Git"
    if command -v git &>/dev/null; then
        skip "git ($(git --version))"
    else
        sudo dnf install -y git && ok "Git installed."
    fi

    # Docker
    step "Docker"
    if command -v docker &>/dev/null; then
        skip "Docker ($(docker --version))"
    else
        echo "   Installing Docker via official install script..."
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        sudo systemctl enable --now docker
        ok "Docker installed."
        echo -e "   ${YELLOW}Log out and back in so the docker group takes effect.${RESET}"
    fi

    # Python
    step "Python 3.13"
    if command -v python3 &>/dev/null && python3 -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
        skip "Python ($(python3 --version))"
    else
        sudo dnf install -y python3.13 && ok "Python 3.13 installed."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────
case "$OS" in
    Darwin) install_macos ;;
    Linux)  install_linux ;;
    *)      fail "Unsupported OS: $OS" ;;
esac

echo ""
echo -e "${GREEN}======================================${RESET}"
echo -e "${GREEN}  All prerequisites installed!${RESET}"
echo -e "${GREEN}======================================${RESET}"
echo ""
echo -e "${YELLOW}NEXT STEPS:${RESET}"
echo "  Follow the setup steps in README.md."
echo ""
