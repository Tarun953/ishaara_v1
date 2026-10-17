-- Ishaara storage schema for Supabase (plain Postgres). Run once in the SQL editor.
-- The backend connects with a direct database login, so row level security is enabled
-- with no policies: this blocks the public REST API (anon key) from reading these tables.

create table if not exists videos (
    video_key text primary key,
    record jsonb not null
);

create table if not exists submissions (
    uid text not null,
    video_key text not null,
    title text not null,
    submitted_at timestamptz not null,
    primary key (uid, video_key)
);

create index if not exists submissions_user_recent on submissions (uid, submitted_at desc);

create table if not exists daily_usage (
    uid text not null,
    day date not null,
    videos_submitted integer not null default 0,
    questions_asked integer not null default 0,
    primary key (uid, day)
);

alter table videos enable row level security;
alter table submissions enable row level security;
alter table daily_usage enable row level security;
