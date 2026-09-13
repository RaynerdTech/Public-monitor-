from pathlib import Path

import aiosqlite
import pytest

from app.core import database
from app.core.models import ReferralCandidate
from app.services import retry_queue
from app.services.pipeline import process_post
from app.services.validator import ValidationResult
from app.watchers.base import SourcePost


@pytest.mark.anyio
async def test_hosted_pipeline_queues_without_waiting_for_validator(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue.db")
    import app.services.pipeline as pipeline

    monkeypatch.setattr(pipeline, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(pipeline, "TELEGRAM_CHAT_IDS", ["chat-1"])
    called = False

    async def validator(_: str) -> ValidationResult:
        nonlocal called
        called = True
        return ValidationResult(status="valid")

    post = SourcePost(
        source="threads:@tester",
        text="https://claude.ai/referral/queued-code",
        url="https://threads.net/example",
    )
    results = await process_post(
        post,
        validator=validator,
        defer_validation=True,
    )

    assert called is False
    assert results[0][1].status == "pending"
    due = await database.get_due_validation_referrals()
    assert [row["referral_code"] for row in due] == ["queued-code"]
    deliveries = await database.get_due_telegram_deliveries()
    assert [row["referral_code"] for row in deliveries] == ["queued-code"]


@pytest.mark.anyio
async def test_blocked_validation_stops_without_suppressing_telegram(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "blocked.db")
    monkeypatch.setattr(retry_queue, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(retry_queue, "TELEGRAM_CHAT_IDS", ["chat-1"])
    monkeypatch.setattr(retry_queue, "VALIDATION_MAX_ATTEMPTS", 1)

    candidate = ReferralCandidate(
        referral_url="https://claude.ai/referral/blocked-code",
        referral_code="blocked-code",
        source="x:@tester",
    )
    await database.init_db()
    await database.save_referral(candidate)
    await database.ensure_telegram_deliveries("blocked-code", ["chat-1"])

    async def blocked(_: str) -> ValidationResult:
        return ValidationResult(status="blocked", method="direct")

    monkeypatch.setattr(retry_queue, "validate_referral", blocked)
    row = (await database.get_due_validation_referrals())[0]
    await retry_queue.validate_queued_referral(row)

    stored = await database.get_referral_by_code("blocked-code")
    assert stored is not None
    assert stored["status"] == "blocked"
    assert stored["validation_attempts"] == 1
    assert stored["next_validation_at"] is None
    assert await database.get_due_validation_referrals() == []
    assert len(await database.get_due_telegram_deliveries()) == 1


@pytest.mark.anyio
async def test_valid_referral_is_delivered_once_per_destination(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "valid.db")
    monkeypatch.setattr(retry_queue, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(retry_queue, "TELEGRAM_CHAT_IDS", ["chat-1", "chat-2"])

    candidate = ReferralCandidate(
        referral_url="https://claude.ai/referral/valid-code",
        referral_code="valid-code",
        source="youtube:test",
    )
    await database.init_db()
    await database.save_referral(candidate)

    async def valid(_: str) -> ValidationResult:
        return ValidationResult(
            status="valid",
            campaign="claude_code_guest_pass_a47c",
            method="direct",
        )

    sent_to: list[str] = []

    async def send(_token: str, chat_id: str, _text: str, **_kwargs) -> None:
        sent_to.append(chat_id)

    monkeypatch.setattr(retry_queue, "validate_referral", valid)
    monkeypatch.setattr(retry_queue, "send_telegram_message", send)

    row = (await database.get_due_validation_referrals())[0]
    await retry_queue.validate_queued_referral(row)
    deliveries = await database.get_due_telegram_deliveries()
    assert len(deliveries) == 2

    for delivery in deliveries:
        assert await retry_queue.deliver_queued_telegram(delivery) is True

    assert sorted(sent_to) == ["chat-1", "chat-2"]
    assert await database.get_due_telegram_deliveries() == []

    async with aiosqlite.connect(database.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, attempts FROM telegram_deliveries ORDER BY chat_id"
        )
        assert await cursor.fetchall() == [("sent", 1), ("sent", 1)]


@pytest.mark.anyio
async def test_failed_telegram_delivery_remains_retryable(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "telegram-retry.db")
    monkeypatch.setattr(retry_queue, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(retry_queue, "TELEGRAM_CHAT_IDS", ["chat-1"])
    monkeypatch.setattr(retry_queue, "TELEGRAM_RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(retry_queue, "TELEGRAM_RETRY_MAX_SECONDS", 0)

    candidate = ReferralCandidate(
        referral_url="https://claude.ai/referral/retry-code",
        referral_code="retry-code",
        source="web:exa",
    )
    await database.init_db()
    await database.save_referral(candidate)
    await database.record_validation_attempt("retry-code", "valid")
    await database.ensure_telegram_deliveries("retry-code", ["chat-1"])

    async def fail(*_args) -> None:
        raise TimeoutError("temporary failure")

    monkeypatch.setattr(retry_queue, "send_telegram_message", fail)
    delivery = (await database.get_due_telegram_deliveries())[0]
    assert await retry_queue.deliver_queued_telegram(delivery) is False
    assert len(await database.get_due_telegram_deliveries()) == 1


@pytest.mark.anyio
async def test_authorized_telegram_feedback_is_saved_and_message_is_updated(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "feedback.db")
    monkeypatch.setattr(retry_queue, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(retry_queue, "TELEGRAM_CHAT_IDS", ["chat-1"])

    candidate = ReferralCandidate(
        referral_url="https://claude.ai/referral/feedback-code",
        referral_code="feedback-code",
        source="x:@tester",
    )
    await database.init_db()
    await database.save_referral(candidate)

    answers: list[str] = []
    edits: list[str] = []

    async def answer(_token: str, _callback_id: str, text: str) -> None:
        answers.append(text)

    async def edit(
        _token: str,
        _chat_id: str,
        _message_id: int,
        text: str,
        **_kwargs,
    ) -> None:
        edits.append(text)

    monkeypatch.setattr(retry_queue, "answer_telegram_callback", answer)
    monkeypatch.setattr(retry_queue, "edit_telegram_message", edit)

    await retry_queue._handle_telegram_feedback(
        {
            "id": "callback-1",
            "data": "referral_feedback:worked:feedback-code",
            "from": {"username": "operator"},
            "message": {
                "message_id": 10,
                "text": "NEW CLAUDE REFERRAL - OPEN NOW",
                "chat": {"id": "chat-1"},
            },
        }
    )

    stored = await database.get_referral_by_code("feedback-code")
    assert stored is not None
    assert stored["feedback_status"] == "worked"
    assert stored["feedback_by"] == "operator"
    assert answers == ["Worked"]
    assert edits and "Claim result: WORKED" in edits[0]
