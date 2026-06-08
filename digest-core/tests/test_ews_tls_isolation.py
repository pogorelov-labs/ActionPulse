"""Per-session TLS isolation (PR3).

``EWSIngest(verify_ssl=False)`` must not leak ``verify=False`` into other httpx /
requests clients (the LLM gateway, Mattermost). The old code monkey-patched
``requests.Session.request`` and ``httpx.Client.__init__`` process-globally; PR3
removed that and relies on exchangelib's per-instance ``BaseProtocol.SSL_CONTEXT``.
"""

import ssl

import httpx
import requests

from digest_core.config import EWSConfig
from digest_core.ingest.ews import EWSIngest


def _ews(verify_ssl: bool) -> EWSIngest:
    return EWSIngest(
        EWSConfig(
            endpoint="https://ews.corp/EWS/Exchange.asmx",
            user_upn="user@corp",
            verify_ssl=verify_ssl,
        )
    )


def test_global_ssl_monkeypatch_api_removed():
    # The process-global patch mechanism is gone entirely.
    assert not hasattr(EWSIngest, "_disable_ssl_verification")
    assert not hasattr(EWSIngest, "restore_ssl_verification")
    assert not hasattr(EWSIngest, "_ssl_verification_disabled")
    assert not hasattr(EWSIngest, "_original_request")


def test_insecure_ews_does_not_patch_httpx_or_requests():
    before_httpx_init = httpx.Client.__init__
    before_requests_request = requests.Session.request

    _ews(verify_ssl=False)

    # No global monkeypatch -> these stay pristine, so the LLM gateway and
    # Mattermost httpx clients keep their own (verifying) TLS settings.
    assert httpx.Client.__init__ is before_httpx_init
    assert requests.Session.request is before_requests_request


def test_fresh_httpx_client_still_verifies_after_insecure_ews():
    _ews(verify_ssl=False)
    client = httpx.Client()
    try:
        ssl_ctx = getattr(getattr(client._transport, "_pool", None), "_ssl_context", None)
    finally:
        client.close()
    # If httpx exposes its SSLContext, it must still be verifying (CERT_REQUIRED).
    if ssl_ctx is not None:
        assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
        assert ssl_ctx.check_hostname is True


def test_per_instance_ssl_context_reflects_verify_ssl():
    insecure = _ews(verify_ssl=False)
    assert insecure.ssl_context.verify_mode == ssl.CERT_NONE
    assert insecure.ssl_context.check_hostname is False

    secure = _ews(verify_ssl=True)
    assert secure.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert secure.ssl_context.check_hostname is True
