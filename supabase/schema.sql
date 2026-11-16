-- ============================================================================
-- MiFicha — schema for Supabase (Postgres)
-- Run once in the Supabase project's SQL Editor.
-- Mirrors the SQLite DAL models (app/db.py) so the backend uses
-- Supabase when SUPABASE_URL and SUPABASE_ANON_KEY are configured.
-- ============================================================================

create table if not exists intakes (
  id               text primary key,
  status           text default 'processing',
  detail           text,
  doc_type_hint    text,
  original_key     text,
  explanation_json text,
  corrections_json text default '[]',
  owner_user_id    text,
  created_at       timestamptz default now(),
  updated_at       timestamptz default now()
);

create table if not exists users (
  id         text primary key,
  email      text unique not null,
  telefono   text,
  created_at timestamptz default now()
);

create table if not exists documents (
  id               text primary key,
  user_id          text not null,
  intake_id        text not null,
  original_key     text,
  explanation_json text,
  created_at       timestamptz default now()
);

create table if not exists shares (
  id            text primary key,
  intake_id     text not null,
  token         text unique not null,
  snapshot_json text not null,
  expires_at    timestamptz not null,
  revoked       boolean default false,
  created_at    timestamptz default now()
);

-- Family members: profiles owned by one account, not separate logins.
create table if not exists members (
  id            text primary key,
  owner_user_id text not null,
  name          text not null,
  created_at    timestamptz default now()
);

-- Backfill for databases created before family members existed.
alter table intakes add column if not exists member_id text;
alter table documents add column if not exists member_id text;

-- Security: RLS ENABLED on all tables, with no policies for anon or
-- authenticated (deny by default). The backend accesses them with the
-- SUPABASE_SERVICE_ROLE_KEY (bypasses RLS) only from the server; the anon key
-- stays reserved for Supabase Auth (magic links) and can no longer read or
-- write these tables even if it leaked.
alter table intakes   enable row level security;
alter table users      enable row level security;
alter table documents  enable row level security;
alter table shares     enable row level security;
alter table members    enable row level security;

-- ---------------------------------------------------------------------------
-- Storage: bucket "originales" for the original PDFs.
-- The backend uses SUPABASE_SERVICE_ROLE_KEY (bypasses RLS), so the bucket
-- needs no anon policies: with no policies, anonymous access is
-- denied by default. Idempotent.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('originales', 'originales', false)
on conflict (id) do nothing;

drop policy if exists "anon insert originales" on storage.objects;
drop policy if exists "anon select originales" on storage.objects;
drop policy if exists "anon delete originales" on storage.objects;
