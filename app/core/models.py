from datetime import datetime, timezone
from pydantic import BaseModel, Field


class ReferralCandidate(BaseModel):
    referral_url: str
    referral_code: str
    source: str
    source_url: str | None = None
    post_created_at: datetime | None = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReferralRecord(ReferralCandidate):
    id: int | None = None
    status: str = "pending"
    campaign: str | None = None
    validation_message: str | None = None
