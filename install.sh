#!/bin/bash
# ============================================================================
#  ActionPulse — bootstrap installer (macOS-first)
#
#  Канонический запуск (Homebrew-стиль — stdin остаётся на терминале,
#  поэтому мастер настройки спрашивает пароли в этой же сессии):
#
#    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"
#
#  Что делает (идемпотентно, безопасно перезапускать):
#    1. Проверяет ОС и наличие git / Xcode Command Line Tools
#       (без git — fallback на tarball с GitHub, git не обязателен).
#    2. Ставит uv (официальный установщик astral.sh; запасной URL — GitHub).
#       uv сам скачает CPython 3.11 (см. digest-core/.python-version) —
#       системный Python любой версии или его отсутствие не важны.
#    3. Клонирует репозиторий в ~/ActionPulse (спросит, можно изменить).
#       Повторный запуск: git pull --ff-only вместо клонирования.
#    4. uv sync --native-tls, при неудаче — uv sync (паритет с Makefile;
#       native-tls нужен за корп-прокси с подменой TLS-сертификатов).
#    5. Запускает интерактивный мастер настройки в этом же терминале
#       (email, EWS, LLM endpoint, токены, Mattermost webhook).
#
#  Флаги:
#    --dir DIR      каталог установки (или env ACTIONPULSE_DIR)
#    --ref REF      ветка/тег вместо main (для тестирования)
#    --no-wizard    только зависимости, без мастера (headless / CI)
#    --help         эта справка
#
#  Дизайн-инварианты:
#    * bash 3.2 (родной для macOS): без mapfile, declare -A, ${var,,}.
#    * Никаких sudo. Всё в $HOME.
#    * Вывод деградирует: не-TTY / NO_COLOR / TERM=dumb / не-UTF-8 локаль.
#    * Секреты не печатаются и не логируются (лог шагов — только stdout
#      команд установки).
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Конфигурация
# ----------------------------------------------------------------------------
REPO_URL="${ACTIONPULSE_REPO_URL:-https://github.com/pogorelov-labs/ActionPulse.git}"
TARBALL_URL_BASE="${ACTIONPULSE_TARBALL_BASE:-https://github.com/pogorelov-labs/ActionPulse/archive/refs/heads}"
REF="main"
INSTALL_DIR="${ACTIONPULSE_DIR:-}"
RUN_WIZARD=1
STEP_LOG=""

# ----------------------------------------------------------------------------
# Оформление: цвета, глифы, спиннер
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
    printf '  %s%s%s %sActionPulse%s %s· установка%s\n' \
        "$C_CYN" "$G_PULSE" "$C_RST" "$C_BOLD" "$C_RST" "$C_DIM" "$C_RST"
    printf '  %sдайджест действий из корпоративной почты — каждое утро%s\n' "$C_DIM" "$C_RST"
    if [ "$UTF8_OK" = 1 ]; then
        printf '  %s─────%s⌁%s──────────────────────────────────────%s\n' \
            "$C_DIM" "$C_CYN" "$C_DIM" "$C_RST"
    else
        printf '  %s---------------------------------------------%s\n' "$C_DIM" "$C_RST"
    fi
    say ""
}

# Последние строки лога шага — при падении, чтобы не слать пользователя в файл.
show_step_log_tail() {
    if [ -n "$STEP_LOG" ] && [ -s "$STEP_LOG" ]; then
        say ""
        note "Последние строки лога ($STEP_LOG):"
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

# run_step "метка" cmd args…  — спиннер пока команда работает, ✓/✗ + длительность.
# stdout/stderr команды уходят в $STEP_LOG (секреты в этих шагах не участвуют).
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
        printf '  %s%s%s %s %s(%sс)%s\n' "$C_GRN" "$G_OK" "$C_RST" "$label" "$C_DIM" "$dur" "$C_RST"
    else
        printf '  %s%s %s — не удалось%s\n' "$C_RED" "$G_FAIL" "$label" "$C_RST"
        return "$rc"
    fi
}

