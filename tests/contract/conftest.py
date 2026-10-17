"""Fixtures that run the repository contract tests against every available backend."""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import sql

from ishaara.clients.memory_repository import InMemoryUsageRepository, InMemoryVideoRepository
from ishaara.clients.repositories import UsageRepository, VideoRepository
from ishaara.models.domain import NarrationLanguage, VideoRecord, VideoStatus


class FakeClock:
    """A clock tests can move forward."""

    def __init__(self) -> None:
        self.current = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **timedelta_kwargs: float) -> None:
        self.current += timedelta(**timedelta_kwargs)


@dataclass
class Backend:
    videos: VideoRepository
    usage: UsageRepository
    clock: FakeClock


def make_record(
    video_key: str = "dQw4w9WgXcQ_en_v1",
    uid: str = "user-1",
    status: VideoStatus = VideoStatus.QUEUED,
    lease_expires_at: datetime | None = None,
    now: datetime | None = None,
) -> VideoRecord:
    created = now or datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    return VideoRecord(
        video_key=video_key,
        youtube_video_id=video_key.split("_")[0],
        narration_language=NarrationLanguage.ENGLISH,
        pipeline_version=1,
        title="Lecture",
        channel_title="Prof",
        duration_seconds=600,
        status=status,
        lease_expires_at=lease_expires_at,
        created_by_uid=uid,
        created_at=created,
        updated_at=created,
    )


@pytest.fixture(params=["memory", "firestore", "supabase"])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    clock = FakeClock()
    if request.param == "memory":
        yield Backend(InMemoryVideoRepository(clock), InMemoryUsageRepository(clock), clock)
    elif request.param == "firestore":
        emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
        if not emulator_host:
            pytest.skip("Set FIRESTORE_EMULATOR_HOST to run the Firestore contract tests")
        from google.cloud import firestore

        from ishaara.clients.firestore_repository import (
            FirestoreUsageRepository,
            FirestoreVideoRepository,
        )

        project = "ishaara-contract-tests"
        httpx.delete(
            f"http://{emulator_host}/emulator/v1/projects/{project}/databases/(default)/documents"
        )
        client = firestore.AsyncClient(project=project)
        yield Backend(
            FirestoreVideoRepository(client, clock), FirestoreUsageRepository(client, clock), clock
        )
    elif request.param == "supabase":
        db_url = os.environ.get("SUPABASE_TEST_DB_URL") or dotenv_values(".env").get(
            "SUPABASE_TEST_DB_URL"
        )
        if not db_url:
            pytest.skip("Set SUPABASE_TEST_DB_URL to run the Supabase contract tests")
        from ishaara.clients.supabase_repository import (
            SupabaseDatabase,
            SupabaseUsageRepository,
            SupabaseVideoRepository,
        )

        schema = f"ishaara_test_{uuid.uuid4().hex[:8]}"
        schema_sql = (Path(__file__).parents[2] / "supabase" / "schema.sql").read_text()
        # Everything happens inside a throwaway schema that is dropped afterwards.
        with psycopg.connect(db_url, autocommit=True, prepare_threshold=None) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        database = SupabaseDatabase(db_url, schema=schema)
        async with database.connection() as connection:
            await connection.execute(schema_sql)
        try:
            yield Backend(
                SupabaseVideoRepository(database, clock),
                SupabaseUsageRepository(database, clock),
                clock,
            )
        finally:
            await database.close()
            with psycopg.connect(db_url, autocommit=True, prepare_threshold=None) as admin:
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
