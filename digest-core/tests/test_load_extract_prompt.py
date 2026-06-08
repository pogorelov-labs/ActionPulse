"""Explicit model -> instruction-prompt map (PR6).

Replaces the fragile `"qwen" in name` check. Output stays RU in both prompts;
only the instruction language differs. The live default model keeps EN to remain
behavior-neutral.
"""

from digest_core.run import _load_extract_prompt


def test_qwen_models_use_en_instructions():
    for model in ("qwen35-397b-a17b", "qwen3-next-80b-a3b", "qwen35-35b-a3b"):
        version, text = _load_extract_prompt(model)
        assert version == "extract_actions.en.v1"
        assert text


def test_glm_uses_ru_instructions():
    version, _ = _load_extract_prompt("glm-4.7-flash")
    assert version == "extract_actions.v1"


def test_unknown_model_defaults_to_ru():
    version, _ = _load_extract_prompt("some-other-model")
    assert version == "extract_actions.v1"


def test_default_model_preserves_en_selection():
    # Behavior-neutral: the live default still resolves to EN instructions.
    version, _ = _load_extract_prompt("qwen35-397b-a17b")
    assert version == "extract_actions.en.v1"


def test_both_prompts_require_verbatim_span_and_keep_ru_output():
    _, en = _load_extract_prompt("qwen35-397b-a17b")
    _, ru = _load_extract_prompt("glm-4.7-flash")
    for text in (en, ru):
        assert "evidence_spans" in text
        assert ("VERBATIM" in text) or ("ДОСЛОВНО" in text)
        assert ("Russian" in text) or ("русском" in text)  # output mandate preserved
