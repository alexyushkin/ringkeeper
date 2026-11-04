"""Claude's Markdown, for places that can't show it."""

import pytest

from ringkeeper.telegram import text as shown_text
from ringkeeper.wording import plain


@pytest.mark.parametrize(
    ("written", "shown"),
    [
        ("has **10 workflows**: **3 active**", "has 10 workflows: 3 active"),
        ("The `demo-n8n` instance", "The demo-n8n instance"),
        ("# Done", "Done"),
        ("- one\n- two", "• one\n• two"),
        ("> quoted", "quoted"),
        ("see [the docs](https://example.com/a)", "see the docs (https://example.com/a)"),
        ("__bold__ and *soft* and _soft_", "bold and soft and soft"),
        ("~~gone~~", "gone"),
    ],
)
def test_marks_become_plain_words(written: str, shown: str) -> None:
    assert plain(written) == shown


@pytest.mark.parametrize(
    "written", ["snake_case_name", "2*3*4", "a ** b", "5 * 4 * 3", "file_name and other_name"]
)
def test_what_only_looks_like_a_mark_stays(written: str) -> None:
    assert plain(written) == written


def test_sql_is_still_hidden_before_marks_go() -> None:
    shown = shown_text.question("Run `UPDATE orders SET paid = true`?", 500)
    assert "UPDATE" not in shown.text
    assert shown_text.SQL_HIDDEN in shown.text
