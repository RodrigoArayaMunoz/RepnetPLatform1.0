create table if not exists public.refax_global_connection (
  id smallint primary key default 1 check (id = 1),
  is_active boolean not null default false,
  access_token text,
  obtained_at timestamptz,
  refresh_at timestamptz,
  expires_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.refax_global_connection is
  'Estado privado del token global de REFAX. Acceso exclusivo mediante service_role.';

alter table public.refax_global_connection enable row level security;

revoke all on table public.refax_global_connection from anon, authenticated;
grant all on table public.refax_global_connection to service_role;

create or replace function public.set_refax_global_connection_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_refax_global_connection_updated_at
  on public.refax_global_connection;

create trigger set_refax_global_connection_updated_at
before update on public.refax_global_connection
for each row
execute function public.set_refax_global_connection_updated_at();
