"""
ActionPulse Digest Core - Email digest generation with LLM.

Note: SCHEMA_VERSION / DEFAULT_PROMPT_VERSION below describe the legacy
EnhancedDigest gateway path. The live daily pipeline uses the Digest schema
(1.0) + extract_actions prompts and its own authoritative ``run.PIPELINE_VERSION``
(1.2.0); the constant here is kept aligned to it for package metadata.
"""

__version__ = "1.2.0"
PIPELINE_VERSION = "1.2.0"
SCHEMA_VERSION = "3.0"
DEFAULT_PROMPT_VERSION = "mvp.5"

__all__ = [
    "__version__",
    "PIPELINE_VERSION",
    "SCHEMA_VERSION",
    "DEFAULT_PROMPT_VERSION",
]
