"""
Test EWS ingestion against the current EWSIngest contract.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from digest_core.config import EWSConfig, TimeConfig
from digest_core.ingest.ews import DELEGATE, NTLM, EWSIngest


@pytest.fixture
def ews_config(tmp_path):
    """Current EWS configuration for tests."""
    return EWSConfig(
        endpoint="https://mail.company.com/EWS/Exchange.asmx",
        user_upn="test@company.com",
        user_login="testuser",
        user_domain="company.com",
        verify_ca=None,
        verify_ssl=True,
        sync_state_path=str(tmp_path / "ews.syncstate"),
        page_size=2,
    )


@pytest.fixture
def time_config():
    """UTC time configuration for deterministic windows."""
    return TimeConfig(user_timezone="UTC", mailbox_tz="UTC", window="calendar_day")


@pytest.fixture
def ingester(ews_config, time_config):
    """EWS ingester with test configuration."""
    return EWSIngest(ews_config, time_config=time_config)


def test_connect_uses_ntlm_configuration(ingester, ews_config, monkeypatch):
    """Test connection setup uses NTLM credentials and disables autodiscover."""
    monkeypatch.setenv("EWS_PASSWORD", "test_password")

    with patch("digest_core.ingest.ews.Credentials") as mock_credentials:
        with patch("digest_core.ingest.ews.Configuration") as mock_configuration:
            with patch("digest_core.ingest.ews.Account") as mock_account:
                mock_account.return_value = Mock()

                ingester._connect()

                mock_credentials.assert_called_once_with(
                    username="testuser@company.com",
                    password="test_password",
                )
                mock_configuration.assert_called_once_with(
                    service_endpoint=ews_config.endpoint,
                    credentials=mock_credentials.return_value,
                    auth_type=NTLM,
                )
                mock_account.assert_called_once_with(
                    primary_smtp_address=ews_config.user_upn,
                    config=mock_configuration.return_value,
                    autodiscover=False,
                    access_type=DELEGATE,
                )


def test_tls_context_setup(ingester):
    """Test TLS context setup creates an SSL context."""
    with patch("digest_core.ingest.ews.ssl.create_default_context") as mock_create:
        mock_context = Mock()
        mock_create.return_value = mock_context

        ingester._setup_ssl_context()

        mock_create.assert_called_once()
        assert ingester.ssl_context is mock_context


def test_calendar_day_window(ingester, time_config):
    """Test calendar day window calculation."""
    start_time, end_time = ingester._get_time_window("2024-01-15", time_config)

    expected_start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    expected_end = datetime(2024, 1, 15, 23, 59, 59, tzinfo=timezone.utc)

    assert start_time == expected_start
    assert end_time == expected_end


def test_retryable_fetch_errors_include_exchangelib_transients():
    """The fetch retry must cover exchangelib's own transient transport errors, not just
    the builtin ConnectionError/TimeoutError (those do NOT subclass exchangelib's)."""
    from exchangelib.errors import ErrorServerBusy, RateLimitError, TransportError

    from digest_core.ingest.ews import _RETRYABLE_FETCH_ERRORS

    for exc in (ConnectionError, TimeoutError, TransportError, RateLimitError, ErrorServerBusy):
        assert issubclass(exc, _RETRYABLE_FETCH_ERRORS)


def test_ews_config_has_timeout_default():
    assert EWSConfig().timeout_s == 120.0


def test_rolling_24h_window(ingester):
    """Test rolling 24h window calculation."""
    rolling = TimeConfig(user_timezone="UTC", mailbox_tz="UTC", window="rolling_24h")

    start_time, end_time = ingester._get_time_window("2024-01-15", rolling)

    delta_hours = (end_time - start_time).total_seconds() / 3600
    assert 23.9 <= delta_hours <= 24.1
    assert end_time > start_time


def test_sync_state_loading(ingester, ews_config):
    """Test SyncState loading from file."""
    path = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc).isoformat()
    sync_state = ews_config.sync_state_path
    with open(sync_state, "w") as handle:
        handle.write(path)

    assert ingester._load_sync_state() == path


def test_sync_state_update(ingester, ews_config):
    """Test SyncState update to file."""
    test_timestamp = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)

    ingester._update_sync_state(test_timestamp)

    with open(ews_config.sync_state_path, "r") as handle:
        assert handle.read().strip() == test_timestamp.isoformat()


