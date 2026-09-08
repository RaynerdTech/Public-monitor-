import asyncio
from pathlib import Path

from app.core import database
from app.services.pipeline import process_post
from app.services.validator import ValidationResult
from app.watchers.base import SourcePost


def test_pipeline_dedupes_by_referral_code(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")

    async def fake_validator(_: str) -> ValidationResult:
        return ValidationResult(status="valid", campaign="test", is_valid=True)

    async def run():
        first = SourcePost(
            source="test",
            text="https://claude.ai/referral/same-code?s=android",
            url="https://example.com/a",
        )
        second = SourcePost(
            source="test",
            text="https://claude.ai/referral/same-code?s=ios",
            url="https://example.com/b",
        )

        first_results = await process_post(first, validator=fake_validator, alert_valid=False)
        second_results = await process_post(second, validator=fake_validator, alert_valid=False)

        assert first_results[0][2] is True
        assert first_results[0][1].status == "valid"
        assert second_results[0][2] is False
        assert second_results[0][1].status == "duplicate"

    asyncio.run(run())