# `test -r /dev/tty` проверяет только права; без управляющего терминала
# (cron, CI) open(2) всё равно падает с ENXIO — проверяем реальным открытием.
tty_openable() { ( : </dev/tty; ) 2>/dev/null; }

# Вопрос пользователю. Читает с /dev/tty (работает и при `curl | bash`),
# иначе — со stdin (скриптовый сценарий). Результат в $REPLY_VALUE.
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
    # При запуске через `bash -c` файла нет — краткая версия:
    say "ActionPulse installer. Флаги: --dir DIR, --ref REF, --no-wizard, --help"
}

# ----------------------------------------------------------------------------
# Шаги установки
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
        warn "ОС $os — установщик рассчитан на macOS, продолжаю как есть"
    fi
    command -v curl >/dev/null 2>&1 || die "curl не найден" "На macOS curl предустановлен; проверьте PATH."
}

# git нужен для clone/pull. На свежем macOS git появляется вместе с
# Command Line Tools. Если их нет — предлагаем поставить, иначе tarball.
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

    warn "git / Xcode Command Line Tools не найдены"
    ask "Установить Command Line Tools сейчас? (откроется окно macOS) y/n" "y"
    if [ "$REPLY_VALUE" = "y" ] || [ "$REPLY_VALUE" = "Y" ]; then
        xcode-select --install >/dev/null 2>&1 || true
        note "Подтвердите установку в открывшемся окне. Жду завершения…"
        local waited=0
        while ! xcode-select -p >/dev/null 2>&1; do
            sleep 5
            waited=$((waited + 5))
            if [ "$waited" -ge 1800 ]; then
                warn "Не дождался Command Line Tools — продолжаю без git (tarball)"
                return 0
            fi
        done
        HAVE_GIT=1
        ok "Command Line Tools установлены"
    else
        note "Продолжаю без git — скачаю снимок репозитория (tarball)."
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
    # Официальный установщик; запасной путь — релизы uv на GitHub
    # (на случай если astral.sh закрыт прокси, а GitHub открыт).
    local tmp
    tmp="$(mktemp -t uv-installer)"
    if ! curl -fsSL https://astral.sh/uv/install.sh -o "$tmp" 2>>"$STEP_LOG"; then
        curl -fsSL https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh \
            -o "$tmp" 2>>"$STEP_LOG"
    fi
    sh "$tmp" >>"$STEP_LOG" 2>&1
    rm -f "$tmp"
    find_uv || die "uv установлен, но не найден в PATH" \
        "Откройте новый терминал и запустите установщик ещё раз."
}

ensure_uv() {
    if find_uv; then
        ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    else
        run_step "Установка uv (менеджер Python-окружений)" install_uv
        ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    fi
}

clone_tarball() {
    local tmp
    tmp="$(mktemp -d -t actionpulse-src)"
    curl -fsSL "$TARBALL_URL_BASE/$REF.tar.gz" | tar -xz -C "$tmp"
    mkdir -p "$INSTALL_DIR"
    # ActionPulse-<ref> → INSTALL_DIR (cp -R: без rsync-зависимости)
    cp -R "$tmp"/ActionPulse-*/. "$INSTALL_DIR"/
    rm -rf "$tmp"
}

fetch_sources() {
    if [ -f "$INSTALL_DIR/digest-core/pyproject.toml" ]; then
        if [ -d "$INSTALL_DIR/.git" ] && [ "$HAVE_GIT" = 1 ]; then
            if [ -n "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null)" ]; then
                warn "В $INSTALL_DIR есть локальные изменения — пропускаю git pull"
            else
                run_step "Обновление репозитория (git pull)" \
                    git -C "$INSTALL_DIR" pull --ff-only origin "$REF"
            fi
        else
            ok "Использую существующий каталог $INSTALL_DIR"
        fi
        return 0
    fi

    if [ "$HAVE_GIT" = 1 ]; then
        run_step "Клонирование репозитория ($REF)" \
            git clone --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
    else
        run_step "Загрузка снимка репозитория ($REF, tarball)" clone_tarball
    fi
}

