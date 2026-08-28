create table if not exists public.meli_notification_events (
  event_key text primary key,
  notification_id text,
  topic text not null,
  resource text not null,
  ml_user_id text not null,
  application_id text not null,
  attempts integer not null default 1,
  sent_at timestamptz,
  received_at timestamptz,
  payload jsonb not null default '{}'::jsonb,
  processing_status text not null default 'queued'
    check (processing_status in ('queued', 'processing', 'processed', 'failed')),
  processing_error text,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists meli_notification_events_status_created_idx
  on public.meli_notification_events (processing_status, created_at);

create table if not exists public.meli_packs (
  pack_id text primary key,
  seller_id text not null,
  shipment_id text,
  status text,
  status_detail text,
  buyer_id text,
  date_created timestamptz,
  last_updated timestamptz,
  raw_data jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now()
);

create table if not exists public.meli_orders (
  order_id text primary key,
  seller_id text not null,
  sale_id text not null,
  pack_id text,
  shipment_id text,
  status text not null default '',
  status_detail text,
  date_created timestamptz,
  date_closed timestamptz,
  last_updated timestamptz,
  total_amount numeric,
  paid_amount numeric,
  currency_id text,
  buyer_id text,
  sale_note text not null default '',
  notes jsonb not null default '[]'::jsonb,
  notes_error boolean not null default false,
  raw_data jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now()
);

create index if not exists meli_orders_seller_closed_idx
  on public.meli_orders (seller_id, date_closed desc);

create index if not exists meli_orders_seller_sale_idx
  on public.meli_orders (seller_id, sale_id);

create table if not exists public.meli_order_items (
  order_id text not null references public.meli_orders(order_id) on delete cascade,
  line_number integer not null,
  seller_id text not null,
  item_id text not null,
  user_product_id text,
  variation_id text,
  seller_sku text,
  seller_custom_field text,
  sku text,
  title text not null default '',
  quantity integer not null default 0,
  unit_price numeric,
  full_unit_price numeric,
  currency_id text,
  raw_data jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now(),
  primary key (order_id, line_number)
);

create index if not exists meli_order_items_seller_sku_idx
  on public.meli_order_items (seller_id, sku)
  where sku is not null;

create table if not exists public.meli_shipments (
  shipment_id text primary key,
  seller_id text not null,
  pack_id text,
  status text not null default '',
  substatus text,
  shipment_type text,
  mode text,
  logistic_type text,
  shipping_type text not null default 'normal'
    check (shipping_type in ('flex', 'normal')),
  tracking_number text,
  tracking_method text,
  date_created timestamptz,
  last_updated timestamptz,
  raw_data jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now()
);

create index if not exists meli_shipments_seller_pack_idx
  on public.meli_shipments (seller_id, pack_id);

create table if not exists public.meli_order_shipments (
  order_id text not null references public.meli_orders(order_id) on delete cascade,
  shipment_id text not null references public.meli_shipments(shipment_id) on delete cascade,
  relation_type text not null default 'forward',
  created_at timestamptz not null default now(),
  primary key (order_id, shipment_id, relation_type)
);

comment on table public.meli_notification_events is
  'Bandeja idempotente de webhooks recibidos desde Mercado Libre.';
comment on table public.meli_orders is
  'Estado consolidado de ordenes obtenido desde GET /orders/{id}.';
comment on table public.meli_order_items is
  'SKU, publicacion, variacion y cantidad por linea de cada orden.';
comment on table public.meli_shipments is
  'Estado consolidado de envios; self_service se clasifica como Flex.';

alter table public.meli_notification_events enable row level security;
alter table public.meli_packs enable row level security;
alter table public.meli_orders enable row level security;
alter table public.meli_order_items enable row level security;
alter table public.meli_shipments enable row level security;
alter table public.meli_order_shipments enable row level security;

-- Estas tablas contienen informacion comercial y se leen/escriben solamente
-- desde el backend con SUPABASE_SERVICE_ROLE_KEY. No se crean politicas para
-- anon ni authenticated.
