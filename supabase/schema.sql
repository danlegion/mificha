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

-- Prototipo: RLS desactivado. La ANON KEY vive solo en el servidor (variables
-- de entorno de Render), nunca llega al navegador. Para producción, activar
-- RLS con políticas por usuario.
alter table intakes   disable row level security;
alter table users      disable row level security;
alter table documents  disable row level security;
alter table shares     disable row level security;

-- ---------------------------------------------------------------------------
-- Storage: bucket "originales" para los PDFs originales.
-- La app usa la ANON KEY desde el servidor (Render); los archivos se nombran
-- con el intake_id (UUID imposible de adivinar). Idempotente.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('originales', 'originales', false)
on conflict (id) do nothing;

drop policy if exists "anon insert originales" on storage.objects;
create policy "anon insert originales"
on storage.objects for insert to anon
with check (bucket_id = 'originales');

drop policy if exists "anon select originales" on storage.objects;
create policy "anon select originales"
on storage.objects for select to anon
using (bucket_id = 'originales');

drop policy if exists "anon delete originales" on storage.objects;
create policy "anon delete originales"
on storage.objects for delete to anon
using (bucket_id = 'originales');
