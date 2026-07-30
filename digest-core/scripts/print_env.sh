#!/bin/bash
# Environment diagnostics script
set -euo pipefail

echo "Digest-core environment diagnostics..."
echo "======================================"

# The interpreter and tools ActionPulse ACTUALLY runs on. `actionpulse diagnose`
# passes these down; standalone use falls back to PATH and says so. A developer
# machine routinely carries several toolchains, and silently reporting the wrong
# one is how a later debugging session loses a day chasing the wrong Python
# (ACTPULSE-97) — corp-brief T1 records this output as *the* environment record.
DIAG_PY="${ACTIONPULSE_DIAG_PY:-}"
DIAG_BIN="${ACTIONPULSE_DIAG_BIN:-}"

echo "Python version:"
if [ -n "$DIAG_PY" ]; then
    echo "✓ $("$DIAG_PY" --version 2>&1) — project interpreter"
    echo "  path: $DIAG_PY"
    if command -v python3 &> /dev/null; then
        path_py="$(command -v python3)"
        if [ "$path_py" != "$DIAG_PY" ]; then
            echo "  note: PATH python3 is different ($("$path_py" --version 2>&1) at $path_py)."
            echo "        The project's interpreter above is the one that matters."
        fi
    fi
else
    echo "  $(python3 --version 2>&1) — PATH python3 at $(command -v python3 || echo 'not found')"
    echo "  note: this may NOT be the interpreter ActionPulse runs on."
    echo "        Run 'actionpulse diagnose' to report the project's."
fi

# Check required tools. Prefer the project environment; uv and docker are host
# tools and correctly fall through to PATH.
echo ""
echo "Required tools:"
for tool in uv docker pytest ruff black; do
    venv_tool=""
    if [ -n "$DIAG_BIN" ] && [ -x "$DIAG_BIN/$tool" ]; then
        venv_tool="$DIAG_BIN/$tool"
    fi
    path_tool="$(command -v "$tool" 2>/dev/null || true)"

    if [ -n "$venv_tool" ]; then
        echo "✓ $tool: $venv_tool (project env)"
        if [ -n "$path_tool" ] && [ "$path_tool" != "$venv_tool" ]; then
            echo "    ⚠ PATH has a different $tool: $path_tool — the project env one is used"
        fi
    elif [ -n "$path_tool" ]; then
        echo "✓ $tool: $path_tool (PATH)"
    else
        echo "✗ $tool: not found"
    fi
done

# Check environment variables (without showing values)
echo ""
echo "Environment variables:"
for var in EWS_USER_UPN EWS_PASSWORD LLM_TOKEN EWS_ENDPOINT LLM_ENDPOINT; do
    if [ -n "${!var:-}" ]; then
        val="${!var}"
        echo "✓ $var: set (${#val} characters)"
    else
        echo "✗ $var: not set"
    fi
done

# Check CA certificate
echo ""
echo "CA certificate:"
CA_FOUND=false
CONFIG_CA_PATH=""

# Try to read ews.verify_ca from configs/config.yaml (best effort)
if [ -f "./configs/config.yaml" ]; then
    CONFIG_CA_PATH=$(awk '
        BEGIN { in_ews=0 }
        /^ews:[[:space:]]*$/ { in_ews=1; next }
        /^[^[:space:]]/ { in_ews=0 }
        in_ews && /^[[:space:]]*verify_ca:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*verify_ca:[[:space:]]*/, "", line)
            sub(/[[:space:]]*#.*/, "", line)
            gsub(/"/, "", line)
            gsub(/'\''/, "", line)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
            if (line == "null" || line == "~") line=""
            print line
            exit
        }
    ' "./configs/config.yaml")
fi

if [ -n "$CONFIG_CA_PATH" ]; then
    if [ -f "$CONFIG_CA_PATH" ]; then
        echo "✓ Configured ews.verify_ca found: $CONFIG_CA_PATH"
        CA_FOUND=true
    else
        echo "✗ Configured ews.verify_ca missing: $CONFIG_CA_PATH"
    fi
fi

for ca_path in "/etc/ssl/corp-ca.pem" "${HOME}/.ssl/corp-ca.pem" "./certs/corp-ca.pem"; do
    if [ -f "$ca_path" ] && [ "$ca_path" != "$CONFIG_CA_PATH" ]; then
        echo "✓ Corporate CA found: $ca_path"
        echo "  Certificate info:"
        openssl x509 -in "$ca_path" -text -noout | rg "(Subject:|Not Before|Not After)" || true
        CA_FOUND=true
        break
    fi
done
if [ "$CA_FOUND" = false ]; then
    echo "✗ Corporate CA not found in standard locations"
    echo "  Checked paths:"
    echo "    - /etc/ssl/corp-ca.pem"
    echo "    - ${HOME}/.ssl/corp-ca.pem"
    echo "    - ./certs/corp-ca.pem"
fi

# Check directories
echo ""
echo "Directory permissions:"
for dir in "${HOME}/.digest-out" "${HOME}/.digest-state" "./out" "./.state"; do
    if [ -d "$dir" ]; then
        echo "✓ $dir: exists (permissions: $(stat -c %a "$dir" 2>/dev/null || stat -f %A "$dir" 2>/dev/null || echo "unknown"))"
    else
        echo "✗ $dir: does not exist"
    fi
done

# Check network connectivity
echo ""
echo "Network connectivity:"
if ping -c 1 8.8.8.8 &> /dev/null; then
    echo "✓ Internet connectivity: OK"
else
    echo "✗ Internet connectivity: failed"
fi

# Check if we can resolve EWS endpoint (if set)
if [ -n "${EWS_ENDPOINT:-}" ]; then
    echo "EWS endpoint: $EWS_ENDPOINT"
    if curl -s --connect-timeout 5 "$EWS_ENDPOINT" &> /dev/null; then
        echo "✓ EWS endpoint reachable"
    else
        echo "✗ EWS endpoint not reachable"
    fi
fi

echo ""
echo "Diagnostics completed!"
