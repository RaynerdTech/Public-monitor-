from pathlib import Path

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
                validation_message TEXT
            )
            """
        )

        # Keep older local databases compatible with newer builds.
        columns = await _column_names(db, "referrals")
        if "campaign" not in columns:
            await db.execute("ALTER TABLE referrals ADD COLUMN campaign TEXT")
        if "validation_message" not in columns:
            await db.execute("ALTER TABLE referrals ADD COLUMN validation_message TEXT")

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


async def get_referral_by_code(referral_code: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, referral_url, referral_code, source, source_url,
                   post_created_at, detected_at, status, campaign, validation_message
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
                   post_created_at, detected_at, status, campaign, validation_message
            FROM referrals
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
