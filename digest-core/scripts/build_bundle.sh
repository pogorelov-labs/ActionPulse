#!/usr/bin/env bash
# Air-gap carry-in bundle (EP-6, frontier-audit F12).
#
# Produces a locked, audited, checksummed, manifested bundle that installs with
# NO network inside the corp air-gap — replacing the ad-hoc "zip the worktree"
# carry-in. Phases per the quality-loop airgap-bundle skill:
#   1. lock     — hashed transitive lockfile + offline-ready wheels (+ project wheel)
#   2. security — pip-audit report + CycloneDX SBOM
#   3. manifest — SLSA-style provenance record + SHA-256 over every artifact
#   4. verify   — offline install proof (--no-index --require-hashes) + CLI smoke
#
# Target platform: defaults to the build host. For a different inside host set
#   BUNDLE_PLATFORM (e.g. manylinux2014_x86_64) and BUNDLE_PYTHON_VERSION (e.g. 3.11)
# — wheels are platform-specific; a mismatch is the #1 cause of "audits clean,
# won't install inside".
#
# Never bundles secrets: the source archive is `git archive HEAD` (tracked files
# only); required env vars are listed in the manifest BY NAME, never by value.
set -euo pipefail
cd "$(dirname "$0")/.."

CODE_SHA=$(git rev-parse HEAD 2>/dev/null || echo unknown)
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
SHORT=${CODE_SHA:0:12}
BUNDLE="dist/bundle-${SHORT}"
TARGET_PLATFORM="${BUNDLE_PLATFORM:-}"
TARGET_PY="${BUNDLE_PYTHON_VERSION:-3.11}"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

rm -rf "${BUNDLE}"
mkdir -p "${BUNDLE}/wheels"

echo "==> [1/4] lock: hashed transitive lockfile (uv export --frozen)"
uv export --frozen --no-emit-project --format requirements-txt -o "${BUNDLE}/requirements.lock"

echo "==> [1/4] lock: project wheel + dependency wheels (offline-ready)"
uv build --wheel --out-dir "${BUNDLE}/wheels" >/dev/null
DL_ARGS=(download --require-hashes -r "${BUNDLE}/requirements.lock" -d "${BUNDLE}/wheels" --quiet)
if [[ -n "${TARGET_PLATFORM}" ]]; then
  DL_ARGS+=(--platform "${TARGET_PLATFORM}" --python-version "${TARGET_PY}" --only-binary=:all:)
fi
uvx pip "${DL_ARGS[@]}"

echo "==> [2/4] security: pip-audit (report-only; findings are recorded, not fatal)"
uvx pip-audit -r "${BUNDLE}/requirements.lock" --disable-pip \
  --format json --output "${BUNDLE}/pip-audit.json" \
  || echo "WARN: pip-audit reported findings — recorded in pip-audit.json (owner acknowledges before the visit)"

echo "==> [2/4] security: SBOM (CycloneDX)"
# pip-audit exits non-zero when it finds vulnerabilities even while writing a
# perfectly good SBOM — judge success by the artifact, not the exit code.
uvx pip-audit -r "${BUNDLE}/requirements.lock" --disable-pip \
  --format cyclonedx-json --output "${BUNDLE}/sbom.cdx.json" || true
[[ -s "${BUNDLE}/sbom.cdx.json" ]] || { echo "ERROR: SBOM was not generated"; exit 1; }

echo "==> [2/4] source archive (tracked files only — no .venv, no secrets)"
git archive --format=tar.gz -o "${BUNDLE}/src.tar.gz" HEAD

echo "==> [3/4] manifest (SLSA-style provenance; materials -> CHECKSUMS.sha256)"
cat > "${BUNDLE}/MANIFEST.json" <<EOF
{
  "builder": "developer workstation (role; FQDNs deliberately omitted)",
  "source": {"repo": "pogorelov-labs/ActionPulse", "commit": "${CODE_SHA}", "branch": "${BRANCH}"},
  "built_at_utc": "${BUILT_AT}",
  "target": {"platform": "${TARGET_PLATFORM:-build-host (set BUNDLE_PLATFORM for the inside host)}", "python": "${TARGET_PY}"},
  "materials": "every artifact + SHA-256 listed in CHECKSUMS.sha256 (wheels, lockfile, SBOM, audit, src.tar.gz)",
  "sbom": "sbom.cdx.json",
  "audit": "pip-audit.json",
  "runtime_env_required_by_name": ["EWS_PASSWORD", "LLM_TOKEN", "MM_WEBHOOK_URL"],
  "install_inside": "python3 -m venv venv && venv/bin/pip install --no-index --find-links wheels --require-hashes -r requirements.lock && venv/bin/pip install --no-index --no-deps wheels/digest_core-*.whl",
  "verify_inside": "shasum -a 256 -c CHECKSUMS.sha256",
  "honesty": "SLSA/in-toto define this provenance FORMAT; there is no standard attestation-transfer handshake across a courier-style air-gap. Treat as an auditable record, not a standards-compliant attestation chain (frontier bar F12 'thin' flag)."
}
EOF

echo "==> [3/4] checksums over every artifact"
(
  cd "${BUNDLE}"
  find . -type f ! -name CHECKSUMS.sha256 | LC_ALL=C sort | xargs shasum -a 256 > CHECKSUMS.sha256
)

echo "==> [4/4] offline verify: --no-index forbids any PyPI fallback"
VERIFY_DIR="$(mktemp -d)"
trap 'rm -rf "${VERIFY_DIR}"' EXIT
( cd "${BUNDLE}" && shasum -a 256 -c CHECKSUMS.sha256 >/dev/null ) && echo "    checksums: OK"
python3 -m venv "${VERIFY_DIR}/venv"
"${VERIFY_DIR}/venv/bin/pip" install --quiet --no-index \
  --find-links "${BUNDLE}/wheels" --require-hashes -r "${BUNDLE}/requirements.lock"
"${VERIFY_DIR}/venv/bin/pip" install --quiet --no-index --no-deps \
  "${BUNDLE}"/wheels/digest_core-*.whl
"${VERIFY_DIR}/venv/bin/python" -m digest_core.cli --help >/dev/null && echo "    offline install + CLI smoke: OK"

echo "==> bundle ready: ${BUNDLE}"
