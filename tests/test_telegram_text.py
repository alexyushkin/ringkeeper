"""What Telegram may show: limits by kind, the "shortened" note, and never SQL or arguments."""

import pytest

from ringkeeper.config import Settings
from ringkeeper.models import Decision, DecisionKind, InstanceType
from ringkeeper.telegram import text
from ringkeeper.telegram.bridge import render

SETTINGS = Settings.model_validate({})


def question(body: str) -> Decision:
    return Decision(kind=DecisionKind.QUESTION, project="acme-web", payload={"question": body})


def test_the_defaults_are_160_for_summaries_and_1000_for_questions() -> None:
    assert (SETTINGS.telegram_summary_chars, SETTINGS.telegram_question_chars) == (160, 1000)


def test_a_question_up_to_1000_characters_is_shown_whole_with_its_line_breaks() -> None:
    body = (
        "Before I touch anything on prod n8n (acme-n8n), please confirm:\n"
        "(1) Which workflow(s) and which nodes should get the line?\n"
        "(2) Which node parameter should the line go into?\n\n\n\n"
        '(3) Is the exact text "voice works"?'
    )
    rendered = render(question(body), SETTINGS)
    assert "(1) Which workflow(s) and which nodes should get the line?\n(2)" in rendered
    assert "?\n\n(3) Is the exact text" in rendered  # blank-line runs collapsed
    assert text.SHORTENED not in rendered
    assert rendered.endswith("Reply to this message with your answer.")


def test_a_longer_question_is_cut_on_a_word_and_says_so() -> None:
    body = "Which of these should I use? " + "option " * 300
    shown = text.question(body, 1000)
    assert shown.cut
    assert len(shown.text) <= 1000
    assert shown.text.endswith("option…")
    rendered = render(question(body), SETTINGS)
    assert text.SHORTENED in rendered


@pytest.mark.parametrize(
    "code",
    [
        "DO $$ BEGIN PERFORM purge(); END $$",
        "$fn$ select secret from vault $fn$",
        "WITH gone AS (DELETE FROM t RETURNING id) SELECT 1",
        "COPY customers TO STDOUT",
        "CALL purge_rows(30)",
        "EXECUTE format('DROP TABLE %I', name)",
    ],
)
def test_more_code_shapes_are_hidden(code: str) -> None:
    hidden, changed = text.hide_code(f"Is this fine? {code}")
    assert changed
    assert "purge" not in hidden.lower()
    assert "customers" not in hidden
    assert "secret" not in hidden
    assert "DROP" not in hidden


@pytest.mark.parametrize(
    "plain",
    [
        "Should I execute on staging first?",
        "Call Bob (the admin) about it?",
        "Copy notes to the wiki?",
    ],
)
def test_plain_english_stays(plain: str) -> None:
    assert text.hide_code(plain) == (plain, False)


def test_approvals_and_plans_stay_one_short_line() -> None:
    held = Decision(
        kind=DecisionKind.APPROVE, project="acme-web", instance="acme-supabase-prod",
        service=InstanceType.SUPABASE, tool="execute",
        args={"sql": "UPDATE customers SET plan = 'gold' WHERE email = 'vip@acme.test'"},
    )  # fmt: skip
    rendered = render(held, SETTINGS)
    assert rendered.splitlines()[1] == "'execute' on acme-supabase-prod: waits for your OK"
    assert "UPDATE" not in rendered
    assert "vip@acme.test" not in rendered
    assert text.SHORTENED not in rendered

    plan = Decision(
        kind=DecisionKind.PLAN, project="acme-web",
        payload={"title": "Refactor " + "everything " * 40, "plan": "SECRET PLAN TEXT"},
    )  # fmt: skip
    rendered = render(plan, SETTINGS)
    assert len(rendered.splitlines()[1]) <= 160
    assert "SECRET PLAN TEXT" not in rendered
    assert text.SHORTENED in rendered


@pytest.mark.parametrize(
    "body",
    [
        "Shall I run this?\n```sql\nDELETE FROM customers WHERE plan = 'trial';\n```\nOK?",
        "Shall I run `UPDATE customers SET plan = 'gold' WHERE id = 7`?",
        "I'd run UPDATE customers SET plan = 'gold' WHERE id = 7; is that right?",
        "Proposed:\nselect id, email from customers where plan = 'trial'\n\nGo ahead?",
        "Proposed: DROP TABLE customers_old; fine?",
        "Should I TRUNCATE audit_tmp before the import?",
        "Should I GRANT SELECT, INSERT ON customers TO reporting?",
    ],
)
def test_sql_and_code_in_a_question_never_reach_telegram(body: str) -> None:
    shown = text.question(body, 1000)
    assert shown.cut
    lowered = shown.text.lower()
    leaks = ("customers where", "set plan", "'gold'", "drop table", "audit_tmp", "to reporting")
    for leak in leaks:
        assert leak not in lowered, shown.text
    assert text.SQL_HIDDEN in shown.text or text.CODE_HIDDEN in shown.text


@pytest.mark.parametrize(
    "body",
    [
        "Update the welcome text to mention the new plans?",
        "Select which workflow from the list should change?",
        "With the current setup, should I create a new table of contents in the README?",
        "Delete the old draft too, or keep it?",
    ],
)
def test_plain_questions_that_start_like_sql_are_left_alone(body: str) -> None:
    shown = text.question(body, 1000)
    assert (shown.text, shown.cut) == (body, False)
