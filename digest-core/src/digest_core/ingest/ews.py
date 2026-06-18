"""
Exchange Web Services (EWS) email ingestion with NTLM authentication.
"""

import structlog
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from dataclasses import dataclass, field
from pathlib import Path
import pytz
from exchangelib import (
    Credentials,
    Account,
    DELEGATE,
    Configuration,
    NTLM,
    Message,
    Folder,
    Q,
    EWSDateTime,
)
from exchangelib.protocol import BaseProtocol
import tenacity
import ssl

from digest_core.config import EWSConfig, TimeConfig
from digest_core.ingest.watermark import SourceWatermark
from digest_core.progress import NullSink, ProgressSink, emit
from digest_core.utils.tz import ensure_aware, to_utc

logger = structlog.get_logger()

#: Max fetch attempts (tenacity stop_after_attempt below) — named so the
#: retry events can report an honest "retry n/8".
FETCH_MAX_ATTEMPTS = 8


def _emit_fetch_retry(retry_state: "tenacity.RetryCallState") -> None:
    """tenacity ``before_sleep`` for the fetch: make the backoff legible.

    The retried callable is a bound method, so ``args[0]`` is the EWSIngest
    instance — this is how a module-level hook reaches the run's sink.
    """
    ingest = retry_state.args[0]
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    reason = f"{type(exc).__name__}: {exc}" if exc else "unknown error"
    ingest._fetch_retries += 1
    emit(
        ingest._sink,
        "on_stage_retry",
        "ingest",
        retry_state.attempt_number + 1,
        FETCH_MAX_ATTEMPTS,
        reason,
    )


@dataclass(frozen=True, init=False)
class NormalizedMessage:
    """Normalized email message with canonical email metadata fields."""

    msg_id: str
    conversation_id: Optional[str]
    datetime_received: datetime
    sender_email: str
    subject: str
    text_body: str
    to_recipients: List[str]
    cc_recipients: List[str]
    importance: str  # "Low" | "Normal" | "High"
    is_flagged: bool
    has_attachments: bool
    attachment_types: List[str]  # ["pdf", "xlsx", ...]

    # Canonical email metadata fields for forward/backward compatibility
    from_email: str = ""
    from_name: Optional[str] = None
    to_emails: List[str] = field(default_factory=list)
    cc_emails: List[str] = field(default_factory=list)
    message_id: str = ""
    body_norm: str = ""
    received_at: Optional[datetime] = None

    #: Source TYPE of the message ("email" | "mm"), NOT the adapter name. EWS
    #: messages keep the "email" default; a Mattermost adapter sets "mm". This
    #: drives the markdown-safe normalize branch (run.py), the authoritative
    #: ``source_ref['type']`` (evidence/split.py) and the source-aware threading
    #: branch (threads/build.py). It is deliberately NOT part of
    #: ``_content_sha256`` (run.py), so existing email content hashes — and thus
    #: idempotency/replay — are unchanged by adding it.
    source: str = "email"

    #: Mattermost channel TYPE for an mm message — 'O'/'P' (open/private "op"
    #: channels), 'D' (1:1 direct), 'G' (group DM). ``None`` for email and for any
    #: mm message whose channel type was not derivable. This is an AUDIT carrier:
    #: it lets a later dump-redaction pass identify DM-sourced text ('D'/'G')
    #: without re-deriving the type from the channel object. Like ``source`` it is
    #: kw-only with a default and is deliberately NOT part of ``_content_sha256``
    #: (run.py hashes only msg_id|subject|body), so existing email/mm content
    #: hashes — and thus idempotency/replay — are byte-identical after adding it.
    mm_channel_type: Optional[str] = None

    def __init__(
        self,
        msg_id: str,
        conversation_id: Optional[str],
        datetime_received: Optional[datetime] = None,
        sender_email: str = "",
        subject: str = "",
        text_body: str = "",
        to_recipients: Optional[List[str]] = None,
        cc_recipients: Optional[List[str]] = None,
        importance: str = "Normal",
        is_flagged: bool = False,
        has_attachments: bool = False,
        attachment_types: Optional[List[str]] = None,
        *,
        sender: Optional[str] = None,
        from_email: str = "",
        from_name: Optional[str] = None,
        to_emails: Optional[List[str]] = None,
        cc_emails: Optional[List[str]] = None,
        message_id: str = "",
        body_norm: str = "",
        received_at: Optional[datetime] = None,
        source: str = "email",
        mm_channel_type: Optional[str] = None,
    ) -> None:
        sender_email = sender_email or sender or from_email
        to_recipients = list(to_recipients or to_emails or [])
        cc_recipients = list(cc_recipients or cc_emails or [])
        attachment_types = list(attachment_types or [])
        if datetime_received is None:
            datetime_received = received_at or datetime.now(timezone.utc)

        object.__setattr__(self, "msg_id", msg_id)
        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "datetime_received", datetime_received)
        object.__setattr__(self, "sender_email", sender_email)
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "text_body", text_body)
        object.__setattr__(self, "to_recipients", to_recipients)
        object.__setattr__(self, "cc_recipients", cc_recipients)
        object.__setattr__(self, "importance", importance)
        object.__setattr__(self, "is_flagged", is_flagged)
        object.__setattr__(self, "has_attachments", has_attachments)
        object.__setattr__(self, "attachment_types", attachment_types)
        object.__setattr__(self, "from_email", from_email)
        object.__setattr__(self, "from_name", from_name)
        object.__setattr__(self, "to_emails", list(to_emails or []))
        object.__setattr__(self, "cc_emails", list(cc_emails or []))
        object.__setattr__(self, "message_id", message_id)
        object.__setattr__(self, "body_norm", body_norm)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "mm_channel_type", mm_channel_type)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.from_email:
            object.__setattr__(self, "from_email", self.sender_email)
        if not self.to_emails:
            object.__setattr__(self, "to_emails", list(self.to_recipients))
        if not self.cc_emails:
            object.__setattr__(self, "cc_emails", list(self.cc_recipients))
        if not self.message_id:
            object.__setattr__(self, "message_id", self.msg_id)
        if not self.body_norm:
            object.__setattr__(self, "body_norm", self.text_body)
        if self.received_at is None:
            object.__setattr__(self, "received_at", self.datetime_received)

    @property
    def sender(self) -> str:
        """Backward compatibility alias for sender_email."""
        return self.from_email or self.sender_email or ""


