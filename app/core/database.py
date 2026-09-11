from pathlib import Path
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.config import DATABASE_PATH
from app.core.models import ReferralCandidate


DB_PATH = Path(DATABASE_PATH)


async def _column_names(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referral_url TEXT NOT NULL UNIQUE,
                referral_code TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                post_created_at TEXT,
                detected_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                campaign TEXT,
                validation_message TEXT,
                validation_attempts INTEGER NOT NULL DEFAULT 0,
                next_validation_at TEXT,
                last_validated_at TEXT
            )
            """
        )

        # Keep older local databases compatible with newer builds.
        columns = await _column_names(db, "referrals")
        if "campaign" not in columns:
            await db.execute("ALTER TABLE referrals ADD COLUMN campaign TEXT")
        if "validation_message" not in columns:
            await db.execute("ALTER TABLE referrals ADD COLUMN validation_message TEXT")
        if "validation_attempts" not in columns:
            await db.execute(
                "ALTER TABLE referrals ADD COLUMN validation_attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "next_validation_at" not in columns:
            await db.execute("ALTER TABLE referrals ADD COLUMN next_validation_at TEXT")
        if "last_validated_at" not in columns:
            await db.execute("ALTER TABLE referrals ADD COLUMN last_validated_at TEXT")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referral_code TEXT NOT NULL,
                referral_url TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                post_created_at TEXT,
                detected_at TEXT NOT NULL,
                UNIQUE(referral_code, source, source_url)
            )
            """
        )

        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_code_unique "
                "ON referrals(referral_code)"
            )
        except aiosqlite.IntegrityError:
            # Older local databases may already contain the same code with
            # different query-string variants. New inserts are still guarded
            # by save_referral below.
            pass
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_occurrences_code ON occurrences(referral_code)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_referrals_validation_queue "
            "ON referrals(status, next_validation_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referral_code TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                sent_at TEXT,
                UNIQUE(referral_code, chat_id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_delivery_queue "
            "ON telegram_deliveries(status, next_attempt_at)"
        )
        await db.commit()


async def referral_code_exists(referral_code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM referrals WHERE referral_code = ? LIMIT 1",
            (referral_code,),
        )
        return await cursor.fetchone() is not None


async def save_occurrence(candidate: ReferralCandidate) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO occurrences (
                referral_code,
                referral_url,
                source,
                source_url,
                post_created_at,
                detected_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.referral_code,
                candidate.referral_url,
                candidate.source,
                candidate.source_url,
                candidate.post_created_at.isoformat() if candidate.post_created_at else None,
                candidate.detected_at.isoformat(),
            ),
        )
        await db.commit()


async def save_referral(candidate: ReferralCandidate) -> bool:
    """Save a referral once per code. Returns True only for a new code."""
    await save_occurrence(candidate)

    if await referral_code_exists(candidate.referral_code):
        return False

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO referrals (
                    referral_url,
                    referral_code,
                    source,
                    source_url,
                    post_created_at,
                    detected_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.referral_url,
                    candidate.referral_code,
                    candidate.source,
                    candidate.source_url,
                    candidate.post_created_at.isoformat() if candidate.post_created_at else None,
                    candidate.detected_at.isoformat(),
                ),
            )
            await db.commit()
            return True
    except aiosqlite.IntegrityError:
        return False


async def update_validation(
    referral_code: str,
    status: str,
    campaign: str | None = None,
    validation_message: str | None = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE referrals
            SET status = ?, campaign = ?, validation_message = ?
            WHERE referral_code = ?
            """,
            (status, campaign, validation_message, referral_code),
        )
        await db.commit()


async def record_validation_attempt(
    referral_code: str,
    status: str,
    campaign: str | None = None,
    validation_message: str | None = None,
    *,
    retry_after_seconds: int | None = None,
) -> int:
    """Record an attempt and optionally schedule another validation."""
    now = datetime.now(timezone.utc)
    next_validation_at = (
        (now + timedelta(seconds=retry_after_seconds)).isoformat()
        if retry_after_seconds is not None
        else None
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE referrals
            SET status = ?, campaign = ?, validation_message = ?,
                validation_attempts = validation_attempts + 1,
                last_validated_at = ?, next_validation_at = ?
            WHERE referral_code = ?
            """,
            (
                status,
                campaign,
                validation_message,
                now.isoformat(),
                next_validation_at,
                referral_code,
            ),
        )
        cursor = await db.execute(
            "SELECT validation_attempts FROM referrals WHERE referral_code = ?",
            (referral_code,),
        )
        row = await cursor.fetchone()
        await db.commit()
        return int(row[0]) if row else 0


async def get_due_validation_referrals(limit: int = 20) -> list[dict]:
    """Return new or retryable referrals whose next validation is due."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, referral_url, referral_code, source, source_url,
                   post_created_at, detected_at, status, campaign,
                   validation_message, validation_attempts, next_validation_at,
                   last_validated_at
            FROM referrals
            WHERE status IN ('pending', 'blocked', 'error', 'unknown')
              AND (next_validation_at IS NULL OR next_validation_at <= ?)
            ORDER BY
              CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
              CASE WHEN status = 'pending' THEN detected_at END DESC,
              next_validation_at ASC,
              detected_at ASC
            LIMIT ?
            """,
            (now, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def ensure_telegram_deliveries(
    referral_code: str, chat_ids: list[str]
) -> None:
    """Create one durable delivery record per Telegram destination."""
    if not chat_ids:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """
            INSERT OR IGNORE INTO telegram_deliveries (
                referral_code, chat_id, status
            ) VALUES (?, ?, 'pending')
            """,
            [(referral_code, chat_id) for chat_id in chat_ids],
        )
        await db.commit()


async def get_due_telegram_deliveries(limit: int = 50) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT d.id AS delivery_id, d.referral_code, d.chat_id,
                   d.status AS delivery_status, d.attempts,
                   r.referral_url, r.source, r.source_url,
                   r.post_created_at, r.detected_at, r.campaign
            FROM telegram_deliveries AS d
            JOIN referrals AS r ON r.referral_code = d.referral_code
            WHERE r.status = 'valid'
              AND d.status IN ('pending', 'failed')
              AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
            ORDER BY d.next_attempt_at ASC, d.id ASC
            LIMIT ?
            """,
            (now, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def record_telegram_delivery(
    delivery_id: int,
    *,
    sent: bool,
    error: str | None = None,
    retry_after_seconds: int | None = None,
) -> int:
    now = datetime.now(timezone.utc)
    next_attempt_at = (
        (now + timedelta(seconds=retry_after_seconds)).isoformat()
        if not sent and retry_after_seconds is not None
        else None
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE telegram_deliveries
            SET status = ?, attempts = attempts + 1,
                next_attempt_at = ?, last_error = ?, sent_at = ?
            WHERE id = ?
            """,
            (
                "sent" if sent else "failed",
                next_attempt_at,
                None if sent else error,
                now.isoformat() if sent else None,
                delivery_id,
            ),
        )
        cursor = await db.execute(
            "SELECT attempts FROM telegram_deliveries WHERE id = ?",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        await db.commit()
        return int(row[0]) if row else 0


async def get_referral_by_code(referral_code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, referral_url, referral_code, source, source_url,
                   post_created_at, detected_at, status, campaign, validation_message,
                   validation_attempts, next_validation_at, last_validated_at
            FROM referrals
            WHERE referral_code = ?
            LIMIT 1
            """,
            (referral_code,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_recent_referrals(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, referral_url, referral_code, source, source_url,
                   post_created_at, detected_at, status, campaign, validation_message,
                   validation_attempts, next_validation_at, last_validated_at
            FROM referrals
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
