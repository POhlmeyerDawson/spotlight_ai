-- VC Brain initial schema. Owner: A.
-- Append-only event log. The append-only property is enforced by the DB (see triggers
-- at the bottom), not by convention — convention does not survive hour 19.

create extension if not exists "uuid-ossp";

-- pgvector is present on Supabase but not on a stock Postgres. No Memory table
-- uses a vector column, so guard it: create it where available (Supabase), skip
-- it cleanly everywhere else so the migration still applies from a clean DB.
do $$
begin
    if exists (select 1 from pg_available_extensions where name = 'vector') then
        create extension if not exists vector;
    end if;
end $$;

create table if not exists entities (
    entity_id   uuid primary key default uuid_generate_v4(),
    display_name text not null,
    -- normalized/transliterated form used for fuzzy matching (Type 6 depends on this)
    name_normalized text not null,
    created_at  timestamptz not null default now()
);

create table if not exists entity_aliases (
    alias_id    uuid primary key default uuid_generate_v4(),
    entity_id   uuid not null references entities(entity_id),
    kind        text not null,          -- 'email' | 'url' | 'handle' | 'name'
    value       text not null,
    source      text not null,
    unique (kind, value)
);

create table if not exists companies (
    company_id  uuid primary key default uuid_generate_v4(),
    name        text not null,
    founder_entity_ids uuid[] not null default '{}',
    archetype   int,                    -- 1..6, seed data only
    created_at  timestamptz not null default now()
);

create table if not exists events (
    event_id        uuid primary key default uuid_generate_v4(),
    entity_id       uuid references entities(entity_id),
    company_id      uuid references companies(company_id),
    kind            text not null,
    source          text not null,
    source_url      text,
    observed_at     timestamptz not null,   -- when the world produced it
    ingested_at     timestamptz not null default now(),
    payload         jsonb not null default '{}',
    evidence_span   text,
    confidence      real not null default 1.0 check (confidence between 0 and 1),
    integrity_flags text[] not null default '{}'
);

-- Every read path is as_of-scoped. These two indexes are the read path.
create index if not exists idx_events_entity_observed on events (entity_id, observed_at);
create index if not exists idx_events_company_observed on events (company_id, observed_at);
create index if not exists idx_events_kind on events (kind);

-- Entity merge decisions, including the AMBIGUOUS ones we refuse to guess on.
create table if not exists merges (
    merge_id    uuid primary key default uuid_generate_v4(),
    entity_a    uuid not null references entities(entity_id),
    entity_b    uuid not null references entities(entity_id),
    status      text not null check (status in ('merged', 'ambiguous', 'rejected')),
    score       real not null,
    rationale   text not null,
    decided_at  timestamptz not null default now()
);

-- Append-only enforcement.
create or replace function reject_mutation() returns trigger as $$
begin
    raise exception 'events is append-only: corrections are new events, not updates';
end;
$$ language plpgsql;

drop trigger if exists events_no_update on events;
create trigger events_no_update before update or delete on events
    for each row execute function reject_mutation();

-- Security. Supabase auto-exposes public tables through its Data API; founder
-- Memory must never be readable by an unauthenticated browser.
--
-- Enabling RLS is portable across every Postgres. With RLS on and NO policy
-- defined, unprivileged roles read nothing; the table OWNER still bypasses RLS
-- (we do NOT use FORCE ROW LEVEL SECURITY), so a direct DATABASE_URL connection
-- as the owning role — Supabase's direct-connection `postgres` user, or the
-- role that ran this migration on a plain Postgres — keeps full read/write.
-- memory/pg_store.py connects that way, so the backend is unaffected; only the
-- Data API's anon/authenticated roles are shut out.
alter table events         enable row level security;
alter table entities       enable row level security;
alter table entity_aliases enable row level security;
alter table companies      enable row level security;
alter table merges         enable row level security;

-- Belt and braces: revoke any default Data API grants. Guarded so a vanilla or
-- local Postgres (which has no `anon`/`authenticated` roles) still migrates
-- cleanly — on such a database this block is a no-op, on Supabase it revokes.
do $$
declare
    role_name text;
begin
    foreach role_name in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format(
                'revoke all on events, entities, entity_aliases, companies, merges from %I',
                role_name
            );
        end if;
    end loop;
end $$;
