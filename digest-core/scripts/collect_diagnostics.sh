#!/bin/bash
# Comprehensive diagnostics collection script for ActionPulse
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Generate timestamp for unique archive name
TIMESTAMP=$(date +"%Y-%m-%d-%H-%M-%S")
ARCHIVE_NAME="diagnostics-${TIMESTAMP}"
TEMP_DIR="${HOME}/.digest-temp/${ARCHIVE_NAME}"

echo "Collecting diagnostics for ActionPulse..."
echo "Archive name: ${ARCHIVE_NAME}.tar.gz"

# Create temporary directory structure
mkdir -p "$TEMP_DIR"/{logs,metrics,config,system,output}

# Function to safely copy files
safe_copy() {
    local src="$1"
    local dst="$2"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        echo "✓ Copied: $src"
    else
        echo "✗ Missing: $src"
    fi
}

# Function to safely copy directories
safe_copy_dir() {
    local src="$1"
    local dst="$2"
    if [ -d "$src" ]; then
        cp -r "$src" "$dst"
        echo "✓ Copied directory: $src"
    else
        echo "✗ Missing directory: $src"
    fi
}

# 1. Collect logs
echo "Collecting logs..."
LOG_DIR="$HOME/.digest-logs"
if [ -d "$LOG_DIR" ]; then
    safe_copy_dir "$LOG_DIR" "$TEMP_DIR/logs/"
else
    echo "✗ Log directory not found: $LOG_DIR"
fi

# Also check for any recent log files in project
find "$PROJECT_ROOT" -name "*.log" -mtime -1 -exec cp {} "$TEMP_DIR/logs/" \; 2>/dev/null || true

# 2. Collect metrics
echo "Collecting metrics..."
# Try to get Prometheus metrics if server is running
if curl -s --connect-timeout 5 http://localhost:9108/metrics > "$TEMP_DIR/metrics/prometheus.txt" 2>/dev/null; then
    echo "✓ Prometheus metrics collected"
else
    echo "✗ Prometheus metrics not available (server not running)"
    echo "Prometheus server not running or not accessible" > "$TEMP_DIR/metrics/prometheus.txt"
fi

# 3. Collect configuration (sanitized)
echo "Collecting configuration..."
cd "$PROJECT_ROOT"

# Copy config files and sanitize them
if [ -f "configs/config.yaml" ]; then
    # Remove sensitive data from config
    sed -E 's/([A-Za-z_]*(password|token|secret|key)[A-Za-z_]*:)[[:space:]]*[^[:space:]].*/\1 REDACTED/' "configs/config.yaml" > "$TEMP_DIR/config/sanitized.yaml"
    echo "✓ Configuration collected (sanitized)"
else
    echo "✗ config.yaml not found"
    echo "# Configuration file not found" > "$TEMP_DIR/config/sanitized.yaml"
fi

# Copy example config for reference
safe_copy "configs/config.example.yaml" "$TEMP_DIR/config/"

# 4. Collect system information
echo "Collecting system information..."

# System info
{
    echo "=== System Information ==="
    echo "Date: $(date)"
    echo "Hostname: $(hostname)"
    echo "User: $(whoami)"
    echo "OS: $(uname -a)"
    echo ""
    echo "=== Python Environment ==="
    # Prefer the interpreter ActionPulse actually runs on (passed down by
    # `actionpulse diagnose`); PATH python3 is often a different install
    # (ACTPULSE-97). This bundle is evidence — it has to name the right one.
    if [ -n "${ACTIONPULSE_DIAG_PY:-}" ]; then
        "${ACTIONPULSE_DIAG_PY}" --version
        echo "Python path: ${ACTIONPULSE_DIAG_PY} (project interpreter)"
    else
        python3 --version
        echo "Python path: $(command -v python3) (PATH — may not be the project's)"
    fi
    echo ""
    echo "=== Environment Variables (sanitized) ==="
    env | grep -E "^(EWS_|LLM_|DIGEST_|MM_)" | sed -E 's/^([A-Za-z0-9_]*(PASSWORD|TOKEN|SECRET|KEY|PAT|WEBHOOK_URL))=.*/\1=REDACTED/' || true
    echo ""
    echo "=== Disk Space ==="
    df -h
    echo ""
    echo "=== Memory Usage ==="
    free -h 2>/dev/null || vm_stat 2>/dev/null || echo "Memory info not available"
    echo ""
    echo "=== Network Connectivity ==="
    ping -c 1 8.8.8.8 >/dev/null 2>&1 && echo "Internet: OK" || echo "Internet: FAILED"
    if [ -n "${EWS_ENDPOINT:-}" ]; then
        echo "EWS endpoint: $EWS_ENDPOINT"
        curl -s --connect-timeout 5 "$EWS_ENDPOINT" >/dev/null 2>&1 && echo "EWS: OK" || echo "EWS: FAILED"
    fi
    if [ -n "${LLM_ENDPOINT:-}" ]; then
        echo "LLM endpoint: $LLM_ENDPOINT"
        curl -s --connect-timeout 5 "$LLM_ENDPOINT" >/dev/null 2>&1 && echo "LLM: OK" || echo "LLM: FAILED"
    fi
} > "$TEMP_DIR/system/system_info.txt"

