"""Link-first candidate evaluation shared by every search-based watcher.

Historically each watcher applied its freshness window *before* checking whether
the row actually contained a referral URL. That ordering had two production
consequences:

1. A row whose timestamp field we failed to map (``created_at is None``) was
   dropped even when it demonstrably contained ``claude.ai/referral/``.
2. ``referral_results`` was only counted after the freshness gate, so the logs
   reported ``referral_results: 0`` for polls that really had found a referral
   link and then discarded it. The failure was invisible.

This module inverts the order. The referral link is the signal we care about, so
it is extracted first, every rejection records an explicit reason, and freshness
becomes a *staleness ceiling* that is deliberately decoupled from the polling
interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.activity_log import activity
from app.core.extractor import extract_referral_links
from app.watchers.base import SourcePost


# Providers may report a timestamp a little ahead of us; tolerate small skew.
FUTURE_TOLERANCE_MINUTES = 5


@dataclass(frozen=True)
class CandidateVerdict:
    accepted: bool
    reason: str
    referral_links: tuple[str, ...] = ()
    age_seconds: float | None = None

    @property
    def has_referral_link(self) -> bool:
        return bool(self.referral_links)


def evaluate_candidate(
    post: SourcePost | None,
    *,
    max_post_age_minutes: int,
    lookback_minutes: int | None = None,
    now: datetime | None = None,
) -> CandidateVerdict:
    """Decide whether one provider row should reach the referral pipeline.

    ``lookback_minutes`` is only used to label how the post was found (inside the
    polling window vs. indexed late). ``max_post_age_minutes`` is the real gate.
    """
    if post is None:
        return CandidateVerdict(False, "unparsable_row")

    links = tuple(extract_referral_links(post.text))
    if not links:
        return CandidateVerdict(False, "no_referral_link")

    current = now or datetime.now(timezone.utc)
    created_at = post.created_at

    # A referral link with no usable timestamp is still a referral link. The
    # database de-duplicates by referral code, so accepting it cannot spam.
    if created_at is None:
        return CandidateVerdict(True, "accepted_timestamp_unavailable", links)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    age_seconds = (current - created_at).total_seconds()

    if age_seconds < -FUTURE_TOLERANCE_MINUTES * 60:
        return CandidateVerdict(False, "timestamp_in_future", links, age_seconds)

    ceiling = timedelta(minutes=max(1, int(max_post_age_minutes))).total_seconds()
    if age_seconds > ceiling:
        return CandidateVerdict(False, "older_than_max_post_age", links, age_seconds)

    if lookback_minutes is not None and age_seconds > max(1, int(lookback_minutes)) * 60:
        # Still accepted. The platform simply indexed the post after we polled.
        return CandidateVerdict(True, "accepted_indexed_late", links, age_seconds)

    return CandidateVerdict(True, "accepted_recent", links, age_seconds)


@dataclass
class PollDiagnostics:
    """Per-poll counters plus bounded raw-row logging for Render logs."""

    source: str
    sample_limit: int = 3
    raw_results: int = 0
    rows_with_referral_link: int = 0
    accepted: int = 0
    rejected_no_link: int = 0
    rejected_stale: int = 0
    rejected_future: int = 0
    rejected_unparsable: int = 0
    rejected_duplicate: int = 0
    _samples_logged: int = field(default=0, repr=False)
    _newest_age_seconds: float | None = field(default=None, repr=False)
    _oldest_age_seconds: float | None = field(default=None, repr=False)

    @staticmethod
    def _snippet(text: str, limit: int = 240) -> str:
        cleaned = " ".join((text or "").split())
        return cleaned[:limit]

    def record_raw(self, count: int = 1) -> None:
        self.raw_results += count

    def record_duplicate(self, row_id: str) -> None:
        self.rejected_duplicate += 1
        activity(
            "source_row_skipped",
            source=self.source,
            reason="already_seen_this_process",
            row_id=row_id[:120],
        )

    def record(
        self,
        verdict: CandidateVerdict,
        post: SourcePost | None,
        *,
        row_id: str = "",
    ) -> bool:
        """Record one evaluated row and return whether it was accepted."""
        if verdict.age_seconds is not None:
            if self._newest_age_seconds is None or verdict.age_seconds < self._newest_age_seconds:
                self._newest_age_seconds = verdict.age_seconds
            if self._oldest_age_seconds is None or verdict.age_seconds > self._oldest_age_seconds:
                self._oldest_age_seconds = verdict.age_seconds

        if verdict.has_referral_link:
            self.rows_with_referral_link += 1

        if verdict.accepted:
            self.accepted += 1
        elif verdict.reason == "no_referral_link":
            self.rejected_no_link += 1
        elif verdict.reason == "older_than_max_post_age":
            self.rejected_stale += 1
        elif verdict.reason == "timestamp_in_future":
            self.rejected_future += 1
        else:
            self.rejected_unparsable += 1

        # Always shout when a row that really did carry a referral URL is
        # discarded. This is the failure mode that used to be silent.
        if verdict.has_referral_link and not verdict.accepted:
            activity(
                "referral_candidate_rejected",
                source=self.source,
                reason=verdict.reason,
                row_id=row_id[:120],
                source_url=post.url if post else None,
                post_created_at=post.created_at if post else None,
                age_seconds=round(verdict.age_seconds) if verdict.age_seconds is not None else None,
                referral_links=list(verdict.referral_links),
                text_snippet=self._snippet(post.text) if post else None,
                level="WARNING",
            )
            return False

        # Otherwise log a bounded sample so a zero-result poll is explainable.
        if self._samples_logged < self.sample_limit:
            self._samples_logged += 1
            activity(
                "source_row_inspected",
                source=self.source,
                decision="accepted" if verdict.accepted else "rejected",
                reason=verdict.reason,
                row_id=row_id[:120],
                source_url=post.url if post else None,
                post_created_at=post.created_at if post else None,
                age_seconds=round(verdict.age_seconds) if verdict.age_seconds is not None else None,
                has_referral_link=verdict.has_referral_link,
                text_snippet=self._snippet(post.text) if post else None,
            )

        return verdict.accepted

    def as_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "raw_results": self.raw_results,
            "rows_with_referral_link": self.rows_with_referral_link,
            "accepted": self.accepted,
            "rejected_no_link": self.rejected_no_link,
            "rejected_stale": self.rejected_stale,
            "rejected_future": self.rejected_future,
            "rejected_unparsable": self.rejected_unparsable,
            "rejected_duplicate": self.rejected_duplicate,
        }
        # Age of the freshest/stalest row the provider returned. If a provider
        # keeps returning rows that are hours old, the query or the provider
        # index is the problem, not our filtering.
        if self._newest_age_seconds is not None:
            fields["newest_result_age_seconds"] = round(self._newest_age_seconds)
        if self._oldest_age_seconds is not None:
            fields["oldest_result_age_seconds"] = round(self._oldest_age_seconds)
        return fields

    def completed(self, **extra: object) -> None:
        activity(
            "source_poll_completed",
            source=self.source,
            **self.as_fields(),
            **extra,
        )