def test_message_normalization(ingester):
    """Test message normalization to NormalizedMessage."""
    mock_message = Mock()
    mock_message.internet_message_id = None
    mock_message.id = "test-message-id"
    mock_message.conversation_id = "test-conversation-id"
    mock_message.datetime_received = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
    mock_message.sender = Mock()
    mock_message.sender.email_address = "sender@company.com"
    mock_message.sender.name = "Sender Name"
    mock_message.to_recipients = [Mock(email_address="to@company.com")]
    mock_message.cc_recipients = [Mock(email_address="cc@company.com")]
    mock_message.subject = "Test Subject"
    mock_message.text_body = "Test body content"
    mock_message.importance = "High"
    mock_message.is_flagged = True
    mock_message.has_attachments = True
    attachment = Mock()
    attachment.name = "report.xlsx"
    mock_message.attachments = [attachment]

    normalized = ingester._normalize_message(mock_message)

    assert normalized.msg_id == "test-message-id"
    assert normalized.conversation_id == "test-conversation-id"
    assert normalized.datetime_received == datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)
    assert normalized.sender_email == "sender@company.com"
    assert normalized.from_email == "sender@company.com"
    assert normalized.from_name == "Sender Name"
    assert normalized.subject == "Test Subject"
    assert normalized.text_body == "Test body content"
    assert normalized.to_recipients == ["to@company.com"]
    assert normalized.cc_recipients == ["cc@company.com"]
    assert normalized.importance == "High"
    assert normalized.is_flagged is True
    assert normalized.has_attachments is True
    assert normalized.attachment_types == ["xlsx"]


def test_fetch_messages_with_retry_paginates_until_short_page(ingester):
    """Test page slicing continues until a short page is returned."""
    folder = Mock()
    filtered = MagicMock()
    filtered.__getitem__.side_effect = [
        [Mock(), Mock()],
        [Mock()],
    ]
    folder.filter.return_value = filtered

    start = datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 15, 23, 59, tzinfo=timezone.utc)
    messages = ingester._fetch_messages_with_retry(folder, start, end)

    assert len(messages) == 3
    assert folder.filter.call_count == 2


def test_fetch_messages_with_retry_retries_connection_error(ingester):
    """Test tenacity retries the fetch method on connection errors."""
    folder = Mock()
    filtered = MagicMock()
    filtered.__getitem__.side_effect = [[Mock()]]
    folder.filter.side_effect = [ConnectionError("boom"), filtered]

    start = datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 15, 23, 59, tzinfo=timezone.utc)
    messages = ingester._fetch_messages_with_retry(folder, start, end)

    assert len(messages) == 1
    assert folder.filter.call_count == 2


class _RecordingSink:
    """Duck-typed ProgressSink capturing the U2 intra-stage events."""

    def __init__(self):
        self.events = []

    def on_stage_progress(self, stage, done, total=None, unit="", detail=""):
        self.events.append(("progress", stage, done, total, unit, detail))

    def on_stage_retry(self, stage, attempt, max_attempts, reason):
        self.events.append(("retry", stage, attempt, max_attempts, reason))


def test_paging_emits_progress_events(ews_config, time_config):
    """U2: each fetched page surfaces running message counts (no total)."""
    sink = _RecordingSink()
    ingester = EWSIngest(ews_config, time_config=time_config, sink=sink)
    folder = Mock()
    filtered = MagicMock()
    filtered.__getitem__.side_effect = [
        [Mock(), Mock()],  # full page (page_size=2) -> continue
        [Mock()],  # short page -> stop
    ]
    folder.filter.return_value = filtered
    start = datetime(2026, 3, 28, tzinfo=timezone.utc)
    end = datetime(2026, 3, 29, tzinfo=timezone.utc)

    messages = ingester._fetch_messages_with_retry(folder, start, end)

    assert len(messages) == 3
    progress = [e for e in sink.events if e[0] == "progress"]
    assert progress[0] == ("progress", "ingest", 2, None, "messages", "page 1")
    assert progress[1] == ("progress", "ingest", 3, None, "messages", "page 2")
    assert ingester._fetch_pages == 2


