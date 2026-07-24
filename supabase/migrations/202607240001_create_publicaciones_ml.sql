create table if not exists public.publicaciones_ml (
  seller_id bigint not null,
  mlc text not null,
  sku text,
  titulo text not null default '',
  fecha_creacion date,
  sync_run_id uuid not null,
  sincronizado_at timestamptz not null default now(),
  primary key (seller_id, mlc)
);

create index if not exists publicaciones_ml_seller_fecha_idx
  on public.publicaciones_ml (seller_id, fecha_creacion);

create index if not exists publicaciones_ml_sku_idx
  on public.publicaciones_ml (seller_id, sku)
  where sku is not null;

comment on table public.publicaciones_ml is
  'Publicaciones de Mercado Libre sincronizadas mediante scan y multiget.';

comment on column public.publicaciones_ml.fecha_creacion is
  'Primeros 10 caracteres de date_created, almacenados como fecha YYYY-MM-DD.';

alter table public.publicaciones_ml enable row level security;

-- No se crean políticas para anon/authenticated. El backend escribe y lee
-- exclusivamente con SUPABASE_SERVICE_ROLE_KEY, que no debe exponerse al frontend.
