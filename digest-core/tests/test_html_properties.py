"""Property-based tests for the HTML normalizer (EP-9, frontier-audit F11).

The normalizer is the highest-risk untrusted-input parser in the pipeline (raw
corp email HTML hits it before anything else). Example-based tests only exercise
hand-picked fixtures; these properties let hypothesis hunt the fallback branches
(`html.py` bs4-failure → regex path, low-quality-parse → plaintext path).

Properties are scoped to what the implementation guarantees by construction —
the plaintext-fallback path intentionally returns the caller's text/plain
alternative nearly verbatim, so tag-stripping properties only apply to calls
without a fallback.
"""

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from digest_core.normalize.html import HTMLNormalizer

_SETTINGS = dict(
    max_examples=200,
    deadline=None,  # bs4 on adversarial input can be slow; flakiness > strictness here
    suppress_health_check=[HealthCheck.too_slow],
)

# Arbitrary unicode text, plus HTML-ish fragments that bias toward parser edges.
_raw_text = st.text(max_size=400)
_html_fragments = st.lists(
    st.sampled_from(
        [
            "<div>",
            "</div>",
            "<script>",
            "</script>",
            "<style>x{color:red}</style>",
            "<!--",
            "-->",
            "<table><tr><td>",
            "<ul><li>",
            "&amp;",
            "&nbsp;",
            "&#x27;",
            "<![CDATA[",
            "<<<",
            ">",
            '<a href="javascript:alert(1)">x</a>',
            '<img src=x onerror="alert(1)">',
            "<p style='display:none'>hidden</p>",
        ]
    ),
    max_size=20,
).map("".join)
_html_soup = st.tuples(_raw_text, _html_fragments, _raw_text).map("".join)


@given(content=_html_soup)
@settings(**_SETTINGS)
@example(content="")
@example(content="<")
@example(content="<script")  # unterminated tag
@example(content="<script>alert(1)")  # unterminated script body
@example(content="\x00\x01<div>\udcff</div>")  # control chars + lone surrogate
@example(content="<div>" * 300)  # deep nesting
@example(content="&amp;" * 200)  # entity soup
def test_never_raises_and_returns_contract(content):
    text, parse_ok = HTMLNormalizer().html_to_text(content)
    assert isinstance(text, str)
    assert isinstance(parse_ok, bool)


@given(content=_html_soup)
@settings(**_SETTINGS)
def test_deterministic(content):
    a = HTMLNormalizer().html_to_text(content)
    b = HTMLNormalizer().html_to_text(content)
    assert a == b


# Script/style BODIES must never leak into digest text (the bs4 path removes the
# elements wholesale; this is the security-relevant property for injected email).
_payload = st.text(
    alphabet=st.characters(blacklist_characters="<>&", blacklist_categories=("Cs",)),
    min_size=8,
    max_size=80,
).map(lambda s: "PAYLOAD" + s.replace("\x00", ""))


@given(payload=_payload)
@settings(**_SETTINGS)
def test_wellformed_script_body_never_leaks(payload):
    html = f"<html><body><p>hello</p><script>{payload}</script></body></html>"
    text, _ = HTMLNormalizer().html_to_text(html)
    assert payload not in text
    assert "hello" in text


@given(payload=_payload)
@settings(**_SETTINGS)
def test_event_handler_attributes_never_leak(payload):
    html = f'<div onclick="{payload}">visible</div>'
    text, _ = HTMLNormalizer().html_to_text(html)
    assert payload not in text
    assert "visible" in text


@given(payload=_payload)
@settings(**_SETTINGS)
def test_hidden_elements_stay_hidden(payload):
    html = f'<p>shown</p><p style="display:none">{payload}</p>'
    text, _ = HTMLNormalizer().html_to_text(html)
    assert payload not in text
    assert "shown" in text
