"""Utility helpers for locating versioned prompt templates."""

from __future__ import annotations

from typing import Dict

PROMPT_TEMPLATE_MAP: Dict[str, str] = {
    "summarize.mvp.5": "summarize/mvp/v5/default.j2",
    "summarize.mvp5": "summarize/mvp/v5/default.j2",
    "summarize.v2": "summarize/v2/default.j2",
    "summarize.v2_hierarchical": "summarize/v2/default.j2",
    "summarize.v1": "summarize/v1/default.j2",
    "summarize.en.v1": "summarize/v1/en.j2",
    "thread_summarize.v1": "thread_summarize/v1/default.j2",
    "extract_actions.v1": "extract_actions.v1.txt",
    "extract_actions.en.v1": "extract_actions.en.v1.txt",
    "extract_actions.en.v2": "extract_actions.en.v2.txt",
}


def get_prompt_template_path(template_name: str) -> str:
    """Return the relative Jinja template path for the given template key.

    Args:
        template_name: Logical template key (e.g. ``"summarize.mvp.5"``).

    Returns:
        Relative path to the template inside the ``prompts`` directory.

    Raises:
        KeyError: If the template key is unknown.
    """

    try:
        return PROMPT_TEMPLATE_MAP[template_name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown prompt template '{template_name}'. Known templates: {sorted(PROMPT_TEMPLATE_MAP)}"
        ) from exc
