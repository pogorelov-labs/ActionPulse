"""
ActionPulse Digest Core - Email digest generation with LLM.

Note: SCHEMA_VERSION / DEFAULT_PROMPT_VERSION below are package metadata kept
aligned to the authoritative ``run.PIPELINE_VERSION`` (1.2.0). The live daily
pipeline uses the Digest schema (1.0) + the extract_actions prompts. (These
constants used to describe the legacy EnhancedDigest gateway path, which was
deleted with the rest of the v2 surface.)
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