sync_deps() {
    # Паритет с digest-core/Makefile: сперва native-tls (корп-прокси с
    # подменой сертификатов), при неудаче — обычный sync.
    cd "$INSTALL_DIR/digest-core"
    if uv sync --native-tls; then
        echo "[install.sh] uv sync --native-tls: ok"
    else
        echo "[install.sh] native-tls не прошёл, пробую обычный uv sync"
        uv sync
    fi
}

run_wizard() {
    say ""
    printf '  %s%s%s %sМастер настройки%s %s— ответьте на 6 вопросов, секреты скрыты при вводе%s\n' \
        "$C_CYN" "$G_PULSE" "$C_RST" "$C_BOLD" "$C_RST" "$C_DIM" "$C_RST"
    say ""
    # При `curl | bash` stdin занят пайпом — отдаём мастеру терминал.
    # Если stdin уже терминал (bash -c "$(curl …)") или это скриптовый
    # запуск с ответами через пайп — ничего не перенаправляем.
    if [ ! -t 0 ] && tty_openable; then
        (cd "$INSTALL_DIR/digest-core" && uv run python -m digest_core.cli setup </dev/tty)
    else
        (cd "$INSTALL_DIR/digest-core" && uv run python -m digest_core.cli setup)
    fi
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
    printf '  %s%s%s %sActionPulse установлен%s %s(за %sс)%s\n' \
        "$C_GRN" "$G_OK" "$C_RST" "$C_BOLD" "$C_RST" "$C_DIM" "$SECONDS" "$C_RST"
    say ""
    printf '  %sкод%s     %s\n' "$C_DIM" "$C_RST" "$dir_short"
    printf '  %sсекреты%s %s %s(chmod 600)%s\n' "$C_DIM" "$C_RST" "$env_short" "$C_DIM" "$C_RST"
    say ""
    printf '  %sПервый дайджест:%s\n' "$C_BOLD" "$C_RST"
    say ""
    printf '    %scd%s %s/digest-core\n' "$C_CYN" "$C_RST" "$dir_short"
    printf '    %sset%s -a && %ssource%s %s && %sset%s +a\n' \
        "$C_CYN" "$C_RST" "$C_CYN" "$C_RST" "$env_short" "$C_CYN" "$C_RST"
    printf '    %suv run%s python -m digest_core.cli run --dry-run\n' "$C_CYN" "$C_RST"
    say ""
    note "EWS и LLM Gateway доступны только из корпоративной сети —"
    note "вне её dry-run честно сообщит об отсутствии подключения."
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
            *) die "Неизвестный флаг: $1" "Доступно: --dir DIR, --ref REF, --no-wizard, --help" ;;
        esac
    done

    STEP_LOG="$(mktemp -t actionpulse-install-log)"
    banner

    check_os
    check_git
    ensure_uv

    # Каталог установки: флаг/env → текущий клон → вопрос с дефолтом.
    if [ -z "$INSTALL_DIR" ]; then
        if [ -f "./digest-core/pyproject.toml" ]; then
            INSTALL_DIR="$(pwd)"
        else
            ask "Куда установить?" "$HOME/ActionPulse"
            INSTALL_DIR="$REPLY_VALUE"
        fi
    fi
    case "$INSTALL_DIR" in
        "~"|"~/"*) INSTALL_DIR="$HOME${INSTALL_DIR#"~"}" ;;
    esac

    fetch_sources
    run_step "Зависимости + Python 3.11 (uv sync)" sync_deps

    if [ "$RUN_WIZARD" = 1 ]; then
        run_wizard
    else
        note "Мастер пропущен (--no-wizard). Запустить позже:"
        note "  cd $INSTALL_DIR/digest-core && uv run python -m digest_core.cli setup"
    fi

    summary
    rm -f "$STEP_LOG"
}

# Вызов через main гарантирует: частично скачанный скрипт не исполнится.
main "$@"