def compute_time_window(
    digest_date: str, time_config: TimeConfig, lookback_hours: int = 24
) -> tuple[datetime, datetime]:
    """Pure digest time-window math, returning aware-UTC ``(start, end)``.

    Shared by EWS ingest and any other source (e.g. the Mattermost adapter) so
    all sources use one window definition. Pure — no I/O, no global side effects
    (unlike constructing an ``EWSIngest``, whose ``__init__`` mutates exchangelib's
    SSL context). ``lookback_hours`` is used only for the ``rolling_24h`` window.
    """
    user_tz = pytz.timezone(time_config.user_timezone)

    if time_config.window == "calendar_day":
        # Calendar day: 00:00:00 to 23:59:59 in user timezone
        start_date = datetime.strptime(digest_date, "%Y-%m-%d").replace(tzinfo=user_tz)
        end_date = start_date.replace(hour=23, minute=59, second=59)
        start_utc = to_utc(start_date)
        end_utc = to_utc(end_date)
    else:  # rolling_24h
        now_utc = datetime.now(timezone.utc)
        end_utc = now_utc
        start_utc = now_utc - timedelta(hours=lookback_hours)

    logger.info(
        "Time window calculated",
        window_type=time_config.window,
        start_utc=start_utc.isoformat(),
        end_utc=end_utc.isoformat(),
    )
    return start_utc, end_utc


