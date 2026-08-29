create table if not exists public.meli_sale_pickings (
  seller_id text not null,
  sale_id text not null,
  status text not null
    check (status in ('in_preparation', 'packed')),
  last_scanned_sku text,
  started_at timestamptz not null default now(),
  packed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (seller_id, sale_id)
);

create index if not exists meli_sale_pickings_seller_status_idx
  on public.meli_sale_pickings (seller_id, status, updated_at desc);

comment on table public.meli_sale_pickings is
  'Estado persistente del flujo de picking y embalaje por venta de Mercado Libre.';

alter table public.meli_sale_pickings enable row level security;

-- La tabla contiene datos operacionales y se lee/escribe exclusivamente
-- desde el backend con SUPABASE_SERVICE_ROLE_KEY.
