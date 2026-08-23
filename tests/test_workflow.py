"""Delivery-mode guarantees in the GitHub Actions workflow."""

from pathlib import Path


WORKFLOW = Path(".github/workflows/daily.yml")


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_r2_delivery_requires_explicit_true_variable():
    text = workflow_text()
    assert "if: vars.ENABLE_R2_PUBLISH == 'true'" in text
    assert "if: vars.ENABLE_R2_PUBLISH != 'true'" in text


def test_email_step_has_no_r2_credentials():
    text = workflow_text()
    email_step = text.split("- name: Run pipeline and email episode", 1)[1].split(
        "- name: Run pipeline and publish to R2", 1
    )[0]
    assert "--email" in email_step
    assert "EMAIL_TO: ${{ secrets.EMAIL_TO }}" in email_step
    assert "R2_ACCOUNT_ID" not in email_step
    assert "FEED_TOKEN" not in email_step


def test_email_delivery_uses_persistent_daily_marker():
    text = workflow_text()
    assert "uses: actions/cache/restore@v4" in text
    assert "uses: actions/cache/save@v4" in text
    assert "--email-marker .email-state/sent" in text


def test_mp3_is_not_uploaded_as_action_artifact():
    text = workflow_text()
    artifact_step = text.split("- name: Upload run artifacts", 1)[1]
    assert "*.mp3" not in artifact_step