class EWSIngest:
    """EWS email ingestion with NTLM authentication."""

    def __init__(
        self,
        config: EWSConfig,
        time_config: TimeConfig = None,
        metrics=None,
        sink: Optional[ProgressSink] = None,
        incremental: bool = True,
    ):
        self.config = config
        self.time_config = time_config or TimeConfig()
        self.metrics = metrics
        self.account: Optional[Account] = None
        self._sink = sink or NullSink()
        self._fetch_retries = 0
        self._fetch_pages = 0
        # Incremental load: when True (a normal "today" run) the per-source
        # watermark narrows the fetch window to "since last seen". An explicit
        # back-dated run sets this False so the full requested window is fetched
        # (a back-fill must not be truncated by a watermark from a later run).
        self.incremental = incremental
        # Stage-health read-out for run_meta (pages/retries/skipped messages).
        self.last_fetch_stats: dict = {}
        self._setup_ssl_context()

    def _watermark(self) -> SourceWatermark:
        """The EWS high-water mark, bound to the historical ``ews.syncstate`` file
        (path honors ``ews.sync_state_path`` / the ``--state`` override)."""
        resolved = self.config.resolved_sync_state_path()
        return SourceWatermark(state_dir=Path(resolved).parent, source="ews", filename=resolved)

    def _setup_ssl_context(self):
        """Setup SSL context based on configuration.

        Three modes:
        1. verify_ssl=false: Disable all SSL verification (TESTING ONLY!)
        2. verify_ca specified: Use custom CA certificate
        3. Default: Use system CA certificates

        Warning:
            Setting verify_ssl=false disables SSL verification globally
            for all EWS connections in this process. Use only for testing!
        """
        # Create SSL context once
        self.ssl_context = ssl.create_default_context()

        if not self.config.verify_ssl:
            # Полностью отключаем SSL verification для тестирования
            self.ssl_context.check_hostname = False  # Не проверяем hostname
            self.ssl_context.verify_mode = ssl.CERT_NONE  # Не проверяем сертификат
            logger.warning(
                "SSL verification disabled (verify_ssl=false)",
                extra={"security_warning": "Use only for testing!"},
            )
        elif self.config.verify_ca:
            # Use custom CA certificate
            try:
                self.ssl_context.load_verify_locations(self.config.verify_ca)
                logger.info(
                    "SSL context configured with corporate CA",
                    ca_path=self.config.verify_ca,
                )
            except FileNotFoundError as e:
                logger.error(
                    "Corporate CA certificate not found",
                    ca_path=self.config.verify_ca,
                    error=str(e),
                )
                raise
        else:
            # Use default system CA
            logger.warning("Using system CA certificates for SSL verification")

    def _connect(self) -> Account:
        """Establish EWS connection with NTLM authentication."""
        if self.account is not None:
            return self.account

        logger.info("Connecting to EWS", endpoint=self.config.endpoint)

        # Create credentials with NTLM username (login@domain)
        ntlm_username = self.config.get_ntlm_username()
        credentials = Credentials(username=ntlm_username, password=self.config.get_password())

        logger.debug("Using NTLM authentication", username=ntlm_username)

        # Apply the per-instance SSL context to exchangelib (EWS connections only).
        # Scoped to exchangelib's protocol — it does NOT touch the LLM gateway or
        # Mattermost httpx clients (PR3: no process-global verify=False monkeypatch).
        BaseProtocol.SSL_CONTEXT = self.ssl_context

        # Create configuration with NTLM auth and explicit service endpoint
        config_obj = Configuration(
            service_endpoint=self.config.endpoint,
            credentials=credentials,
            auth_type=NTLM,
        )

        # Create account with explicit settings
        self.account = Account(
            primary_smtp_address=self.config.user_upn,
            config=config_obj,
            autodiscover=False,  # Explicitly disable autodiscover
            access_type=DELEGATE,
        )

        logger.info(
            "EWS connection established",
            endpoint=self.config.endpoint,
            user="[[REDACTED]]",  # Маскируем email в логах
            auth_type="NTLM",
        )
        return self.account

    def _get_time_window(
        self, digest_date: str, time_config: TimeConfig
    ) -> tuple[datetime, datetime]:
        """Calculate time window for email fetching. Returns UTC datetimes."""
        return compute_time_window(digest_date, time_config, self.config.lookback_hours)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(FETCH_MAX_ATTEMPTS),
        wait=tenacity.wait_exponential(multiplier=0.5, max=60),
        retry=tenacity.retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=_emit_fetch_retry,
    )
    def _fetch_messages_with_retry(
        self, folder: Folder, start_date: datetime, end_date: datetime
    ) -> List[Message]:
        """Fetch messages with retry logic."""
        try:
            # Create EWS datetime objects (only if not already EWSDateTime)
            if isinstance(start_date, EWSDateTime):
                start_ews = start_date
            else:
                start_ews = EWSDateTime.from_datetime(start_date)

            if isinstance(end_date, EWSDateTime):
                end_ews = end_date
            else:
                end_ews = EWSDateTime.from_datetime(end_date)

            # Create filter for last 24 hours
            filter_query = Q(datetime_received__gte=start_ews, datetime_received__lte=end_ews)

            # Fetch messages with pagination
            messages = []
            offset = 0
            page_no = 0

            while True:
                # Use folder.filter() with pagination
                page = folder.filter(filter_query)[offset : offset + self.config.page_size]
                page_list = list(page)

                if not page_list:
                    break

                messages.extend(page_list)
                offset += self.config.page_size
                page_no += 1

                logger.debug("Fetched page", page_size=len(page_list), total=len(messages))
                # Unbounded loop: no total, so counters only (§3 — never a
                # percentage for an estimated total).
                emit(
                    self._sink,
                    "on_stage_progress",
                    "ingest",
                    len(messages),
                    None,
                    "messages",
                    f"page {page_no}",
                )

                # Safety check to prevent infinite loops
                if len(page_list) < self.config.page_size:
                    break

            self._fetch_pages = page_no
            return messages

        except Exception as e:
            logger.warning("EWS fetch failed, retrying", error=str(e))
            raise

    def _normalize_message(self, msg: Message) -> NormalizedMessage:
        """Normalize EWS message to our format."""
        # Get message ID (prefer InternetMessageId, fallback to EWS ID)
        msg_id = getattr(msg, "internet_message_id", None) or str(msg.id)
        if msg_id and msg_id.startswith("<") and msg_id.endswith(">"):
            msg_id = msg_id[1:-1]  # Remove angle brackets
        msg_id = (msg_id or "").lower()

        # Normalize conversation ID (convert ConversationId object to string)
        conversation_id = getattr(msg, "conversation_id", None)
        if conversation_id:
            # ConversationId is an object from exchangelib
            # Try to extract the actual ID value
            try:
                # ConversationId might have an 'id' attribute or need str() conversion
                if hasattr(conversation_id, "id"):
                    conversation_id = str(conversation_id.id)
                elif hasattr(conversation_id, "__str__"):
                    conversation_id = str(conversation_id)
                else:
                    # Fallback: use repr
                    conversation_id = repr(conversation_id)
            except Exception as e:
                logger.warning(
                    "Failed to extract conversation_id",
                    conversation_id_type=type(conversation_id).__name__,
                    error=str(e),
                )
                conversation_id = ""
        else:
            conversation_id = ""

        # Get sender email address
        sender_email = ""
        if msg.sender and hasattr(msg.sender, "email_address") and msg.sender.email_address:
            sender_email = msg.sender.email_address.lower()

        # Get recipients
        to_recipients = []
        if hasattr(msg, "to_recipients") and msg.to_recipients:
            to_recipients = [
                r.email_address.lower()
                for r in msg.to_recipients
                if hasattr(r, "email_address") and r.email_address
            ]

        cc_recipients = []
        if hasattr(msg, "cc_recipients") and msg.cc_recipients:
            cc_recipients = [
                r.email_address.lower()
                for r in msg.cc_recipients
                if hasattr(r, "email_address") and r.email_address
            ]

        # Get text body (prefer text_body, fallback to body)
        text_body = ""
        if hasattr(msg, "text_body") and msg.text_body:
            text_body = msg.text_body
        elif hasattr(msg, "body") and msg.body:
            text_body = str(msg.body)

        # Convert datetime to standard Python datetime with UTC timezone
        # msg.datetime_received might be EWSDateTime, convert to standard datetime
        datetime_received = msg.datetime_received

        # If it's EWSDateTime, convert to standard datetime
        if isinstance(datetime_received, EWSDateTime):
            # EWSDateTime can be converted to standard datetime
            datetime_received = datetime(
                datetime_received.year,
                datetime_received.month,
                datetime_received.day,
                datetime_received.hour,
                datetime_received.minute,
                datetime_received.second,
                datetime_received.microsecond,
                tzinfo=datetime_received.tzinfo,
            )

        # Ensure timezone aware using mailbox_tz and convert to UTC
        datetime_received = ensure_aware(
            datetime_received, self.time_config.mailbox_tz, metrics=self.metrics
        )
        datetime_received = to_utc(datetime_received)

        # Extract importance (Low, Normal, High)
        importance = "Normal"
        if hasattr(msg, "importance") and msg.importance:
            importance = str(msg.importance)

        # Extract flagged status
        is_flagged = False
        if hasattr(msg, "is_flagged") and msg.is_flagged:
            is_flagged = bool(msg.is_flagged)

        # Extract attachments
        has_attachments = False
        attachment_types = []
        if hasattr(msg, "has_attachments") and msg.has_attachments:
            has_attachments = True
            # Try to extract attachment types
            if hasattr(msg, "attachments") and msg.attachments:
                for attachment in msg.attachments:
                    if hasattr(attachment, "name") and attachment.name:
                        # Extract file extension
                        name = str(attachment.name)
                        if "." in name:
                            ext = name.rsplit(".", 1)[-1].lower()
                            if ext and ext not in attachment_types:
                                attachment_types.append(ext)

        # Extract sender name if available
        from_name = None
        if msg.sender and hasattr(msg.sender, "name") and msg.sender.name:
            from_name = str(msg.sender.name)

        return NormalizedMessage(
            msg_id=msg_id,
            conversation_id=conversation_id,
            datetime_received=datetime_received,
            sender_email=sender_email,
            subject=msg.subject or "",
            text_body=text_body,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            importance=importance,
            is_flagged=is_flagged,
            has_attachments=has_attachments,
            attachment_types=attachment_types,
            # Canonical fields for forward/backward compatibility
            from_email=sender_email,
            from_name=from_name,
            to_emails=to_recipients,
            cc_emails=cc_recipients,
            message_id=msg_id,
            body_norm=text_body,
            received_at=datetime_received,
        )

    def _resolve_folder(self, account: Account, name: str):
        """Resolve a configured folder name to an exchangelib folder.

        "Inbox" (any case) is the canonical inbox; a few well-known mailbox
        folders map to their locale-independent account attributes; anything
        else resolves by display name under the inbox's parent (the message
        root) — display names are locale-dependent, so unresolved names skip
        with a warning rather than crash the run.
        """
        well_known = {
            "inbox": "inbox",
            "sent": "sent",
            "sent items": "sent",
            "drafts": "drafts",
            "junk": "junk",
            "archive": "archive",
            "trash": "trash",
            "deleted items": "trash",
        }
        attr = well_known.get((name or "").strip().lower())
        if attr:
            return getattr(account, attr, None)
        try:
            return account.inbox.parent / name
        except Exception as e:  # noqa: BLE001 - unresolved folder degrades, never crashes
            logger.warning("Failed to resolve EWS folder", folder=name, error=str(e))
            return None

    def fetch_messages(self, digest_date: str, time_config: TimeConfig) -> List[NormalizedMessage]:
        """Fetch and normalize messages for the given date."""
        logger.info("Starting EWS message fetch", digest_date=digest_date)
        self._fetch_retries = 0
        self._fetch_pages = 0

        # Connect to EWS
        account = self._connect()

        # Calculate time window
        start_date, end_date = self._get_time_window(digest_date, time_config)

        # Incremental window: when enabled, narrow the fetch to "since the last
        # seen message" (minus the overlap re-read window). A malformed/absent
        # watermark degrades to the full window inside SourceWatermark. An explicit
        # back-dated run (incremental=False) always fetches the full window.
        if self.incremental:
            incremental_start = self._watermark().effective_start(start_date)
            if incremental_start != start_date:
                start_date = incremental_start
                logger.info(
                    "Using watermark for incremental window",
                    start=start_date.isoformat(),
                )
        # Fetch with retry over the computed window — one pass per configured
        # folder (PR12b remainder: EWSConfig.folders, default ["Inbox"]). A
        # folder that cannot be resolved is skipped with a warning; the others
        # still fetch (degrade-not-drop at the folder boundary).
        raw_messages = []
        total_pages = 0
        for folder_name in self.config.folders or ["Inbox"]:
            folder = self._resolve_folder(account, folder_name)
            if folder is None:
                logger.warning("EWS folder not found, skipping", folder=folder_name)
                continue
            raw_messages.extend(self._fetch_messages_with_retry(folder, start_date, end_date))
            total_pages += self._fetch_pages
        self._fetch_pages = total_pages

        logger.info("Raw messages fetched", count=len(raw_messages))

        # Normalize messages
        normalized_messages = []
        skipped = 0
        for msg in raw_messages:
            try:
                normalized_msg = self._normalize_message(msg)
                normalized_messages.append(normalized_msg)
            except Exception as e:
                import traceback

                skipped += 1
                logger.warning(
                    "Failed to normalize message",
                    msg_id=str(msg.id),
                    error=str(e),
                    traceback=traceback.format_exc(),
                )
                continue

        logger.info("Messages normalized", count=len(normalized_messages))
        self.last_fetch_stats = {
            "pages": self._fetch_pages,
            "retries": self._fetch_retries,
            "skipped": skipped,
        }

        # Advance the watermark to the latest message actually seen (NOT the
        # window end) so a message arriving after the last fetched item is not
        # skipped on the next run. No-op on a quiet window (observed_max is None).
        if self.incremental:
            observed_max = max((m.datetime_received for m in normalized_messages), default=None)
            self._watermark().advance(observed_max)

        return normalized_messages

    # Note: Real EWS SyncFolderItems can be added later; MVP uses a timestamp
    # watermark, now via the shared ``ingest.watermark.SourceWatermark`` engine.
    # These two methods remain as back-compat shims over that engine.

    def _load_sync_state(self) -> Optional[str]:
        """The stored watermark as an ISO-8601 string (or ``None`` if absent)."""
        dt = self._watermark().load()
        return dt.isoformat() if dt else None

    def _update_sync_state(self, last_processed: datetime) -> None:
        """Persist ``last_processed`` as the watermark."""
        self._watermark().advance(last_processed)
