-- VC Brain initial schema. Owner: A.
-- Append-only event log. The append-only property is enforced by the DB (see triggers
-- at the bottom), not by convention — convention does not survive hour 19.
-- Applied by scripts/migrate.py; idempotent, so it is safe to re-run.

-- pgvector is not used yet and a hosted role may not be allowed to create extensions.
-- Not having it must never fail the migration.
do $$
begin
    create extension if not exists vector;
exception when others then
    raise notice 'pgvector unavailable, continuing without it';
end
$$;

create table if not exists entities (
    entity_id   uuid primary key default gen_random_uuid(),
    display_name text not null,
    -- normalized/transliterated form used for fuzzy matching (Type 6 depends on this)
    name_normalized text not null,
    created_at  timestamptz not null default now()
);

create table if not exists entity_aliases (
    alias_id    uuid primary key default gen_random_uuid(),
    entity_id   uuid not null references entities(entity_id),
    kind        text not null,          -- 'email' | 'url' | 'handle' | 'name'
    value       text not null,
    source      text not null,
    unique (kind, value)
);

create table if not exists companies (
    company_id  uuid primary key default gen_random_uuid(),
    name        text not null,
    -- jsonb, not uuid[]: the SQLite backend stores a JSON array here and both backends
    -- must round-trip the identical representation.
    founder_entity_ids jsonb not null default '[]',
    archetype   int,                    -- 1..6, seed data only
    created_at  timestamptz not null default now()
);

create table if not exists events (
    event_id        uuid primary key default gen_random_uuid(),
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
    integrity_flags jsonb not null default '[]'
);

-- Every read path is as_of-scoped. These two indexes are the read path.
create index if not exists idx_events_entity_observed on events (entity_id, observed_at);
create index if not exists idx_events_company_observed on events (company_id, observed_at);
create index if not exists idx_events_kind on events (kind);

-- Entity merge decisions, including the AMBIGUOUS ones we refuse to guess on.
create table if not exists merges (
    merge_id    uuid primary key default gen_random_uuid(),
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
