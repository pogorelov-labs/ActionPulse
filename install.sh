#!/bin/bash
# ============================================================================
#  ActionPulse — bootstrap installer (macOS-first)
#
#  Canonical invocation (Homebrew-style — stdin stays on the terminal,
#  so the setup wizard can ask for passwords in the same session):
#
#    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"
#
#  What it does (idempotent, safe to re-run):
#    1. Checks the OS and git / Xcode Command Line Tools
#       (without git it falls back to a GitHub tarball — git is optional).
#    2. Installs uv (official astral.sh installer; GitHub fallback URL).
#       uv downloads CPython 3.11 itself (see digest-core/.python-version) —
#       any system Python version, or none at all, is fine.
#    3. Clones the repository into ~/ActionPulse (asks; can be changed).
#       Re-run: git pull --ff-only instead of cloning.
#    4. uv sync --native-tls, falling back to uv sync (Makefile parity;
#       native-tls is needed behind corp proxies with TLS interception).
#    5. Installs a global `actionpulse` launcher into ~/.local/bin and
#       launches the interactive setup wizard in this same terminal
#       (email, EWS, LLM endpoint, tokens, Mattermost webhook, report lang).
#
#  Flags:
#    --dir DIR      install directory (or env ACTIONPULSE_DIR)
#    --ref REF      branch/tag instead of main (for testing)
#    --no-wizard    dependencies only, no wizard (headless / CI)
#    --help         this help
#
#  Design invariants:
#    * bash 3.2 (macOS stock): no mapfile, declare -A, ${var,,}.
#    * No sudo. Everything in $HOME.
#    * Output degrades: non-TTY / NO_COLOR / TERM=dumb / non-UTF-8 locale.
#    * Secrets are never printed or logged (the step log captures only
#      installer command output).
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
REPO_URL="${ACTIONPULSE_REPO_URL:-https://github.com/pogorelov-labs/ActionPulse.git}"
TARBALL_URL_BASE="${ACTIONPULSE_TARBALL_BASE:-https://github.com/pogorelov-labs/ActionPulse/archive/refs/heads}"
REF="main"
INSTALL_DIR="${ACTIONPULSE_DIR:-}"
RUN_WIZARD=1
STEP_LOG=""

# ----------------------------------------------------------------------------
# Presentation: colors, glyphs, spinner
# ----------------------------------------------------------------------------
TTY_OUT=0
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    TTY_OUT=1
fi

if [ "$TTY_OUT" = 1 ]; then
    C_RST=$'\033[0m'   C_BOLD=$'\033[1m'  C_DIM=$'\033[2m'
    C_RED=$'\033[31m'  C_GRN=$'\033[32m'  C_YLW=$'\033[33m'
    C_CYN=$'\033[36m'  C_MAG=$'\033[35m'
else
    C_RST="" C_BOLD="" C_DIM="" C_RED="" C_GRN="" C_YLW="" C_CYN="" C_MAG=""
fi

UTF8_OK=0
case "$(locale charmap 2>/dev/null || true)" in
    *UTF-8*|*utf8*) UTF8_OK=1 ;;
esac

if [ "$UTF8_OK" = 1 ]; then
    G_OK="✓" G_FAIL="✗" G_WARN="⚠" G_PULSE="⌁" G_ARROW="→"
    SPIN_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
else
    G_OK="OK" G_FAIL="X" G_WARN="!" G_PULSE="~" G_ARROW="->"
    SPIN_FRAMES=(- \\ \| /)
fi

say()  { printf '%s\n' "$1"; }
note() { printf '  %s%s%s\n' "$C_DIM" "$1" "$C_RST"; }
warn() { printf '  %s%s %s%s\n' "$C_YLW" "$G_WARN" "$1" "$C_RST"; }
ok()   { printf '  %s%s%s %s\n' "$C_GRN" "$G_OK" "$C_RST" "$1"; }

banner() {
    say ""
    printf '  %s%s%s %sActionPulse%s %s· installer%s\n' \
        "$C_CYN" "$G_PULSE" "$C_RST" "$C_BOLD" "$C_RST" "$C_DIM" "$C_RST"
    printf '  %syour corporate inbox, distilled into actions — every morning%s\n' "$C_DIM" "$C_RST"
    if [ "$UTF8_OK" = 1 ]; then
        printf '  %s─────%s⌁%s──────────────────────────────────────%s\n' \
            "$C_DIM" "$C_CYN" "$C_DIM" "$C_RST"
    else
        printf '  %s---------------------------------------------%s\n' "$C_DIM" "$C_RST"
    fi
    say ""
}

