"""Supabase (Postgres) implementations of the storage ports.

Uses a direct database connection (``SUPABASE_DB_URL``) with ``psycopg``. The tables are
defined in ``supabase/schema.sql``. Server-side prepared statements are disabled so the
adapter also works behind Supabase's connection pooler.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from ishaara.clients.repositories import DailyUsageField
from ishaara.clients.serialization import to_jsonable
from ishaara.clock import Clock, utc_now
from ishaara.models.api_models import VideoSummary
from ishaara.models.domain import (
    NarrationCue,
    PipelineStage,
    VideoRecord,
    failed_update,
    lease_update,
    progress_update,
    ready_update,
    restart_update,
)


class SupabaseDatabase:
    """A lazily opened connection pool shared by the Supabase repositories.

    Args:
        db_url: Postgres connection string (session pooler or direct connection).
        schema: Optional schema put first on the ``search_path`` (used by tests).
    """

    def __init__(self, db_url: str, schema: str | None = None) -> None:
        """Create the pool without opening it."""
        self._schema = schema
        self._open_lock = asyncio.Lock()
        self._pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            db_url,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"prepare_threshold": None, "row_factory": dict_row},
            configure=self._configure,
        )

    async def _configure(self, connection: psycopg.AsyncConnection[Any]) -> None:
        """Apply the search path on each new connection."""
        if self._schema:
            await connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(self._schema))
            )
            await connection.commit()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """Yield a pooled connection inside a transaction, committed on success."""
        async with self._open_lock:
            await self._pool.open()
        async with self._pool.connection() as connection:
            yield connection

    async def close(self) -> None:
        """Close the pool."""
        await self._pool.close()


class SupabaseVideoRepository:
    """``VideoRepository`` backed by Postgres.

    Args:
        database: The shared connection pool.
        clock: Source of the current time.
    """

    def __init__(self, database: SupabaseDatabase, clock: Clock = utc_now) -> None:
        """Store the database and clock."""
        self._database = database
        self._clock = clock

    async def get(self, video_key: str) -> VideoRecord | None:
        """Return the record, or ``None``."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT record FROM videos WHERE video_key = %s", (video_key,)
            )
            row = await cursor.fetchone()
        return VideoRecord.model_validate(row["record"]) if row else None

    async def create_if_absent(self, record: VideoRecord) -> tuple[VideoRecord, bool]:
        """Insert the record unless the key exists."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO videos (video_key, record) VALUES (%s, %s) "
                "ON CONFLICT (video_key) DO NOTHING RETURNING video_key",
                (record.video_key, Jsonb(record.model_dump(mode="json"))),
            )
            if await cursor.fetchone():
                return record, True
            cursor = await connection.execute(
                "SELECT record FROM videos WHERE video_key = %s", (record.video_key,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError(f"Video {record.video_key} vanished during create_if_absent")
        return VideoRecord.model_validate(row["record"]), False

    async def claim_restart(self, video_key: str, lease_expires_at: datetime) -> VideoRecord | None:
        """Lock the row, and reset it to ``queued`` if it is restartable."""
        now = self._clock()
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT record FROM videos WHERE video_key = %s FOR UPDATE", (video_key,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            record = VideoRecord.model_validate(row["record"])
            if not record.is_restartable(now):
                return None
            await connection.execute(
                "UPDATE videos SET record = record || %s WHERE video_key = %s",
                (Jsonb(to_jsonable(restart_update(now, lease_expires_at))), video_key),
            )
        return record.restarted(now, lease_expires_at)

    async def _update_fields(self, video_key: str, fields: dict[str, Any]) -> None:
        """Merge ``fields`` into the stored record; a missing record is ignored."""
        async with self._database.connection() as connection:
            await connection.execute(
                "UPDATE videos SET record = record || %s WHERE video_key = %s",
                (Jsonb(to_jsonable(fields)), video_key),
            )

    async def update_progress(
        self, video_key: str, stage: PipelineStage, progress_percent: int
    ) -> None:
        """Record progress."""
        await self._update_fields(
            video_key, progress_update(stage, progress_percent, self._clock())
        )

    async def renew_lease(self, video_key: str, lease_expires_at: datetime) -> None:
        """Extend the lease."""
        await self._update_fields(video_key, lease_update(lease_expires_at, self._clock()))

    async def mark_ready(
        self,
        video_key: str,
        cues: list[NarrationCue],
        model_versions: dict[str, str],
        token_usage: dict[str, int],
        transcript_blob_path: str | None,
    ) -> None:
        """Store the result."""
        await self._update_fields(
            video_key,
            ready_update(cues, model_versions, token_usage, transcript_blob_path, self._clock()),
        )

    async def mark_failed(self, video_key: str, error_code: str, error_message: str) -> None:
        """Store the failure."""
        await self._update_fields(
            video_key, failed_update(error_code, error_message, self._clock())
        )

    async def record_submission(
        self, uid: str, video_key: str, title: str, submitted_at: datetime
    ) -> None:
        """Add or refresh a history row."""
        async with self._database.connection() as connection:
            await connection.execute(
                "INSERT INTO submissions (uid, video_key, title, submitted_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (uid, video_key) "
                "DO UPDATE SET title = EXCLUDED.title, submitted_at = EXCLUDED.submitted_at",
                (uid, video_key, title, submitted_at),
            )

    async def list_for_user(self, uid: str, limit: int) -> list[VideoSummary]:
        """Return the user's history, newest first."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT video_key, title, submitted_at FROM submissions "
                "WHERE uid = %s ORDER BY submitted_at DESC LIMIT %s",
                (uid, limit),
            )
            rows = await cursor.fetchall()
        return [VideoSummary.model_validate(row) for row in rows]


class SupabaseUsageRepository:
    """``UsageRepository`` backed by one atomic upsert per use.

    Args:
        database: The shared connection pool.
        clock: Source of the current time; the UTC date selects the counter row.
    """

    def __init__(self, database: SupabaseDatabase, clock: Clock = utc_now) -> None:
        """Store the database and clock."""
        self._database = database
        self._clock = clock

    async def increment_and_check(self, uid: str, field: DailyUsageField, limit: int) -> bool:
        """Count one use unless today's limit is reached."""
        if limit <= 0:
            return False
        column = sql.Identifier(field.value)
        statement = sql.SQL(
            "INSERT INTO daily_usage (uid, day, {column}) VALUES (%s, %s, 1) "
            "ON CONFLICT (uid, day) DO UPDATE SET {column} = daily_usage.{column} + 1 "
            "WHERE daily_usage.{column} < %s RETURNING {column}"
        ).format(column=column)
        async with self._database.connection() as connection:
            cursor = await connection.execute(statement, (uid, self._clock().date(), limit))
            return await cursor.fetchone() is not None
