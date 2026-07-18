-- VC Brain initial schema. Owner: A.
-- Append-only event log. The append-only property is enforced by the DB (see triggers
-- at the bottom), not by convention — convention does not survive hour 19.
-- Applied by scripts/migrate.py; idempotent, so it is safe to re-run.

create extension if not exists "uuid-ossp";

-- pgvector is optional: Memory currently has no vector column, and clean stock
-- Postgres installations commonly do not provide the extension.
do $$
begin
    if exists (select 1 from pg_available_extensions where name = 'vector') then
        create extension if not exists vector;
    end if;
end $$;

create table if not exists entities (
    entity_id   uuid primary key default uuid_generate_v4(),
    display_name text not null,
    name_normalized text not null,
    created_at  timestamptz not null default now()
);

create table if not exists entity_aliases (
    alias_id    uuid primary key default uuid_generate_v4(),
    entity_id   uuid not null references entities(entity_id),
    kind        text not null,
    value       text not null,
    source      text not null,
    unique (kind, value)
);

create table if not exists companies (
    company_id  uuid primary key default uuid_generate_v4(),
    name        text not null,
    founder_entity_ids uuid[] not null default '{}',
    archetype   int,
    created_at  timestamptz not null default now()
);

create table if not exists events (
    event_id        uuid primary key default uuid_generate_v4(),
    entity_id       uuid references entities(entity_id),
    company_id      uuid references companies(company_id),
    kind            text not null,
    source           text not null,
    source_url       text,
    observed_at     timestamptz not null,
    ingested_at     timestamptz not null default now(),
    payload         jsonb not null default '{}',
    evidence_span   text,
    confidence      real not null default 1.0 check (confidence between 0 and 1),
    integrity_flags text[] not null default '{}'
);

create index if not exists idx_events_entity_observed on events (entity_id, observed_at);
create index if not exists idx_events_company_observed on events (company_id, observed_at);
create index if not exists idx_events_kind on events (kind);

create table if not exists merges (
    merge_id    uuid primary key default uuid_generate_v4(),
    entity_a    uuid not null references entities(entity_id),
    entity_b    uuid not null references entities(entity_id),
    status      text not null check (status in ('merged', 'ambiguous', 'rejected')),
    score       real not null,
    rationale   text not null,
    decided_at  timestamptz not null default now()
);

create or replace function reject_mutation() returns trigger as $$
begin
    raise exception 'events is append-only: corrections are new events, not updates';
end;
$$ language plpgsql;

drop trigger if exists events_no_update on events;
create trigger events_no_update before update or delete on events
    for each row execute function reject_mutation();

alter table events         enable row level security;
alter table entities       enable row level security;
alter table entity_aliases enable row level security;
alter table companies      enable row level security;
alter table merges         enable row level security;

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
