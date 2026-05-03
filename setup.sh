#!/data/data/com.termux/files/usr/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  walidcode v2 — Termux Installer
#  Local Multi-Agent Swarm Framework
#  Run once:  bash setup.sh
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[walidcode]${RESET} $*"; }
success() { echo -e "${GREEN}[✔]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✘]${RESET} $*"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}━━ $* ${RESET}"; }

# ── Banner ─────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat <<'BANNER'
 ██╗    ██╗ █████╗ ██╗     ██╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗
 ██║    ██║██╔══██╗██║     ██║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ██║ █╗ ██║███████║██║     ██║██║  ██║██║     ██║   ██║██║  ██║█████╗
 ██║███╗██║██╔══██║██║     ██║██║  ██║██║     ██║   ██║██║  ██║██╔══╝
 ╚███╔███╔╝██║  ██║███████╗██║██████╔╝╚██████╗╚██████╔╝██████╔╝███████╗
  ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚═╝╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
    v2.0 — Multi-Agent Swarm Framework — Termux Installer
BANNER
echo -e "${RESET}"

# ── Environment detection ──────────────────────────────────────────────────────
TERMUX_PREFIX="/data/data/com.termux/files/usr"
IS_TERMUX=false
[ -d "$TERMUX_PREFIX" ] && IS_TERMUX=true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "Project root : $SCRIPT_DIR"
info "Termux       : $IS_TERMUX"

# ── Storage permission (Termux) ────────────────────────────────────────────────
if $IS_TERMUX && [ ! -d "$HOME/storage" ]; then
    step "Storage Permission"
    info "Requesting storage access (Android dialog may appear) …"
    termux-setup-storage 2>/dev/null || warn "Storage permission failed — continuing."
    sleep 2
fi

# ── System packages ────────────────────────────────────────────────────────────
step "System Packages"
if $IS_TERMUX; then
    info "Updating pkg …"
    pkg update -y 2>/dev/null || warn "pkg update had errors (usually harmless)."

    for pkg_name in python git; do
        info "Installing $pkg_name …"
        pkg install -y "$pkg_name" 2>/dev/null || warn "$pkg_name install issue."
    done

    # tur-repo for modern Chromium
    info "Installing tur-repo (provides Chromium) …"
    pkg install -y tur-repo 2>/dev/null || warn "tur-repo failed — Chromium may be unavailable."

    info "Installing chromium …"
    pkg install -y chromium 2>/dev/null || warn "Chromium install failed."

    success "System packages ready."
else
    # Generic Linux
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y python3 python3-pip git chromium-browser 2>/dev/null \
            || sudo apt-get install -y python3 python3-pip git chromium 2>/dev/null \
            || warn "Some packages failed on apt."
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-pip git chromium
    elif command -v pacman &>/dev/null; then
        sudo pacman -Syu --noconfirm python python-pip git chromium
    else
        warn "Unknown package manager — ensure python3, git, and chromium are installed."
    fi
fi

# ── Python version check ───────────────────────────────────────────────────────
step "Python Check"
PY_CMD="python3"
command -v python3 &>/dev/null || PY_CMD="python"
PY_VERSION=$($PY_CMD --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
info "Python version: $PY_VERSION"
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ( [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ] ); then
    warn "Python 3.10+ is strongly recommended. Current: $PY_VERSION"
fi

# ── Python dependencies ────────────────────────────────────────────────────────
step "Python Dependencies"
info "Upgrading pip …"
$PY_CMD -m pip install --upgrade pip --quiet 2>/dev/null || true

REQ="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQ" ]; then
    info "Installing from requirements.txt …"
    $PY_CMD -m pip install -r "$REQ" --quiet \
        || error "pip install failed. Check requirements.txt."
else
    info "Installing core dependencies …"
    $PY_CMD -m pip install --quiet \
        "playwright>=1.44.0" \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.29.0" \
        "websockets>=12.0" \
        "httpx>=0.27.0" \
        "aiofiles>=23.2.0" \
        "pydantic>=2.7.0" \
        "textual>=0.60.0" \
        "rich>=13.7.0" \
        "click>=8.1.7" \
        "pathspec>=0.12.1" \
        "anyio>=4.3.0" \
        || error "pip install failed."
fi
success "Python dependencies installed."

# ── Playwright Chromium ────────────────────────────────────────────────────────
step "Playwright Browser"
if $IS_TERMUX; then
    info "Registering system Chromium with Playwright …"
    PLAYWRIGHT_BROWSERS_PATH="$HOME/.playwright-browsers" \
        $PY_CMD -m playwright install chromium 2>/dev/null \
        || warn "Playwright browser step had issues — will fall back to system chromium."
else
    info "Downloading Playwright Chromium …"
    $PY_CMD -m playwright install chromium \
        || error "playwright install chromium failed."
