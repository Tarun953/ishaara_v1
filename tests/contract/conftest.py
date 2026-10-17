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
from ishaara.settings import env_files


def _local_env_value(name: str) -> str | None:
    """Read a variable from the local env files (.env overlaid by .env.local)."""
    merged: dict[str, str | None] = {}
    for env_file in env_files("local"):
        merged.update(dotenv_values(env_file))
    return merged.get(name)


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
        real_project = os.environ.get("FIRESTORE_TEST_PROJECT")
        if not emulator_host and not real_project:
            pytest.skip(
                "Set FIRESTORE_EMULATOR_HOST (emulator) or FIRESTORE_TEST_PROJECT (a real "
                "project, using throwaway collections) to run the Firestore contract tests"
            )
        from google.cloud import firestore

        from ishaara.clients.firestore_repository import (
            FirestoreUsageRepository,
            FirestoreVideoRepository,
        )

        if emulator_host:
            project = "ishaara-contract-tests"
            httpx.delete(
                f"http://{emulator_host}/emulator/v1/projects/{project}/databases/(default)/documents"
            )
            prefix = ""
        else:
            # A real database: use collections no other data can share, and delete them after.
            project = str(real_project)
            prefix = f"contract_test_{uuid.uuid4().hex[:8]}_"
        client = firestore.AsyncClient(project=project)
        try:
            yield Backend(
                FirestoreVideoRepository(client, clock, prefix),
                FirestoreUsageRepository(client, clock, prefix),
                clock,
            )
        finally:
            if prefix:
                for name in ("videos", "users"):
                    await client.recursive_delete(client.collection(f"{prefix}{name}"))
    elif request.param == "supabase":
        db_url = os.environ.get("SUPABASE_TEST_DB_URL") or _local_env_value("SUPABASE_TEST_DB_URL")
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
