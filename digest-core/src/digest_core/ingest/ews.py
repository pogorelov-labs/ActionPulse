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
from digest_core.utils.tz import ensure_aware, to_utc

logger = structlog.get_logger()


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


class EWSIngest:
    """EWS email ingestion with NTLM authentication."""

    def __init__(self, config: EWSConfig, time_config: TimeConfig = None, metrics=None):
        self.config = config
        self.time_config = time_config or TimeConfig()
        self.metrics = metrics
        self.account: Optional[Account] = None
        self._setup_ssl_context()

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
        user_tz = pytz.timezone(time_config.user_timezone)

        if time_config.window == "calendar_day":
            # Calendar day: 00:00:00 to 23:59:59 in user timezone
            start_date = datetime.strptime(digest_date, "%Y-%m-%d").replace(tzinfo=user_tz)
            end_date = start_date.replace(hour=23, minute=59, second=59)

            # Convert to UTC using our utilities
            start_utc = to_utc(start_date)
            end_utc = to_utc(end_date)

        else:  # rolling_24h
            # Rolling 24 hours from now (use standard UTC)
            now_utc = datetime.now(timezone.utc)
            end_utc = now_utc
            start_utc = now_utc - timedelta(hours=self.config.lookback_hours)

        logger.info(
            "Time window calculated",
            window_type=time_config.window,
            start_utc=start_utc.isoformat(),
            end_utc=end_utc.isoformat(),
        )

        return start_utc, end_utc

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(8),
        wait=tenacity.wait_exponential(multiplier=0.5, max=60),
        retry=tenacity.retry_if_exception_type((ConnectionError, TimeoutError)),
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

            while True:
                # Use folder.filter() with pagination
                page = folder.filter(filter_query)[offset : offset + self.config.page_size]
                page_list = list(page)

                if not page_list:
                    break

                messages.extend(page_list)
                offset += self.config.page_size

                logger.debug("Fetched page", page_size=len(page_list), total=len(messages))

                # Safety check to prevent infinite loops
                if len(page_list) < self.config.page_size:
                    break

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

    def fetch_messages(self, digest_date: str, time_config: TimeConfig) -> List[NormalizedMessage]:
        """Fetch and normalize messages for the given date."""
        logger.info("Starting EWS message fetch", digest_date=digest_date)

        # Connect to EWS
        account = self._connect()

        # Calculate time window
        start_date, end_date = self._get_time_window(digest_date, time_config)

        # Check SyncState/Watermark for incremental processing
        watermark = self._load_sync_state()
        if watermark:
            try:
                start_date_parsed = datetime.fromisoformat(watermark)
                # Ensure timezone aware and convert to UTC
                start_date = ensure_aware(
                    start_date_parsed, self.time_config.mailbox_tz, metrics=self.metrics
                )
                start_date = to_utc(start_date)
                logger.info(
                    "Using watermark for incremental window",
                    start=start_date.isoformat(),
                )
            except Exception as e:
                logger.warning(
                    "Invalid watermark format, doing full fetch",
                    watermark=watermark,
                    error=str(e),
                )
        # Fetch with retry over the computed window
        raw_messages = self._fetch_messages_with_retry(account.inbox, start_date, end_date)

        logger.info("Raw messages fetched", count=len(raw_messages))

        # Normalize messages
        normalized_messages = []
        for msg in raw_messages:
            try:
                normalized_msg = self._normalize_message(msg)
                normalized_messages.append(normalized_msg)
            except Exception as e:
                import traceback

                logger.warning(
                    "Failed to normalize message",
                    msg_id=str(msg.id),
                    error=str(e),
                    traceback=traceback.format_exc(),
                )
                continue

        logger.info("Messages normalized", count=len(normalized_messages))

        # Update SyncState with latest timestamp
        self._update_sync_state(end_date)

        return normalized_messages

    def _load_sync_state(self) -> Optional[str]:
        """Load SyncState/watermark (ISO timestamp) from file."""
        sync_state_path = Path(self.config.sync_state_path)
        if not sync_state_path.exists():
            logger.info("No SyncState file found, will perform full fetch")
            return None

        try:
            with open(sync_state_path, "r") as f:
                sync_state = f.read().strip()
            logger.info("SyncState loaded", path=str(sync_state_path))
            return sync_state
        except Exception as e:
            logger.warning("Failed to load SyncState", path=str(sync_state_path), error=str(e))
            return None

    # Note: Real EWS SyncFolderItems can be added later; MVP uses timestamp watermark

    def _update_sync_state(self, last_processed: datetime) -> None:
        """Update timestamp watermark for incremental processing."""
        sync_state_path = Path(self.config.sync_state_path)
        sync_state_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(sync_state_path, "w") as f:
                f.write(last_processed.isoformat())

            logger.debug(
                "SyncState updated",
                path=str(sync_state_path),
                timestamp=last_processed.isoformat(),
            )
        except Exception as e:
            logger.warning("Failed to update SyncState", path=str(sync_state_path), error=str(e))
