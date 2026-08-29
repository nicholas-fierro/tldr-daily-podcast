"""Delivery-mode guarantees in the GitHub Actions workflow."""

from pathlib import Path


WORKFLOW = Path(".github/workflows/daily.yml")


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_r2_delivery_requires_explicit_true_variable():
    text = workflow_text()
    assert "vars.ENABLE_R2_PUBLISH == 'true'" in text
    assert "vars.ENABLE_R2_PUBLISH != 'true'" in text


def test_email_step_has_no_r2_credentials():
    text = workflow_text()
    email_step = text.split("- name: Run pipeline and email episode", 1)[1].split(
        "- name: Run pipeline and publish to R2", 1
    )[0]
    assert "--email" in email_step
    assert "EMAIL_TO: ${{ secrets.EMAIL_TO }}" in email_step
    assert "R2_ACCOUNT_ID" not in email_step
    assert "FEED_TOKEN" not in email_step


def test_email_delivery_marker_uses_actual_edition_identity():
    text = workflow_text()
    assert "uses: actions/cache/restore@v4" in text
    assert "uses: actions/cache/save@v4" in text
    assert "https://tldr.tech/api/latest/tech" in text
    assert "key: emailed-daily-${{ steps.edition_key.outputs.date }}" in text
    assert '--email-marker "${{ steps.edition_key.outputs.marker }}"' in text
    assert "format('--resolved-date {0}', steps.edition_key.outputs.date)" in text


def test_scheduled_runs_build_one_combined_episode():
    text = workflow_text()
    assert "strategy:" not in text
    assert "matrix" not in text
    assert "--bundle daily" in text
    assert "group: daily-combined" in text
    # One marker, one email, one episode per day.
    assert text.count("key: emailed-daily-") == 2
    assert text.count("--email \\") == 1


def test_delivery_marker_guard_runs_before_dependency_setup():
    text = workflow_text()
    assert text.index("- name: Restore email delivery marker") < text.index(
        "uses: actions/checkout@v4"
    )
    assert text.index("- name: Decide whether work is needed") < text.index(
        "uses: actions/setup-python@v5"
    )


def test_ffmpeg_uses_runner_or_retried_ubuntu_package():
    text = workflow_text()
    assert "FedericoCarboni/setup-ffmpeg" not in text
    assert "command -v ffmpeg" in text
    assert "sudo apt-get install -y ffmpeg" in text
    assert "for attempt in 1 2 3" in text


def test_mp3_is_not_uploaded_as_action_artifact():
    text = workflow_text()
    artifact_step = text.split("- name: Upload run artifacts", 1)[1]
    assert "*.mp3" not in artifact_step