# Tail of the step log on failure — so the user is not sent off to a file.
show_step_log_tail() {
    if [ -n "$STEP_LOG" ] && [ -s "$STEP_LOG" ]; then
        say ""
        note "Last log lines ($STEP_LOG):"
        tail -n 15 "$STEP_LOG" | while IFS= read -r line; do
            printf '  %s│%s %s\n' "$C_DIM" "$C_RST" "$line"
        done
    fi
}

die() {
    printf '  %s%s %s%s\n' "$C_RED" "$G_FAIL" "$1" "$C_RST" >&2
    [ $# -ge 2 ] && note "$2"
    show_step_log_tail
    exit 1
}

# run_step "label" cmd args…  — spinner while the command runs, ✓/✗ + duration.
# Command stdout/stderr goes to $STEP_LOG (no secrets flow through these steps).
run_step() {
    local label="$1"; shift
    local start=$SECONDS rc=0

    if [ "$TTY_OUT" = 1 ]; then
        "$@" >>"$STEP_LOG" 2>&1 &
        local pid=$! i=0 n=${#SPIN_FRAMES[@]}
        while kill -0 "$pid" 2>/dev/null; do
            printf '\r  %s%s%s %s… ' "$C_CYN" "${SPIN_FRAMES[$i]}" "$C_RST" "$label"
            i=$(( (i + 1) % n ))
            sleep 0.08
        done
        wait "$pid" || rc=$?
        printf '\r\033[2K'
    else
        printf '  · %s…\n' "$label"
        "$@" >>"$STEP_LOG" 2>&1 || rc=$?
    fi

    local dur=$((SECONDS - start))
    if [ "$rc" = 0 ]; then
        printf '  %s%s%s %s %s(%ss)%s\n' "$C_GRN" "$G_OK" "$C_RST" "$label" "$C_DIM" "$dur" "$C_RST"
    else
        printf '  %s%s %s — failed%s\n' "$C_RED" "$G_FAIL" "$label" "$C_RST"
        return "$rc"
    fi
}

# `test -r /dev/tty` checks permission bits only; without a controlling
# terminal (cron, CI) open(2) still fails with ENXIO — test with a real open.
tty_openable() { ( : </dev/tty; ) 2>/dev/null; }

# Ask the user. Reads from /dev/tty (works under `curl | bash` too),
# otherwise from stdin (scripted scenario). Result lands in $REPLY_VALUE.
REPLY_VALUE=""
ask() {
    local prompt="$1" default="${2:-}" input=""
    if [ "$TTY_OUT" = 1 ] && tty_openable; then
        printf '  %s?%s %s %s[%s]%s ' \
            "$C_MAG" "$C_RST" "$prompt" "$C_DIM" "$default" "$C_RST" >/dev/tty
        IFS= read -r input </dev/tty || input=""
    else
        IFS= read -r input 2>/dev/null || input=""
    fi
    if [ -n "$input" ]; then REPLY_VALUE="$input"; else REPLY_VALUE="$default"; fi
}

usage() {
    sed -n '2,33p' "$0" 2>/dev/null | sed 's/^#//' || true
    # Under `bash -c` there is no file on disk — short version:
    say "ActionPulse installer. Flags: --dir DIR, --ref REF, --no-wizard, --help"
}

# ----------------------------------------------------------------------------
# Install steps
# ----------------------------------------------------------------------------

check_os() {
    local os
    os="$(uname -s)"
    if [ "$os" = "Darwin" ]; then
        local ver arch
        ver="$(sw_vers -productVersion 2>/dev/null || echo '?')"
        arch="$(uname -m)"
        ok "macOS $ver ($arch)"
    else
        warn "OS $os — this installer targets macOS, continuing as-is"
    fi
    command -v curl >/dev/null 2>&1 || die "curl not found" "macOS ships curl preinstalled; check your PATH."
}

# git is needed for clone/pull. On a fresh macOS git arrives with the
# Command Line Tools. If absent — offer to install them, else tarball.
HAVE_GIT=0
check_git() {
    if [ "$(uname -s)" != "Darwin" ]; then
        command -v git >/dev/null 2>&1 && HAVE_GIT=1
        [ "$HAVE_GIT" = 1 ] && ok "git $(git --version | awk '{print $3}')"
        return 0
    fi

    if xcode-select -p >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
        HAVE_GIT=1
        ok "git $(git --version | awk '{print $3}')"
        return 0
    fi

    warn "git / Xcode Command Line Tools not found"
    ask "Install Command Line Tools now? (a macOS dialog will open) y/n" "y"
    if [ "$REPLY_VALUE" = "y" ] || [ "$REPLY_VALUE" = "Y" ]; then
        xcode-select --install >/dev/null 2>&1 || true
        note "Confirm the install in the dialog. Waiting for it to finish…"
        local waited=0
        while ! xcode-select -p >/dev/null 2>&1; do
            sleep 5
            waited=$((waited + 5))
            if [ "$waited" -ge 1800 ]; then
                warn "Command Line Tools never arrived — continuing without git (tarball)"
                return 0
            fi
        done
        HAVE_GIT=1
        ok "Command Line Tools installed"
    else
        note "Continuing without git — downloading a repository snapshot (tarball)."
    fi
}

find_uv() {
    if command -v uv >/dev/null 2>&1; then return 0; fi
    local cand
    for cand in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -x "$cand/uv" ]; then
            export PATH="$cand:$PATH"
            return 0
        fi
    done
    return 1
}

install_uv() {
    # Official installer; fallback path — uv releases on GitHub
    # (for proxies that block astral.sh while GitHub stays open).
    local tmp
    tmp="$(mktemp -t uv-installer)"
    if ! curl -fsSL https://astral.sh/uv/install.sh -o "$tmp" 2>>"$STEP_LOG"; then
        curl -fsSL https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh \
            -o "$tmp" 2>>"$STEP_LOG"
    fi
    sh "$tmp" >>"$STEP_LOG" 2>&1
    rm -f "$tmp"
    find_uv || die "uv installed but not found on PATH" \
        "Open a new terminal and run the installer again."
}

ensure_uv() {
    if find_uv; then
        ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    else
        run_step "Installing uv (Python environment manager)" install_uv
        ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    fi
}

clone_tarball() {
    local tmp
    tmp="$(mktemp -d -t actionpulse-src)"
    curl -fsSL "$TARBALL_URL_BASE/$REF.tar.gz" | tar -xz -C "$tmp"
    mkdir -p "$INSTALL_DIR"
    # ActionPulse-<ref> → INSTALL_DIR (cp -R: no rsync dependency)
    cp -R "$tmp"/ActionPulse-*/. "$INSTALL_DIR"/
    rm -rf "$tmp"
}

fetch_sources() {
    if [ -f "$INSTALL_DIR/digest-core/pyproject.toml" ]; then
        if [ -d "$INSTALL_DIR/.git" ] && [ "$HAVE_GIT" = 1 ]; then
            if [ -n "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null)" ]; then
                warn "Local changes in $INSTALL_DIR — skipping git pull"
            else
                run_step "Updating the repository (git pull)" \
                    git -C "$INSTALL_DIR" pull --ff-only origin "$REF"
            fi
        else
            ok "Using the existing directory $INSTALL_DIR"
        fi
        return 0
    fi

    if [ "$HAVE_GIT" = 1 ]; then
        run_step "Cloning the repository ($REF)" \
            git clone --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
    else
        run_step "Downloading a repository snapshot ($REF, tarball)" clone_tarball
    fi
}

sync_deps() {
    # Makefile parity: native-tls first (corp proxies with certificate
    # interception), plain sync on failure.
    cd "$INSTALL_DIR/digest-core"
    if uv sync --native-tls; then
        echo "[install.sh] uv sync --native-tls: ok"
    else
        echo "[install.sh] native-tls failed, trying plain uv sync"
        uv sync
    fi
}

run_wizard() {
    say ""
    printf '  %s%s%s %sSetup wizard%s %s— 7 questions, secrets hidden while typing%s\n' \
        "$C_CYN" "$G_PULSE" "$C_RST" "$C_BOLD" "$C_RST" "$C_DIM" "$C_RST"
    say ""
    # Under `curl | bash` stdin is the pipe — hand the wizard the terminal.
    # If stdin already is a terminal (bash -c "$(curl …)") or this is a
    # scripted run with piped answers — redirect nothing.
    if [ ! -t 0 ] && tty_openable; then
        (cd "$INSTALL_DIR/digest-core" && uv run python -m digest_core.cli setup </dev/tty)
    else
        (cd "$INSTALL_DIR/digest-core" && uv run python -m digest_core.cli setup)
    fi
}

# Install a global `actionpulse` launcher into ~/.local/bin so the command is
# available everywhere. The shim execs the project via uv; the CLI itself
# auto-loads ~/.config/actionpulse/env, so no manual `source` is needed.
LAUNCHER_PATH="$HOME/.local/bin/actionpulse"
install_launcher() {
    mkdir -p "$HOME/.local/bin"
    cat >"$LAUNCHER_PATH" <<LAUNCHER
#!/bin/sh
# ActionPulse launcher — generated by install.sh. Re-run the installer to update.
exec uv run --project "$INSTALL_DIR/digest-core" python -m digest_core.cli "\$@"
LAUNCHER
    chmod +x "$LAUNCHER_PATH"
}

# Is ~/.local/bin on PATH? (uv's installer normally arranges this.)
launcher_on_path() {
    case ":$PATH:" in *":$HOME/.local/bin:"*) return 0 ;; *) return 1 ;; esac
}

