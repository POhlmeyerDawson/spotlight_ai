-- Outbound cold-reach drafts and the suppression list. Owner: B. DIFFERENTIATOR §6.
-- Applied by scripts/migrate.py; idempotent, so it is safe to re-run.
--
-- WHY THESE ARE NOT EVENTS. Same reasoning as 002. `events` is append-only by trigger and
-- every read of it is as_of-scoped on `observed_at` — "when the world produced it". A
-- draft's status is MUTABLE workflow state (queued -> approved/rejected by a human), and a
-- suppression is a standing instruction, not an observation about the world. Writing either
-- into the event log would mean stamping an observed_at of "when a reviewer clicked a
-- button", which is not a fact about a founder and would corrupt every as_of query.
--
-- WHAT IS NOT HERE. No `sent_at`, no provider message id, no delivery status. Nothing in
-- this feature sends mail: approval marks a draft sendable by a human and stops. A column
-- for a send would be a column something eventually fills in automatically.

create table if not exists outbound_drafts (
    draft_id         uuid primary key default uuid_generate_v4(),
    -- Intentionally NOT a foreign key to companies. The reference is recorded for the
    -- audit trail, and a draft's record of "we wrote to this company on this date" must
    -- survive the company row being rebuilt, which reseeding does routinely.
    company_id       text not null,
    company_name     text,
    recipient_name   text,
    -- Null is the normal case: the store holds public build artifacts, not contact
    -- details. The reviewer supplies the address at send time. A generated guess at
    -- someone's email address is exactly the fabrication this feature refuses.
    recipient_email  text,
    -- `rejected_unverifiable` is reached with NO human in the loop, when a generated
    -- draft failed grounding. It is recorded and then excluded from every queue read: an
    -- unverifiable draft is not a draft with a warning on it, it is one that must never
    -- reach a reviewer who might approve it anyway.
    status           text not null check (status in
                        ('queued', 'approved', 'rejected', 'rejected_unverifiable')),
    subject          text,
    body             text,
    -- JSON arrays/objects as text, NOT jsonb — see 002. Everything above memory/db.py is
    -- written in the SQLite dialect and translated centrally, so a column needing a
    -- psycopg-specific Jsonb() wrapper would put a backend branch in the storage layer.
    --
    -- `citations` is the resolved trace: one row per cited event with its real stored
    -- source_url. The URL is written HERE, by code, from the event — it never passes
    -- through a model in either direction.
    citations        text not null default '[]',
    -- The full computed eligibility verdict at the moment of drafting, check by check.
    -- Snapshotted rather than recomputed on read, because the reviewer has to see what
    -- the system believed when it wrote the mail, not what it believes now.
    eligibility      text not null default '{}',
    rejection_reason text,
    as_of            timestamptz not null,
    created_at       timestamptz not null default now(),
    decided_at       timestamptz,
    decided_by       text
);

create index if not exists idx_outbound_drafts_status on outbound_drafts (status);
create index if not exists idx_outbound_drafts_company on outbound_drafts (company_id);

-- Standing "do not contact". There is deliberately no delete path in the module or the
-- router: someone who asked not to be contacted does not get un-asked by a later call.
create table if not exists outbound_suppression (
    suppression_id uuid primary key default uuid_generate_v4(),
    company_id     text,
    email          text,
    reason         text not null,
    -- 'opt_out' is the recipient's own one-touch request; 'manual' is the fund's choice.
    -- Both block identically — the distinction exists so the record says who decided.
    source         text not null check (source in ('manual', 'opt_out')),
    added_at       timestamptz not null default now()
);

create index if not exists idx_outbound_suppression_company on outbound_suppression (company_id);
create index if not exists idx_outbound_suppression_email on outbound_suppression (email);

alter table outbound_drafts      enable row level security;
alter table outbound_suppression enable row level security;

-- Same posture as 001/002: the API reaches Postgres with the service credential, and the
-- anon/authenticated roles have no business reading unsent drafts about named people.
do $$
declare
    role_name text;
begin
    foreach role_name in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format(
                'revoke all on outbound_drafts, outbound_suppression from %I', role_name
            );
        end if;
    end loop;
end $$;
