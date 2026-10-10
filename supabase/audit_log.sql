-- MiFicha: tabla de auditoría (quién accedió a qué documento y cuándo).
-- Ejecutar una vez en Supabase Dashboard → SQL Editor → Run.
-- Hasta que esta tabla exista, la app registra la advertencia en el log
-- y sigue funcionando (los eventos de auditoría no se guardan en prod).

create table if not exists public.audit_log (
  id text primary key,
  created_at timestamptz not null default now(),
  actor text,            -- 'user:<id>', 'guest', 'share' o 'anon'
  action text not null,  -- upload, view_result, view_original, view_shared,
                         -- share_create, share_revoke, delete
  intake_id text,
  doc_id text,
  ip text,
  detail text            -- JSON opcional con contexto del evento
);

create index if not exists audit_log_action_idx
  on public.audit_log (action);
create index if not exists audit_log_intake_idx
  on public.audit_log (intake_id);
create index if not exists audit_log_created_idx
  on public.audit_log (created_at desc);

-- RLS activado sin políticas públicas: solo el service key (el servidor)
-- puede leer y escribir, igual que las demás tablas de MiFicha.
alter table public.audit_log enable row level security;
