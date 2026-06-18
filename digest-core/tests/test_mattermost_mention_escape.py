"""@-mention escaping in delivered Mattermost digests.

Mattermost parses @handle/@channel/@here/@all out of a posted message's TEXT and
notifies those users, with no per-post opt-out. A digest renders LLM-extracted
titles (and, later, quoted chat), so a quoted "@ivan" would ping a real person.
``escape_mentions`` wraps each mention token in a backtick code span (which the
server-side mention parser skips), without touching mid-word "@" (email
addresses). These tests pin the escaper and its application in the delivered
message. All synthetic.
"""

from __future__ import annotations

from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer, escape_mentions
from digest_core.llm.schemas import Digest, Item, Section

# --------------------------------------------------------------------------- #
# escape_mentions unit behavior
# --------------------------------------------------------------------------- #


def test_plain_text_unchanged():
    assert escape_mentions("Approve the Q4 budget") == "Approve the Q4 budget"
    assert escape_mentions("") == ""


def test_handle_is_wrapped():
    assert escape_mentions("ping @ivan before EOD") == "ping `@ivan` before EOD"


def test_broadcasts_are_wrapped():
    assert escape_mentions("@channel please review") == "`@channel` please review"
    assert escape_mentions("@here standup now") == "`@here` standup now"
    assert escape_mentions("@all hands") == "`@all` hands"


def test_mention_at_start_and_after_punctuation():
    assert escape_mentions("@ivan ok") == "`@ivan` ok"
    assert escape_mentions("(@ivan)") == "(`@ivan`)"


def test_multiple_mentions():
    assert escape_mentions("see @ivan and @petrov-v") == "see `@ivan` and `@petrov-v`"


def test_email_address_is_not_touched():
    # Mid-word "@" is not a Mattermost mention and must not be escaped.
    assert escape_mentions("mail bob@corp.com please") == "mail bob@corp.com please"
    assert escape_mentions("user@example.org") == "user@example.org"


def test_idempotent_on_already_escaped():
    once = escape_mentions("ping @ivan")
    assert escape_mentions(once) == once  # already-wrapped token is not re-wrapped


def test_bare_at_is_left_alone():
    # A lone "@" with no handle is not a mention trigger.
    assert escape_mentions("email me @ 5pm") == "email me @ 5pm"


# --------------------------------------------------------------------------- #
# applied in the delivered Mattermost message
# --------------------------------------------------------------------------- #


def _digest(title: str) -> Digest:
    item = Item(
        title=title,
        evidence_id="ev-1",
        confidence=0.8,
        source_ref={"type": "email", "msg_id": "m1"},
    )
    return Digest(
        prompt_version="extract_actions.en.v2",
        digest_date="2026-06-18",
        trace_id="t-escape",
        sections=[Section(title="My actions", items=[item])],
    )


def _render(title: str) -> str:
    deliverer = MattermostDeliverer(MattermostDeliverConfig(), language="en")
    return deliverer._format_digest(_digest(title))


def test_delivered_message_escapes_a_quoted_handle():
    out = _render("Reply to @ivan about the contract")
    assert "`@ivan`" in out
    # The mention-triggering bare token must not appear outside the code span.
    assert "to @ivan about" not in out


def test_delivered_message_leaves_email_addresses_intact():
    out = _render("Forward the note to bob@corp.com")
    assert "bob@corp.com" in out
    assert "`@corp" not in out


def test_delivered_message_unchanged_without_mentions():
    out = _render("Approve the vendor contract")
    assert "Approve the vendor contract" in out
    assert "`" not in out