summary() {
    local dir_short env_short
    dir_short="$INSTALL_DIR"
    case "$dir_short" in "$HOME"/*) dir_short="~${dir_short#"$HOME"}" ;; esac
    env_short="~/.config/actionpulse/env"

    say ""
    if [ "$UTF8_OK" = 1 ]; then
        printf '  %s──────────────────────────────────────────────%s\n' "$C_DIM" "$C_RST"
    else
        printf '  %s----------------------------------------------%s\n' "$C_DIM" "$C_RST"
    fi
    printf '  %s%s%s %sActionPulse installed%s %s(in %ss)%s\n' \
        "$C_GRN" "$G_OK" "$C_RST" "$C_BOLD" "$C_RST" "$C_DIM" "$SECONDS" "$C_RST"
    say ""
    printf '  %scode%s    %s\n' "$C_DIM" "$C_RST" "$dir_short"
    printf '  %ssecrets%s %s %s(chmod 600)%s\n' "$C_DIM" "$C_RST" "$env_short" "$C_DIM" "$C_RST"
    say ""
    printf '  %sUse it from anywhere:%s\n' "$C_BOLD" "$C_RST"
    say ""
    printf '    %sactionpulse%s            %s# interactive menu (run · settings · …)%s\n' \
        "$C_CYN" "$C_RST" "$C_DIM" "$C_RST"
    printf '    %sactionpulse%s run --dry-run\n' "$C_CYN" "$C_RST"
    say ""
    note "Secrets load automatically from $env_short — no manual source needed."
    if ! launcher_on_path; then
        warn "~/.local/bin is not on your PATH yet."
        note "Add it, then restart the shell:"
        note "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && exec \$SHELL"
    else
        note "Open a new terminal (or 'hash -r') so the shell sees the new command."
    fi
    note "EWS and the LLM Gateway are reachable only from the corp network —"
    note "outside it, dry-run will honestly report the missing connectivity."
    say ""
}

# ----------------------------------------------------------------------------
main() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --dir)        INSTALL_DIR="${2:-}"; shift 2 ;;
            --ref)        REF="${2:-main}"; shift 2 ;;
            --no-wizard)  RUN_WIZARD=0; shift ;;
            --help|-h)    usage; exit 0 ;;
            *) die "Unknown flag: $1" "Available: --dir DIR, --ref REF, --no-wizard, --help" ;;
        esac
    done

    STEP_LOG="$(mktemp -t actionpulse-install-log)"
    banner

    check_os
    check_git
    ensure_uv

    # Install directory: flag/env → current clone → question with a default.
    if [ -z "$INSTALL_DIR" ]; then
        if [ -f "./digest-core/pyproject.toml" ]; then
            INSTALL_DIR="$(pwd)"
        else
            ask "Where to install?" "$HOME/ActionPulse"
            INSTALL_DIR="$REPLY_VALUE"
        fi
    fi
    case "$INSTALL_DIR" in
        "~"|"~/"*) INSTALL_DIR="$HOME${INSTALL_DIR#"~"}" ;;
    esac

    fetch_sources
    run_step "Dependencies + Python 3.11 (uv sync)" sync_deps
    install_launcher
    ok "Command installed: actionpulse  (~/.local/bin/actionpulse)"

    if [ "$RUN_WIZARD" = 1 ]; then
        run_wizard
    else
        note "Wizard skipped (--no-wizard). Run it later:"
        note "  cd $INSTALL_DIR/digest-core && uv run python -m digest_core.cli setup"
    fi

    summary
    rm -f "$STEP_LOG"
}

# Calling through main guarantees a partially downloaded script never runs.
main "$@"