# Process information
{
    echo "=== Running Processes ==="
    ps aux | grep -E "(python|digest)" | grep -v grep || echo "No digest processes found"
} > "$TEMP_DIR/system/processes.txt"

# 5. Collect output files
echo "Collecting output files..."
OUTPUT_DIRS=("./out" "${HOME}/.digest-out" "${HOME}/digest-out")
for dir in "${OUTPUT_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        safe_copy_dir "$dir" "$TEMP_DIR/output/"
        break
    fi
done

# 6. Collect state files
echo "Collecting state files..."
STATE_DIRS=("./.state" "${HOME}/.digest-state" "${HOME}/.state")
for dir in "${STATE_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        safe_copy_dir "$dir" "$TEMP_DIR/output/"
        break
    fi
done

# 7. Run environment diagnostics
echo "Running environment diagnostics..."
"$SCRIPT_DIR/print_env.sh" > "$TEMP_DIR/system/env_diagnostics.txt" 2>&1 || true

# 8. Create summary
echo "Creating summary..."
{
    echo "ActionPulse Diagnostics Report"
    echo "============================="
    echo "Generated: $(date)"
    echo "Archive: ${ARCHIVE_NAME}.tar.gz"
    echo ""
    echo "Contents:"
    echo "- logs/: Application logs and debug information"
    echo "- metrics/: Prometheus metrics and performance data"
    echo "- config/: Configuration files (sanitized)"
    echo "- system/: System information and diagnostics"
    echo "- output/: Generated output files and state"
    echo ""
    echo "Files collected:"
    find "$TEMP_DIR" -type f | wc -l | xargs echo "Total files:"
    echo ""
    echo "Log files:"
    find "$TEMP_DIR/logs" -name "*.log" 2>/dev/null | wc -l | xargs echo "Log files:"
    echo ""
    echo "Configuration status:"
    if [ -f "$TEMP_DIR/config/sanitized.yaml" ] && [ -s "$TEMP_DIR/config/sanitized.yaml" ]; then
        echo "✓ Configuration file found and sanitized"
    else
        echo "✗ Configuration file missing or empty"
    fi
    echo ""
    echo "Metrics status:"
    if grep -q "prometheus" "$TEMP_DIR/metrics/prometheus.txt" 2>/dev/null; then
        echo "✓ Prometheus metrics available"
    else
        echo "✗ Prometheus metrics not available"
    fi
    echo ""
    echo "System status:"
    if grep -q "Internet: OK" "$TEMP_DIR/system/system_info.txt" 2>/dev/null; then
        echo "✓ Internet connectivity: OK"
    else
        echo "✗ Internet connectivity: FAILED"
    fi
} > "$TEMP_DIR/summary.txt"

# 9. Create archive
echo "Creating archive..."
ARCHIVE_DIR="${HOME}/.digest-temp"
mkdir -p "$ARCHIVE_DIR"
cd "$ARCHIVE_DIR"
tar -czf "${ARCHIVE_NAME}.tar.gz" "$ARCHIVE_NAME"

# 10. Cleanup
rm -rf "$TEMP_DIR"

# 11. Show results
ARCHIVE_PATH="${ARCHIVE_DIR}/${ARCHIVE_NAME}.tar.gz"
ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)

echo ""
echo "Diagnostics collection completed!"
echo "================================"
echo "Archive: $ARCHIVE_PATH"
echo "Size: $ARCHIVE_SIZE"
echo ""
echo "To send via email:"
echo "1. Attach the archive to your email"
echo "2. Subject: ActionPulse Diagnostics - $(date +%Y-%m-%d)"
echo "3. Include any specific error messages or issues you encountered"
echo ""
echo "Archive contents:"
tar -tzf "$ARCHIVE_PATH" | head -20
if [ "$(tar -tzf "$ARCHIVE_PATH" | wc -l)" -gt 20 ]; then
    echo "... and $(($(tar -tzf "$ARCHIVE_PATH" | wc -l) - 20)) more files"
fi
