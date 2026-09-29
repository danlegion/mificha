-- ============================================================================
-- MiFicha — schema para Supabase (Postgres)
-- Ejecutar una vez en el SQL Editor del proyecto Supabase.
-- Replica los modelos del DAL SQLite (app/db.py) para que el backend use
-- Supabase cuando SUPABASE_URL y SUPABASE_ANON_KEY están configuradas.
-- ============================================================================

create table if not exists intakes (
  id               text primary key,
  guest_token_hash text,
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

-- Seguridad: RLS ACTIVADO en todas las tablas, sin políticas para anon ni
-- authenticated (denegar por defecto). El backend accede con la
-- SUPABASE_SERVICE_ROLE_KEY (salta RLS) solo desde el servidor; la anon key
-- queda reservada a Supabase Auth (magic links) y ya no puede leer ni
-- escribir estas tablas aunque se filtrara.
alter table intakes   enable row level security;
alter table users      enable row level security;
alter table documents  enable row level security;
alter table shares     enable row level security;

-- ---------------------------------------------------------------------------
-- Storage: bucket "originales" para los PDFs originales.
-- El backend usa SUPABASE_SERVICE_ROLE_KEY (salta RLS), así que el bucket no
-- necesita políticas para anon: sin políticas, el acceso anónimo queda
-- denegado por defecto. Idempotente.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('originales', 'originales', false)
on conflict (id) do nothing;

drop policy if exists "anon insert originales" on storage.objects;
drop policy if exists "anon select originales" on storage.objects;
drop policy if exists "anon delete originales" on storage.objects;