fi
success "Playwright setup complete."

# ── Create directory structure ─────────────────────────────────────────────────
step "Directory Structure"
mkdir -p "$SCRIPT_DIR"/{orchestrator,daemon,ui/static,ingestion,executor,skills/prompts}
[ -f "$SCRIPT_DIR/orchestrator/__init__.py" ] || echo "# orchestrator" > "$SCRIPT_DIR/orchestrator/__init__.py"
[ -f "$SCRIPT_DIR/daemon/__init__.py"       ] || echo "# daemon"       > "$SCRIPT_DIR/daemon/__init__.py"
[ -f "$SCRIPT_DIR/ui/__init__.py"           ] || echo "# ui"           > "$SCRIPT_DIR/ui/__init__.py"
[ -f "$SCRIPT_DIR/ingestion/__init__.py"    ] || echo "# ingestion"    > "$SCRIPT_DIR/ingestion/__init__.py"
[ -f "$SCRIPT_DIR/executor/__init__.py"     ] || echo "# executor"     > "$SCRIPT_DIR/executor/__init__.py"
# Ensure walidcode home exists
mkdir -p "$HOME/.walidcode"
success "Directory structure ready."

# ── Create the walidcode launcher ──────────────────────────────────────────────
step "Launcher Script"

if $IS_TERMUX; then
    LAUNCHER_PATH="$TERMUX_PREFIX/bin/walidcode"
    SHEBANG="#!/data/data/com.termux/files/usr/bin/bash"
else
    LAUNCHER_PATH="/usr/local/bin/walidcode"
    SHEBANG="#!/usr/bin/env bash"
fi

# Detect Python
PY_BIN="$(command -v python3 || command -v python)"

cat > "$LAUNCHER_PATH" << LAUNCHER_EOF
${SHEBANG}
# walidcode launcher — generated by setup.sh
WALIDCODE_DIR="${SCRIPT_DIR}"
export PYTHONPATH="\${WALIDCODE_DIR}:\${PYTHONPATH:-}"
exec "${PY_BIN}" "\${WALIDCODE_DIR}/main.py" "\$@"
LAUNCHER_EOF

chmod +x "$LAUNCHER_PATH"
success "Launcher created: $LAUNCHER_PATH"

# ── Verify installation ────────────────────────────────────────────────────────
step "Verification"

if walidcode --help &>/dev/null; then
    success "walidcode command working ✓"
else
    warn "walidcode command not found in PATH — you may need to run:"
    warn "  export PATH=\"\$PATH:$TERMUX_PREFIX/bin\""
fi

# Quick Python import test
$PY_CMD -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
try:
    from config import SwarmConfig, AgentConfig
    from orchestrator.swarm import SwarmOrchestrator
    from orchestrator.message_bus import MessageBus
    from ingestion.project_reader import ProjectReader
    print('  Core imports: OK')
except ImportError as e:
    print(f'  Import warning: {e}')
"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  walidcode v2 installed successfully! 🎉${RESET}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo -e ""
echo -e "  ${CYAN}# 1. Launch the swarm (interactive setup if no --agent flags)${RESET}"
echo -e "  ${BOLD}walidcode start${RESET}"
echo -e ""
echo -e "  ${CYAN}# Or specify agents directly:${RESET}"
echo -e "  ${BOLD}walidcode start \\${RESET}"
echo -e "    ${BOLD}-a 'coder|https://chat.deepseek.com|coder' \\${RESET}"
echo -e "    ${BOLD}-a 'reviewer|https://claude.ai|reviewer'${RESET}"
echo -e ""
echo -e "  ${CYAN}# 2. Open the TUI chat client (in another terminal / tmux pane)${RESET}"
echo -e "  ${BOLD}walidcode chat${RESET}"
echo -e ""
echo -e "  ${CYAN}# 3. Open the Web Dashboard${RESET}"
echo -e "  ${BOLD}walidcode web${RESET}"
echo -e ""
echo -e "  ${CYAN}# Check swarm status${RESET}"
echo -e "  ${BOLD}walidcode status${RESET}"
echo -e ""
echo -e "  ${CYAN}# Ingest a project directory into LLM context${RESET}"
echo -e "  ${BOLD}walidcode ingest ~/my_project${RESET}"
echo -e ""
echo -e "  ${CYAN}# Reinstall / update:${RESET}"
echo -e "  bash ${SCRIPT_DIR}/setup.sh"
echo ""
if $IS_TERMUX; then
    echo -e "  ${YELLOW}Termux tip:${RESET} Run the daemon in one pane and the TUI in another."
    echo -e "  Use tmux:  ${BOLD}pkg install tmux && tmux new -s walidcode${RESET}"
    echo ""
fi