def test_fetch_retry_emits_stage_retry(ews_config, time_config):
    """U2: a transient EWS failure surfaces as on_stage_retry and is counted."""
    sink = _RecordingSink()
    ingester = EWSIngest(ews_config, time_config=time_config, sink=sink)
    folder = Mock()
    filtered = MagicMock()
    filtered.__getitem__.side_effect = [[Mock()]]
    folder.filter.side_effect = [ConnectionError("boom"), filtered]
    start = datetime(2026, 3, 28, tzinfo=timezone.utc)
    end = datetime(2026, 3, 29, tzinfo=timezone.utc)

    messages = ingester._fetch_messages_with_retry(folder, start, end)

    assert len(messages) == 1
    retries = [e for e in sink.events if e[0] == "retry"]
    assert len(retries) == 1
    _, stage, attempt, max_attempts, reason = retries[0]
    assert (stage, attempt, max_attempts) == ("ingest", 2, 8)
    assert "ConnectionError" in reason and "boom" in reason
    assert ingester._fetch_retries == 1


class TestFolderResolution:
    """PR12b remainder: EWSConfig.folders honored (default ["Inbox"])."""

    def _account(self):
        account = Mock()
        account.inbox = Mock(name="inbox")
        account.sent = Mock(name="sent")
        account.inbox.parent = MagicMock()
        return account

    def test_inbox_case_insensitive(self, ingester):
        account = self._account()
        assert ingester._resolve_folder(account, "Inbox") is account.inbox
        assert ingester._resolve_folder(account, "INBOX") is account.inbox

    def test_well_known_names_map_to_account_attrs(self, ingester):
        account = self._account()
        assert ingester._resolve_folder(account, "Sent Items") is account.sent

    def test_named_folder_resolves_under_inbox_parent(self, ingester):
        account = self._account()
        reports = Mock(name="reports")
        account.inbox.parent.__truediv__ = Mock(return_value=reports)
        assert ingester._resolve_folder(account, "Reports") is reports

    def test_resolution_failure_returns_none(self, ingester):
        account = self._account()
        account.inbox.parent.__truediv__ = Mock(side_effect=KeyError("no such folder"))
        assert ingester._resolve_folder(account, "Nope") is None


class TestMultiFolderFetch:
    def _normalized(self, ingester, monkeypatch):
        from datetime import datetime, timezone

        def fake_normalize(msg):
            from digest_core.ingest.ews import NormalizedMessage

            return NormalizedMessage(
                msg_id=str(msg),
                conversation_id="",
                subject="s",
                sender_email="a@corp.ru",
                to_recipients=[],
                cc_recipients=[],
                datetime_received=datetime(2026, 6, 1, tzinfo=timezone.utc),
                text_body="b",
            )

        monkeypatch.setattr(ingester, "_normalize_message", fake_normalize)

    def _wire(self, ingester, monkeypatch, per_folder):
        account = Mock()
        account.inbox = Mock(name="inbox")
        reports = Mock(name="reports")
        account.inbox.parent = MagicMock()
        account.inbox.parent.__truediv__ = Mock(
            side_effect=lambda name: (
                reports if name == "Reports" else (_ for _ in ()).throw(KeyError(name))
            )
        )
        monkeypatch.setattr(ingester, "_connect", lambda: account)
        fetched = []

        def fake_fetch(folder, start, end):
            fetched.append(folder)
            ingester._fetch_pages = 1  # what the paging loop records per call
            return [f"m-{len(fetched)}-{i}" for i in range(per_folder)]

        monkeypatch.setattr(ingester, "_fetch_messages_with_retry", fake_fetch)
        self._normalized(ingester, monkeypatch)
        return account, reports, fetched

    def test_fetches_every_configured_folder_and_sums_pages(
        self, ews_config, time_config, monkeypatch
    ):
        ews_config = ews_config.model_copy(update={"folders": ["Inbox", "Reports"]})
        ingester = EWSIngest(ews_config, time_config=time_config)
        account, reports, fetched = self._wire(ingester, monkeypatch, per_folder=2)

        messages = ingester.fetch_messages("2026-06-01", time_config)

        assert fetched == [account.inbox, reports]
        assert len(messages) == 4
        assert ingester.last_fetch_stats["pages"] == 2  # summed across folders

    def test_unresolved_folder_is_skipped_others_still_fetch(
        self, ews_config, time_config, monkeypatch
    ):
        ews_config = ews_config.model_copy(update={"folders": ["Inbox", "Nope"]})
        ingester = EWSIngest(ews_config, time_config=time_config)
        account, _, fetched = self._wire(ingester, monkeypatch, per_folder=1)

        messages = ingester.fetch_messages("2026-06-01", time_config)

        assert fetched == [account.inbox]  # the bad folder degraded, run survived
        assert len(messages) == 1
