"""Persistent encrypted message store (opt-in; default OFF).

A SQLCipher-encrypted SQLite archive of fetched messages for all sources, with
incremental upsert, a 30-day TTL sweep, and (search PR) FTS5 keyword +
brute-force-cosine semantic hybrid search. Configured via ``StoreConfig``; the
encryption key is ENV-only (``DIGEST_STORE_KEY``).

Importing this package never imports the SQLCipher driver — that happens only in
``MessageStore.open``. Use ``HAS_SQLCIPHER`` to degrade/skip when the ``store``
extra is not installed.
"""

from digest_core.store._driver import HAS_SQLCIPHER, INSTALL_HINT
from digest_core.store.db import MessageStore, StoreError

__all__ = ["MessageStore", "StoreError", "HAS_SQLCIPHER", "INSTALL_HINT"]
