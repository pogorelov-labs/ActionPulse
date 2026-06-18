"""Lazy access to the encrypted SQLite driver (opt-in; not a base dependency).

The message store is default-OFF, so ``sqlcipher3`` must never be imported on the
common path. Both distributions expose the import name ``sqlcipher3``:

* Linux  — ``sqlcipher3-binary`` (prebuilt manylinux wheel, zero build);
* macOS  — ``sqlcipher3`` (source build of the bundled SQLCipher amalgamation,
  needs ``brew install sqlcipher openssl@3``).

``HAS_SQLCIPHER`` lets the CLI degrade with a clear message and the test suite
skip cleanly when the ``store`` extra is not installed.
"""

from __future__ import annotations

import re

try:  # pragma: no cover - presence is environment-dependent
    import sqlcipher3 as _sqlcipher  # noqa: F401

    HAS_SQLCIPHER = True
except ImportError:  # pragma: no cover
    HAS_SQLCIPHER = False

INSTALL_HINT = (
    "The encrypted message store needs the SQLCipher driver, which is an opt-in "
    "extra. Install it with:\n"
    "    uv sync --extra store\n"
    "(Linux uses the sqlcipher3-binary wheel; macOS builds sqlcipher3 from source "
    "and needs `brew install sqlcipher openssl@3`.)"
)

#: A 256-bit key rendered as 64 hex chars — what the wizard generates.
_RAW_KEY_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")


def key_pragma(key: str) -> str:
    """Render the ``PRAGMA key`` statement for ``key``.

    A 64-hex-char value is used as a SQLCipher **raw key** (``x'..'``) — no PBKDF2,
    the key is used verbatim. Anything else is treated as a passphrase (SQLCipher
    derives the key via PBKDF2). PRAGMA values cannot be parameter-bound, so the
    passphrase form escapes single quotes by doubling them.
    """
    if _RAW_KEY_RE.match(key):
        return f"PRAGMA key = \"x'{key}'\""
    escaped = key.replace("'", "''")
    return f"PRAGMA key = '{escaped}'"


def connect(path: str):
    """Open a raw autocommit DBAPI connection (no key set yet).

    Raises ``RuntimeError`` with an install hint when the driver is absent — the
    caller only reaches here when the store is explicitly enabled.
    """
    if not HAS_SQLCIPHER:
        raise RuntimeError(INSTALL_HINT)
    import sqlcipher3

    # isolation_level=None → autocommit; the store manages batches with explicit
    # BEGIN/COMMIT. PRAGMA key must be the first statement on the connection.
    return sqlcipher3.dbapi2.connect(path, isolation_level=None)
