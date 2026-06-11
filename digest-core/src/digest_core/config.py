"""
Configuration management using pydantic-settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import structlog
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TimeConfig(BaseModel):
    """Time zone and window configuration."""

    user_timezone: str = Field(default="Europe/Moscow", description="User timezone")
    window: str = Field(
        default="calendar_day", description="Window mode: calendar_day | rolling_24h"
    )
    mailbox_tz: str = Field(
        default="Europe/Moscow",
        description="Mailbox timezone for normalizing naive datetime",
    )
    runner_tz: str = Field(default="America/Sao_Paulo", description="Runner/job timezone")
    fail_on_naive: bool = Field(default=True, description="Fail if naive datetime is encountered")


class EWSConfig(BaseModel):
    """Exchange Web Services configuration."""

    endpoint: str = Field(default="", description="EWS endpoint URL")
    user_upn: str = Field(default="", description="User UPN (user@corp)")
    user_login: Optional[str] = Field(
        default=None, description="User login for NTLM (e.g., ivanov)"
    )
    user_domain: Optional[str] = Field(
        default=None, description="Domain for NTLM (e.g., corp-domain.ru)"
    )
    password_env: str = Field(
        default="EWS_PASSWORD", description="Environment variable for password"
    )
    verify_ca: Optional[str] = Field(default=None, description="Path to CA certificate")
    verify_ssl: bool = Field(default=True, description="Enable SSL certificate verification")
    autodiscover: bool = Field(default=False, description="Enable autodiscover")
    folders: List[str] = Field(default=["Inbox"], description="Folders to process")
    lookback_hours: int = Field(default=24, description="Hours to look back")
    page_size: int = Field(default=100, description="Page size for pagination")
    sync_state_path: str = Field(default=".state/ews.syncstate", description="Sync state file path")
    user_aliases: List[str] = Field(
        default_factory=list,
        description="User email aliases for AddressedToMe detection",
    )

    def __init__(self, **kwargs):
        # Читаем значения из переменных окружения если они не заданы
        env_values = {
            "endpoint": os.getenv("EWS_ENDPOINT", ""),
            "user_upn": os.getenv("EWS_USER_UPN", ""),
            "user_login": os.getenv("EWS_USER_LOGIN"),
            "user_domain": os.getenv("EWS_USER_DOMAIN"),
        }

        # Применяем значения из переменных окружения только если они не заданы явно
        for key, env_value in env_values.items():
            if key not in kwargs and env_value:
                kwargs[key] = env_value

        super().__init__(**kwargs)

    def get_password(self) -> str:
        """Get EWS password from environment.

        This method should be used when you have an EWSConfig instance directly.
        For Config instances, use Config.get_ews_password() instead.
        """
        password = os.getenv(self.password_env)
        if not password:
            raise ValueError(f"Environment variable {self.password_env} not set")
        return password

    def get_ntlm_username(self) -> str:
        """Get username for NTLM authentication (login@domain format)."""
        if self.user_login and self.user_domain:
            return f"{self.user_login}@{self.user_domain}"

        # Fallback: use user_upn if login/domain not specified
        if self.user_upn and "@" in self.user_upn:
            return self.user_upn

        raise ValueError(
            "Cannot determine NTLM username: user_login and user_domain not set, and user_upn is invalid"
        )


# Hard output ceiling of the corp gateway's flagship model. Oversize `max_tokens`
# comes back as HTTP 429 (not 413), so clamp client-side instead of surfacing it
# as an opaque rate-limit error. See docs/CORP_VALIDATION_FINDINGS_2026-06.md F-18.
GATEWAY_MAX_OUTPUT_TOKENS = 16384


class LLMConfig(BaseModel):
    """LLM Gateway configuration."""

    endpoint: str = Field(default="", description="LLM Gateway endpoint")
    model: str = Field(default="qwen35-397b-a17b", description="Model identifier")
    timeout_s: int = Field(default=120, description="Request timeout in seconds")
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for extraction calls (0.0 = deterministic)",
    )
    max_output_tokens: int = Field(
        default=6000,
        ge=1,
        description=(
            "Max completion tokens per LLM call; a real production day measured 5,226,"
            " so the former hardcoded 2000 truncated. Values above the gateway ceiling"
            " are clamped."
        ),
    )
    headers: Dict[str, str] = Field(default_factory=dict, description="Additional headers")
    max_tokens_per_run: int = Field(default=30000, description="Max tokens per run")
    cost_limit_per_run: float = Field(default=5.0, description="Cost limit per run in USD")
    rate_limit_rpm: int = Field(
        default=15, description="Default per-model RPM when a model is absent from fleet_rpm"
    )
    fleet_rpm: Dict[str, float] = Field(
        default_factory=lambda: {
            "qwen35-397b-a17b": 15,
            "qwen3-next-80b-a3b": 45,
            "glm-4.7-flash": 60,
            "qwen35-35b-a3b": 30,
            "bge-m3": 30,
            "qwen3-embedding": 30,
            "bge-reranker-v2-m3": 10,
        },
        description="Per-model request-per-minute buckets for the gateway fleet (RateBroker)",
    )
    fleet_burst: int = Field(default=3, description="Token-bucket burst size per model")
    stage_call_budgets: Dict[str, int] = Field(
        default_factory=lambda: {
            "extractor": 2,
            "reranker": 10,
            "embeddings": 30,
            "judge": 8,
            "tokenize": 20,
        },
        description="Hard per-stage call budgets enforced per run by the RateBroker",
    )
    strict_json: bool = Field(
        default=True, description="Enforce strict JSON validation with Pydantic"
    )
    max_retries: int = Field(default=3, description="Maximum retry attempts for invalid JSON")

    @field_validator("max_output_tokens")
    @classmethod
    def _clamp_max_output_tokens(cls, v: int) -> int:
        if v > GATEWAY_MAX_OUTPUT_TOKENS:
            structlog.get_logger().warning(
                "llm.max_output_tokens exceeds the gateway output ceiling; clamping",
                requested=v,
                ceiling=GATEWAY_MAX_OUTPUT_TOKENS,
            )
            return GATEWAY_MAX_OUTPUT_TOKENS
        return v

    def __init__(self, **kwargs):
        # Читаем значения из переменных окружения если они не заданы
        env_values = {
            "endpoint": os.getenv("LLM_ENDPOINT", ""),
        }

        # Применяем значения из переменных окружения только если они не заданы явно
        for key, env_value in env_values.items():
            if key not in kwargs and env_value:
                kwargs[key] = env_value

        super().__init__(**kwargs)

    def get_token(self) -> str:
        """Get LLM token from environment."""
        token = os.getenv("LLM_TOKEN")
        if not token:
            raise ValueError("Environment variable LLM_TOKEN not set")
        return token


class ObservabilityConfig(BaseModel):
    """Observability configuration."""

    prometheus_port: int = Field(default=9108, description="Prometheus metrics port")
    log_level: str = Field(default="INFO", description="Log level")


class MattermostDeliverConfig(BaseModel):
    """Mattermost delivery configuration."""

    enabled: bool = Field(default=True, description="Enable Mattermost delivery")
    webhook_url_env: str = Field(
        default="MM_WEBHOOK_URL", description="Environment variable with webhook URL"
    )
    max_message_length: int = Field(default=16383, description="Mattermost max message size")
    include_trace_footer: bool = Field(default=True, description="Append trace footer to delivery")

    def get_webhook_url(self) -> str:
        """Return the Mattermost incoming webhook URL."""
        webhook_url = os.getenv(self.webhook_url_env, "")
        if not webhook_url:
            raise ValueError(f"Environment variable {self.webhook_url_env} not set")
        return webhook_url


class DeliverConfig(BaseModel):
    """Delivery target configuration."""

    mattermost: MattermostDeliverConfig = Field(default_factory=MattermostDeliverConfig)


class SelectionBucketsConfig(BaseModel):
    """Configuration for balanced evidence selection buckets."""

    threads_top: int = Field(default=10, description="Minimum threads to cover (1 chunk each)")
    addressed_to_me: int = Field(default=8, description="Minimum chunks with AddressedToMe=true")
    dates_deadlines: int = Field(default=6, description="Minimum chunks with dates/deadlines")
    critical_senders: int = Field(default=4, description="Minimum chunks from sender_rank>=2")
    per_thread_max: int = Field(default=3, description="Maximum chunks per thread")
    max_total_chunks: int = Field(default=20, description="Maximum total chunks to select")


class SelectionWeightsConfig(BaseModel):
    """Feature weights for evidence chunk scoring."""

    recency: float = Field(default=2.0, description="Weight for message recency (hours)")
    addressed_to_me: float = Field(default=3.0, description="Weight for AddressedToMe flag")
    action_verbs: float = Field(default=1.5, description="Weight per action verb found")
    question_mark: float = Field(default=1.0, description="Weight for questions")
    dates_found: float = Field(default=1.5, description="Weight per date/deadline found")
    importance_high: float = Field(default=2.0, description="Weight for High importance")
    is_flagged: float = Field(default=1.5, description="Weight for flagged messages")
    has_doc_attachments: float = Field(
        default=1.0, description="Weight for doc/xlsx/pdf attachments"
    )
    sender_rank: float = Field(default=1.0, description="Weight multiplier per sender rank level")
    thread_activity: float = Field(default=0.5, description="Weight for thread activity")
    negative_prior: float = Field(
        default=-2.0, description="Penalty for noreply/unsubscribe patterns"
    )
    # Fused relevance score (PR9). enable_relevance stays False until PC-2; when off,
    # scoring is byte-identical to the legacy enhanced score.
    enable_relevance: bool = Field(
        default=False, description="Fuse embeddings/reranker relevance into the chunk score"
    )
    w_meta: float = Field(default=1.0, description="Weight on the metadata component (fused)")
    w_rerank: float = Field(default=1.0, description="Weight on the relevance component (fused)")


class ContextBudgetConfig(BaseModel):
    """Configuration for context token budget."""

    max_total_tokens: int = Field(default=7000, description="Maximum total tokens for LLM input")
    per_thread_max: int = Field(default=3, description="Maximum chunks per thread")


class ChunkingConfig(BaseModel):
    """Configuration for message chunking."""

    long_email_tokens: int = Field(default=1000, description="Threshold for long email")
    max_chunks_if_long: int = Field(default=3, description="Max chunks for long emails")
    max_chunks_default: int = Field(default=12, description="Default max chunks per message")
    adaptive_high_load_emails: int = Field(
        default=200, description="Email count threshold for high load"
    )
    adaptive_high_load_threads: int = Field(
        default=60, description="Thread count threshold for high load"
    )
    adaptive_multiplier: float = Field(default=0.75, description="Multiplier for high load")


class ShrinkConfig(BaseModel):
    """Configuration for auto-shrink behavior."""

    enable_auto_shrink: bool = Field(default=True, description="Enable auto-shrink on overflow")
    preserve_min_quotas: bool = Field(
        default=True, description="Preserve minimum bucket quotas during shrink"
    )


class EmailCleanerConfig(BaseModel):
    """Configuration for email body cleaning (quotes, signatures, disclaimers)."""

    enabled: bool = Field(default=True, description="Enable email body cleaning")
    keep_top_quote_head: bool = Field(
        default=True, description="Keep 1-2 paragraphs from top-level quote"
    )
    max_top_quote_paragraphs: int = Field(
        default=2, description="Max paragraphs to keep from top quote"
    )
    max_top_quote_lines: int = Field(default=10, description="Max lines to keep from top quote")
    max_quote_removal_length: int = Field(
        default=10000,
        description="Max chars to remove in single quote block (safety limit)",
    )

    locales: List[str] = Field(
        default=["ru", "en"], description="Supported locales for pattern matching"
    )

    # Pattern whitelists (regexes that should NOT be removed even if in quoted/signature area)
    whitelist_patterns: List[str] = Field(
        default_factory=lambda: [
            r"\b(deadline|срок|дедлайн|до)\s+\d{1,2}[./]\d{1,2}",  # Deadlines
            r"\b(approve|одобр|согласов)",  # Approval requests
        ],
        description="Patterns to preserve even in quoted areas",
    )

    # Pattern blacklists (additional patterns to aggressively remove)
    blacklist_patterns: List[str] = Field(
        default_factory=lambda: [
            r"Click here to unsubscribe",
            r"Нажмите.*отписаться",
            r"Privacy Policy",
            r"Политика конфиденциальности",
        ],
        description="Additional patterns to remove aggressively",
    )

    # Track removed spans for offset mapping
    track_removed_spans: bool = Field(
        default=True, description="Track removed text spans for offset mapping"
    )


class HierarchicalConfig(BaseModel):
    """Configuration for hierarchical digest mode."""

    enable: bool = Field(default=True, description="Enable hierarchical mode")
    auto_enable: bool = Field(default=True, description="Auto-enable based on thresholds")
    enable_auto: bool = Field(
        default=True, description="Enable automatic hierarchical mode activation"
    )
    threshold_threads: int = Field(
        default=40, description="Thread count threshold for auto activation"
    )
    threshold_emails: int = Field(
        default=200, description="Email count threshold for auto activation"
    )
    min_threads_to_summarize: int = Field(
        default=6, description="Minimum threads required to use hierarchical mode"
    )
    min_threads: int = Field(default=60, description="Min threads to auto-activate (was 30)")
    min_emails: int = Field(default=300, description="Min emails to auto-activate (was 150)")

    per_thread_max_chunks_in: int = Field(
        default=8, description="Max chunks per thread for summarization"
    )
    per_thread_max_chunks_exception: int = Field(
        default=12,
        description="Max chunks in exceptional cases (mentions, last update)",
    )
    summary_max_tokens: int = Field(default=90, description="Max tokens for thread summary")
    parallel_pool: int = Field(default=8, description="Max parallel thread summarization workers")
    timeout_sec: int = Field(default=20, description="Timeout per thread summarization")
    degrade_on_timeout: str = Field(
        default="best_2_chunks", description="Degradation strategy on timeout"
    )

    # Must-include chunks
    must_include_mentions: bool = Field(
        default=True, description="Always include chunks with user mentions"
    )
    must_include_last_update: bool = Field(
        default=True, description="Always include last update chunk per thread"
    )

    # Merge policy
    merge_max_citations: int = Field(default=5, description="Max citations in merged summary (3-5)")
    merge_include_title: bool = Field(
        default=True, description="Include brief title in merged summary"
    )

    # Optimization
    skip_llm_if_no_evidence: bool = Field(
        default=True, description="Skip LLM call if no evidence after selection"
    )

    final_input_token_cap: int = Field(
        default=4000, description="Max tokens for final aggregator input"
    )
    max_latency_increase_pct: int = Field(
        default=50, description="Max acceptable latency increase %"
    )
    target_latency_increase_pct: int = Field(default=30, description="Target latency increase %")
    max_cost_increase_per_email_pct: int = Field(
        default=40, description="Max acceptable cost increase per email %"
    )


class NLPConfig(BaseModel):
    """Configuration for NLP features (lemmatization, action extraction)."""

    # Custom action verbs: form → lemma mapping for domain-specific actions
    custom_action_verbs: Dict[str, str] = Field(
        default_factory=lambda: {
            # EN domain-specific examples
            "deploy": "deploy",
            "deployed": "deploy",
            "deploying": "deploy",
            "merge": "merge",
            "merged": "merge",
            "merging": "merge",
            # RU domain-specific examples
            "задеплоить": "задеплоить",
            "задеплой": "задеплоить",
            "замержить": "замержить",
            "замержь": "замержить",
        },
        description="Custom verb forms for domain-specific action extraction",
    )


class RankerConfig(BaseModel):
    """Configuration for digest item ranking."""

    enabled: bool = Field(
        default=False,
        description="Enable post-LLM reordering of items via DigestRanker (per section)",
    )

    # Feature weights (will be normalized to sum to 1.0)
    weight_user_in_to: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Weight for user as direct recipient (To)",
    )
    weight_user_in_cc: float = Field(
        default=0.05, ge=0.0, le=1.0, description="Weight for user as CC recipient"
    )
    weight_has_action: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Weight for item containing action markers",
    )
    weight_has_mention: float = Field(
        default=0.10, ge=0.0, le=1.0, description="Weight for item mentioning user"
    )
    weight_has_due_date: float = Field(
        default=0.15, ge=0.0, le=1.0, description="Weight for item having a deadline"
    )
    weight_sender_importance: float = Field(
        default=0.10, ge=0.0, le=1.0, description="Weight for sender being important"
    )
    weight_thread_length: float = Field(
        default=0.05, ge=0.0, le=1.0, description="Weight for long conversation thread"
    )
    weight_recency: float = Field(
        default=0.10, ge=0.0, le=1.0, description="Weight for recent message"
    )
    weight_has_attachments: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Weight for message having attachments",
    )
    weight_has_project_tag: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Weight for message having a project tag (e.g., JIRA)",
    )

    important_senders: List[str] = Field(
        default_factory=list,
        description="List of important sender email addresses or domain patterns (e.g., 'ceo@', 'example.com')",
    )

    log_positions: bool = Field(default=True, description="Log item positions for A/B analysis")


class DegradeConfig(BaseModel):
    """Configuration for LLM failure degradation."""

    enable: bool = Field(default=True, description="Enable degradation on LLM failures")
    mode: str = Field(default="extractive", description="Degradation mode: extractive | empty")


class RerankerConfig(BaseModel):
    """Cross-encoder reranker support scoring for the P2 gate (PR8).

    enabled stays False until PC-2 (per-endpoint data-handling ADR). The reranker
    is the scarce fleet resource (non-batchable), so it is budgeted per run and
    only spent on low-confidence items.
    """

    enabled: bool = Field(default=False, description="Use the reranker for support scores")
    tau: float = Field(default=0.0, description="Support-score threshold for weak_evidence")
    budget_per_run: int = Field(default=10, description="Max reranker calls per run")
    low_confidence_threshold: float = Field(
        default=0.7, description="Only items below this confidence spend the reranker"
    )
    # PR11 flip. recall_floor 0.0 keeps exit codes neutral until a real floor is
    # set from PR10 calibration; exit 2 fires only when support_recall < recall_floor.
    recall_floor: float = Field(
        default=0.0, description="Min support recall before --validate-citations exits 2"
    )
    tau_repair: float = Field(
        default=0.0, description="Higher support bar a re-selected span must clear (PR11)"
    )


class JudgeConfig(BaseModel):
    """LLM-judge + calibration config (PR10). Off until PC-2; live run untouched."""

    enabled: bool = Field(default=False, description="Run the LLM judge over digest items")
    model: str = Field(default="qwen35-35b-a3b", description="Judge model (own RPM bucket)")
    target_recall: float = Field(default=0.90, description="Calibrate tau at recall >= this")
    min_samples_per_stratum: int = Field(
        default=20, description="Min gold samples/stratum to trust a calibrated tau"
    )


class Config(BaseSettings):
    """Main configuration class."""

    # Sub-configurations
    time: TimeConfig = Field(default_factory=TimeConfig)
    ews: EWSConfig = Field(default_factory=EWSConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    deliver: DeliverConfig = Field(default_factory=DeliverConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    selection_buckets: SelectionBucketsConfig = Field(default_factory=SelectionBucketsConfig)
    selection_weights: SelectionWeightsConfig = Field(default_factory=SelectionWeightsConfig)
    context_budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    shrink: ShrinkConfig = Field(default_factory=ShrinkConfig)
    hierarchical: HierarchicalConfig = Field(default_factory=HierarchicalConfig)
    email_cleaner: EmailCleanerConfig = Field(default_factory=EmailCleanerConfig)
    nlp: NLPConfig = Field(default_factory=NLPConfig)
    ranker: RankerConfig = Field(default_factory=RankerConfig)
    degrade: DegradeConfig = Field(default_factory=DegradeConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    def __init__(self, **kwargs):
        # First, load defaults
        super().__init__(**kwargs)

        # Then load from YAML files (in order of precedence)
        yaml_configs = self._load_yaml_configs()

        # Apply YAML configs (lower precedence first)
        for yaml_config in yaml_configs:
            self._apply_yaml_config(yaml_config)

    def _load_yaml_configs(self) -> List[Dict]:
        """Load YAML configuration files in order of precedence."""
        configs = []

        # 1. Load config.example.yaml (lowest precedence)
        example_path = PROJECT_ROOT / "configs/config.example.yaml"
        if example_path.exists():
            try:
                with open(example_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    if config:
                        configs.append(config)
            except Exception as e:
                print(f"Warning: Failed to load {example_path}: {e}")

        # 2. Load config.yaml (higher precedence)
        user_path = PROJECT_ROOT / "configs/config.yaml"
        if user_path.exists():
            try:
                with open(user_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    if config:
                        configs.append(config)
            except Exception as e:
                print(f"Warning: Failed to load {user_path}: {e}")

        # 3. Load from DIGEST_CONFIG_PATH (highest precedence)
        custom_path = os.getenv("DIGEST_CONFIG_PATH")
        if custom_path:
            custom_path = Path(custom_path).expanduser()
            if custom_path.exists():
                try:
                    with open(custom_path, "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                        if config:
                            configs.append(config)
                except Exception as e:
                    print(f"Warning: Failed to load {custom_path}: {e}")

        return configs

    def _apply_yaml_config(self, yaml_config: Dict) -> None:
        """Apply YAML configuration to current config.

        Every section now carries an ``env_prefix`` so that any field can be
        overridden via ``DIGEST_<PREFIX>_<FIELD>`` environment variables
        (e.g. ``DIGEST_LLM_TIMEOUT_S=300``).  Explicit ``env_field_map``
        entries are kept for backward-compatibility with the original
        ``EWS_ENDPOINT`` / ``LLM_ENDPOINT`` names.
        """
        if "time" in yaml_config:
            self._merge_model(self.time, yaml_config["time"], env_prefix="TIME")
        if "ews" in yaml_config:
            self._merge_model(
                self.ews,
                yaml_config["ews"],
                env_field_map={
                    "endpoint": "EWS_ENDPOINT",
                    "user_upn": "EWS_USER_UPN",
                    "user_login": "EWS_USER_LOGIN",
                    "user_domain": "EWS_USER_DOMAIN",
                },
                env_prefix="EWS",
            )
        if "llm" in yaml_config:
            self._merge_model(
                self.llm,
                yaml_config["llm"],
                env_field_map={"endpoint": "LLM_ENDPOINT"},
                env_prefix="LLM",
            )
        if "deliver" in yaml_config:
            mattermost_config = yaml_config["deliver"].get("mattermost", {})
            self._merge_model(self.deliver.mattermost, mattermost_config, env_prefix="MM")
        if "observability" in yaml_config:
            self._merge_model(self.observability, yaml_config["observability"], env_prefix="OBS")
        if "selection_buckets" in yaml_config:
            self._merge_model(
                self.selection_buckets,
                yaml_config["selection_buckets"],
                env_prefix="SEL_BUCKETS",
            )
        if "selection_weights" in yaml_config:
            self._merge_model(
                self.selection_weights,
                yaml_config["selection_weights"],
                env_prefix="SEL_WEIGHTS",
            )
        if "context_budget" in yaml_config:
            self._merge_model(
                self.context_budget,
                yaml_config["context_budget"],
                env_prefix="CTX_BUDGET",
            )
        if "chunking" in yaml_config:
            self._merge_model(self.chunking, yaml_config["chunking"], env_prefix="CHUNKING")
        if "shrink" in yaml_config:
            self._merge_model(self.shrink, yaml_config["shrink"], env_prefix="SHRINK")
        if "hierarchical" in yaml_config:
            self._merge_model(
                self.hierarchical,
                yaml_config["hierarchical"],
                env_prefix="HIERARCHICAL",
            )
        if "email_cleaner" in yaml_config:
            self._merge_model(
                self.email_cleaner,
                yaml_config["email_cleaner"],
                env_prefix="EMAIL_CLEANER",
            )
        if "nlp" in yaml_config:
            self._merge_model(self.nlp, yaml_config["nlp"], env_prefix="NLP")
        if "ranker" in yaml_config:
            self._merge_model(self.ranker, yaml_config["ranker"], env_prefix="RANKER")
        if "degrade" in yaml_config:
            self._merge_model(self.degrade, yaml_config["degrade"], env_prefix="DEGRADE")

    def _merge_model(
        self,
        model: BaseModel,
        values: Dict,
        env_field_map: Optional[Dict[str, str]] = None,
        env_prefix: Optional[str] = None,
    ) -> None:
        """Merge YAML values into an existing model while preserving ENV precedence.

        ENV override is checked in this order for each field:
        1. Explicit ``env_field_map`` entry (e.g. ``{"endpoint": "EWS_ENDPOINT"}``).
        2. Generic ``DIGEST_{env_prefix}_{FIELD}`` when *env_prefix* is given.
        If the corresponding ENV variable is set (non-empty), the YAML value is
        skipped so that the operator's ENV always wins.
        """
        env_field_map = env_field_map or {}
        for key, value in values.items():
            if not hasattr(model, key):
                continue
            # Check explicit mapping first
            env_var = env_field_map.get(key)
            if env_var and os.getenv(env_var):
                continue
            # Check generic prefix-based mapping
            if env_prefix and not env_var:
                generic_env = f"DIGEST_{env_prefix}_{key}".upper()
                if os.getenv(generic_env):
                    continue
            setattr(model, key, value)

    def get_ews_password(self) -> str:
        """Get EWS password from environment.

        This method delegates to the EWSConfig.get_password() method.
        Use this method when you have a Config instance.
        """
        return self.ews.get_password()

    def get_llm_token(self) -> str:
        """Get LLM token from environment."""
        return self.llm.get_token()
