#!/data/data/com.termux/files/usr/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  walidcode v2 — Smart Termux Installer
#  يتحقق من وجود كل أداة قبل تثبيتها
#  Run: bash setup.sh
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[walidcode]${RESET} $*"; }
success() { echo -e "${GREEN}[✔]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✘]${RESET} $*"; exit 1; }
skip()    { echo -e "${GREEN}[✔]${RESET} $* ${YELLOW}(already installed — skipped)${RESET}"; }
step()    { echo -e "\n${BOLD}${CYAN}━━ $*${RESET}"; }

echo -e "${BOLD}${CYAN}"
cat <<'BANNER'
 ██╗    ██╗ █████╗ ██╗     ██╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗
 ██║    ██║██╔══██╗██║     ██║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ██║ █╗ ██║███████║██║     ██║██║  ██║██║     ██║   ██║██║  ██║█████╗
 ██║███╗██║██╔══██║██║     ██║██║  ██║██║     ██║   ██║██║  ██║██╔══╝
 ╚███╔███╔╝██║  ██║███████╗██║██████╔╝╚██████╗╚██████╔╝██████╔╝███████╗
  ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚═╝╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
    v2.0 — Smart Installer (checks before installing)
BANNER
echo -e "${RESET}"

# ── Helper: check if a CLI command exists ──────────────────────────────────────
has_cmd() { command -v "$1" &>/dev/null; }

# ── Helper: check if a Termux pkg is installed ────────────────────────────────
pkg_installed() { pkg list-installed 2>/dev/null | grep -q "^$1/"; }

# ── Helper: check if a Python module is importable ────────────────────────────
py_has() { "$PY_CMD" -c "import $1" &>/dev/null 2>&1; }

# ── Detect environment ─────────────────────────────────────────────────────────
TERMUX_PREFIX="/data/data/com.termux/files/usr"
IS_TERMUX=false
[ -d "$TERMUX_PREFIX" ] && IS_TERMUX=true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "Project root : $SCRIPT_DIR"
info "Termux       : $IS_TERMUX"

# Pick Python binary
if   has_cmd python3; then PY_CMD="python3"
elif has_cmd python;  then PY_CMD="python"
else PY_CMD="python3"; fi

# ══════════════════════════════════════════════════════════════════════════════
# 1. Storage permission
# ══════════════════════════════════════════════════════════════════════════════
step "Storage Permission"
if $IS_TERMUX; then
    if [ -d "$HOME/storage" ]; then
        skip "storage access"
    else
        info "Requesting storage access ..."
        termux-setup-storage 2>/dev/null || warn "Failed — continuing."
        sleep 2
    fi
else
    skip "storage (not Termux)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 2. System packages — each checked individually
# ══════════════════════════════════════════════════════════════════════════════
step "System Packages"

if $IS_TERMUX; then

    # pkg update — only if not done in last 24 h
    STAMP="$HOME/.walidcode/.pkg_updated"
    mkdir -p "$(dirname "$STAMP")"
    NOW=$(date +%s)
    LAST=0
    [ -f "$STAMP" ] && LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
    if [ $(( NOW - LAST )) -lt 86400 ]; then
        skip "pkg update (ran within 24 h)"
    else
        info "Running pkg update ..."
        pkg update -y 2>/dev/null || warn "pkg update had errors (usually harmless)."
        echo "$NOW" > "$STAMP"
    fi

    # python
    if has_cmd python || has_cmd python3; then
        skip "python"
    else
        info "Installing python ..."
        pkg install -y python 2>/dev/null || error "Failed to install python."
        success "python installed."
    fi
    has_cmd python3 && PY_CMD="python3" || PY_CMD="python"

    # git
    if has_cmd git; then
        skip "git"
    else
        info "Installing git ..."
        pkg install -y git 2>/dev/null || warn "git install failed."
        success "git installed."
    fi

    # tur-repo
    if pkg_installed "tur-repo"; then
        skip "tur-repo"
    else
        info "Installing tur-repo (needed for Chromium) ..."
        pkg install -y tur-repo 2>/dev/null || warn "tur-repo failed."
    fi

    # Chromium — check all known paths
    CHROMIUM_BIN=""
    for c in \
        "/data/data/com.termux/files/usr/bin/chromium-browser" \
        "/data/data/com.termux/files/usr/bin/chromium"; do
        [ -x "$c" ] && { CHROMIUM_BIN="$c"; break; }
    done
    has_cmd chromium-browser && CHROMIUM_BIN="$(command -v chromium-browser)"
    has_cmd chromium          && [ -z "$CHROMIUM_BIN" ] && CHROMIUM_BIN="$(command -v chromium)"

    if [ -n "$CHROMIUM_BIN" ]; then
        skip "chromium ($CHROMIUM_BIN)"
    else
        info "Installing chromium ..."
        pkg install -y chromium 2>/dev/null || warn "Chromium install failed."
        success "chromium step done."
    fi

