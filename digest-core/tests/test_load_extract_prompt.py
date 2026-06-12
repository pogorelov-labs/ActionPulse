"""Prompt routing: (model, report language) -> prompt version (PR6 + L1).

RU reports keep the explicit per-model instruction-language map (output RU in
both of those prompts). EN reports (the default) use the en.v2 prompt -- EN
instructions and EN output -- for every model.
"""

from digest_core.run import _load_extract_prompt


def test_default_language_is_en_v2():
    for model in ("qwen35-397b-a17b", "qwen3-next-80b-a3b", "glm-4.7-flash", "other"):
        version, text = _load_extract_prompt(model)
        assert version == "extract_actions.en.v2"
        assert text


def test_ru_keeps_per_model_instruction_map():
    for model in ("qwen35-397b-a17b", "qwen3-next-80b-a3b", "qwen35-35b-a3b"):
        version, _ = _load_extract_prompt(model, "ru")
        assert version == "extract_actions.en.v1"
    version, _ = _load_extract_prompt("glm-4.7-flash", "ru")
    assert version == "extract_actions.v1"
    version, _ = _load_extract_prompt("some-other-model", "ru")
    assert version == "extract_actions.v1"


def test_all_prompts_require_verbatim_spans():
    _, en_v2 = _load_extract_prompt("qwen35-397b-a17b", "en")
    _, en_v1 = _load_extract_prompt("qwen35-397b-a17b", "ru")
    _, ru_v1 = _load_extract_prompt("glm-4.7-flash", "ru")
    for text in (en_v2, en_v1, ru_v1):
        assert "evidence_spans" in text
        assert ("VERBATIM" in text) or ("\u0414\u041e\u0421\u041b\u041e\u0412\u041d\u041e" in text)


def test_output_language_mandates():
    _, en_v2 = _load_extract_prompt("qwen35-397b-a17b", "en")
    assert "must be in English" in en_v2
    assert '"My actions", "Urgent", or "FYI"' in en_v2
    _, en_v1 = _load_extract_prompt("qwen35-397b-a17b", "ru")
    assert "must be in Russian" in en_v1


def test_quote_rule_keeps_source_language():
    # The citation-gate invariant survives the EN output contract.
    _, en_v2 = _load_extract_prompt("qwen35-397b-a17b", "en")
    assert "source language" in en_v2
    assert "do NOT translate" in en_v2