else
    # Generic Linux — only install what is missing
    MISSING=()
    has_cmd python3   || MISSING+=("python3")
    has_cmd git       || MISSING+=("git")
    { has_cmd chromium || has_cmd chromium-browser || has_cmd google-chrome; } \
        && skip "chromium" \
        || MISSING+=("chromium-browser")

    if [ ${#MISSING[@]} -eq 0 ]; then
        skip "all system packages"
    else
        info "Installing: ${MISSING[*]} ..."
        if   has_cmd apt-get; then sudo apt-get install -y "${MISSING[@]}"
        elif has_cmd dnf;     then sudo dnf install -y "${MISSING[@]}"
        elif has_cmd pacman;  then sudo pacman -S --noconfirm "${MISSING[@]}"
        else warn "Unknown package manager — install manually: ${MISSING[*]}"; fi
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# 3. Python version check
# ══════════════════════════════════════════════════════════════════════════════
step "Python Version"
PY_VERSION=$("$PY_CMD" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
info "Found Python $PY_VERSION via '$PY_CMD'"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    warn "Python 3.10+ recommended (found $PY_VERSION)."
else
    success "Python $PY_VERSION — OK"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 4. pip — upgrade only if old
# ══════════════════════════════════════════════════════════════════════════════
step "pip"
if "$PY_CMD" -m pip --version &>/dev/null; then
    CURRENT_PIP=$("$PY_CMD" -m pip --version | grep -oP '\d+' | head -1)
    if [ "${CURRENT_PIP:-0}" -ge 23 ]; then
        skip "pip (v$CURRENT_PIP)"
    else
        info "Upgrading pip (current: $CURRENT_PIP) ..."
        "$PY_CMD" -m pip install --upgrade pip --quiet 2>/dev/null || true
        success "pip upgraded."
    fi
else
    "$PY_CMD" -m ensurepip --upgrade 2>/dev/null || warn "ensurepip failed."
fi

# ══════════════════════════════════════════════════════════════════════════════
# 5. Python packages — each checked individually via import
# ══════════════════════════════════════════════════════════════════════════════
step "Python Packages"

# Format: "import_name|pip_install_spec"
PACKAGES=(
    "playwright|playwright>=1.44.0"
    "fastapi|fastapi>=0.111.0"
    "uvicorn|uvicorn[standard]>=0.29.0"
    "websockets|websockets>=12.0"
    "httpx|httpx>=0.27.0"
    "aiofiles|aiofiles>=23.2.0"
    "pydantic|pydantic>=2.7.0"
    "textual|textual>=0.60.0"
    "rich|rich>=13.7.0"
    "click|click>=8.1.7"
    "pathspec|pathspec>=0.12.1"
    "anyio|anyio>=4.3.0"
)

MISSING_PIP=()
for entry in "${PACKAGES[@]}"; do
    import_name="${entry%%|*}"
    pip_spec="${entry##*|}"
    if py_has "$import_name"; then
        skip "pip:$import_name"
    else
        info "Will install: $pip_spec"
        MISSING_PIP+=("$pip_spec")
    fi
done

if [ ${#MISSING_PIP[@]} -eq 0 ]; then
    success "All Python packages already installed."
else
    info "Installing ${#MISSING_PIP[@]} missing package(s) ..."
    "$PY_CMD" -m pip install "${MISSING_PIP[@]}" --quiet \
        || error "pip install failed."
    success "${#MISSING_PIP[@]} package(s) installed."
fi

# ══════════════════════════════════════════════════════════════════════════════
# 6. Playwright browser — only if not already present
# ══════════════════════════════════════════════════════════════════════════════
step "Playwright Browser"

PW_OK=false
if "$PY_CMD" -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
p.stop()
" &>/dev/null 2>&1; then
    PW_OK=true
fi
[ -n "${CHROMIUM_BIN:-}" ] && PW_OK=true

if $PW_OK; then
    skip "Playwright browser"
else
    if $IS_TERMUX; then
        info "Registering system Chromium with Playwright ..."
        PLAYWRIGHT_BROWSERS_PATH="$HOME/.playwright-browsers" \
            "$PY_CMD" -m playwright install chromium 2>/dev/null \
            || warn "Playwright step had issues — will fall back to system Chromium."
    else
        info "Downloading Playwright Chromium ..."
        "$PY_CMD" -m playwright install chromium || error "playwright install failed."
    fi
    success "Playwright browser ready."
fi

# ══════════════════════════════════════════════════════════════════════════════
# 7. Project directories & __init__ files
# ══════════════════════════════════════════════════════════════════════════════
step "Project Structure"

for d in \
    "$SCRIPT_DIR/orchestrator" \
    "$SCRIPT_DIR/daemon" \
    "$SCRIPT_DIR/ui/static" \
    "$SCRIPT_DIR/ingestion" \
    "$SCRIPT_DIR/executor" \
    "$SCRIPT_DIR/skills/prompts" \
    "$HOME/.walidcode"; do
    [ -d "$d" ] && skip "dir $d" || { mkdir -p "$d"; success "Created: $d"; }
done

for pkg in orchestrator daemon ui ingestion executor; do
    f="$SCRIPT_DIR/$pkg/__init__.py"
    [ -f "$f" ] || echo "# $pkg" > "$f"
done

# ══════════════════════════════════════════════════════════════════════════════
# 8. Launcher — only create/update if missing or stale
# ══════════════════════════════════════════════════════════════════════════════
step "Launcher"

$IS_TERMUX \
    && LAUNCHER_PATH="$TERMUX_PREFIX/bin/walidcode" \
    || LAUNCHER_PATH="/usr/local/bin/walidcode"

$IS_TERMUX \
    && SHEBANG="#!/data/data/com.termux/files/usr/bin/bash" \
    || SHEBANG="#!/usr/bin/env bash"

PY_BIN="$(command -v "$PY_CMD")"

NEEDS_UPDATE=true
if [ -f "$LAUNCHER_PATH" ] && grep -q "$SCRIPT_DIR" "$LAUNCHER_PATH" 2>/dev/null; then
    skip "launcher ($LAUNCHER_PATH)"
    NEEDS_UPDATE=false
fi

if $NEEDS_UPDATE; then
    printf '%s\n' \
        "$SHEBANG" \
        "# walidcode launcher — generated by setup.sh" \
        "WALIDCODE_DIR=\"${SCRIPT_DIR}\"" \
        "export PYTHONPATH=\"\${WALIDCODE_DIR}:\${PYTHONPATH:-}\"" \
        "exec \"${PY_BIN}\" \"\${WALIDCODE_DIR}/main.py\" \"\$@\"" \
        > "$LAUNCHER_PATH"
    chmod +x "$LAUNCHER_PATH"
    success "Launcher written: $LAUNCHER_PATH"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 9. Final verification
# ══════════════════════════════════════════════════════════════════════════════
step "Verification"
ERRORS=0

has_cmd walidcode \
    && success "walidcode command: OK" \
    || { warn "walidcode not in PATH — run: export PATH=\"\$PATH:$TERMUX_PREFIX/bin\""; ERRORS=$((ERRORS+1)); }

IMPORT_RESULT=$("$PY_CMD" -c "
import sys; sys.path.insert(0, '${SCRIPT_DIR}')
fail = []
for m in ['config','orchestrator.swarm','orchestrator.message_bus',
          'ingestion.project_reader','executor.local_executor','executor.tool_parser']:
    try: __import__(m)
    except ImportError as e: fail.append(f'{m}: {e}')
print('FAIL:'+' | '.join(fail) if fail else 'OK')
" 2>&1)

echo "$IMPORT_RESULT" | grep -q "^OK" \
    && success "Core imports: OK" \
    || { warn "Import issues: $IMPORT_RESULT"; ERRORS=$((ERRORS+1)); }

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${RESET}"
    echo -e "${GREEN}  walidcode v2 is ready! 🎉${RESET}"
    echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${RESET}"
else
    echo -e "${BOLD}${YELLOW}══════════════════════════════════════════════${RESET}"
    echo -e "${YELLOW}  Done with $ERRORS warning(s) — check above.${RESET}"
    echo -e "${BOLD}${YELLOW}══════════════════════════════════════════════${RESET}"
fi
echo ""
echo -e "  ${CYAN}walidcode start${RESET}   — launch swarm"
echo -e "  ${CYAN}walidcode chat${RESET}    — open TUI"
echo -e "  ${CYAN}walidcode --help${RESET}  — all commands"
echo ""
$IS_TERMUX && echo -e "  ${YELLOW}Tip:${RESET} pkg install tmux && tmux new -s walidcode"
echo ""
