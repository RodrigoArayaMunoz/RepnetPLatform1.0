begin;

alter table public.publicaciones_ml
  add column if not exists status text,
  add column if not exists has_compatibilities boolean;

comment on column public.publicaciones_ml.status is
  'Valor original de status del item de Mercado Libre (active, paused, closed, etc.).';
comment on column public.publicaciones_ml.has_compatibilities is
  'True si existe HAS_COMPATIBILITIES afirmativo; false si es negativo o falta y existe el tag incomplete_compatibilities; null sin evidencia.';

create or replace function public.upsert_publicaciones_ml_incremental(
  publication_rows jsonb
)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  affected_rows integer := 0;
begin
  if publication_rows is null or jsonb_typeof(publication_rows) <> 'array' then
    raise exception 'publication_rows debe ser un arreglo JSON';
  end if;

  with incoming as (
    select
      row.seller_id,
      btrim(row.mlc) as mlc,
      nullif(btrim(row.sku), '') as sku,
      nullif(btrim(row.part_number), '') as part_number,
      nullif(btrim(row.status), '') as status,
      row.has_compatibilities,
      coalesce(row.titulo, '') as titulo,
      row.fecha_creacion,
      coalesce(row.sincronizado_at, now()) as sincronizado_at
    from jsonb_to_recordset(publication_rows) as row(
      seller_id bigint,
      mlc text,
      sku text,
      part_number text,
      status text,
      has_compatibilities boolean,
      titulo text,
      fecha_creacion date,
      sincronizado_at timestamptz
    )
    where row.seller_id is not null
      and btrim(coalesce(row.mlc, '')) <> ''
  ),
  deduplicated as (
    select distinct on (seller_id, mlc)
      seller_id,
      mlc,
      sku,
      part_number,
      status,
      has_compatibilities,
      titulo,
      fecha_creacion,
      sincronizado_at
    from incoming
    order by seller_id, mlc, sincronizado_at desc
  )
  insert into public.publicaciones_ml as stored (
    seller_id,
    mlc,
    sku,
    part_number,
    status,
    has_compatibilities,
    titulo,
    fecha_creacion,
    sync_run_id,
    sincronizado_at
  )
  select
    seller_id,
    mlc,
    sku,
    part_number,
    status,
    has_compatibilities,
    titulo,
    fecha_creacion,
    gen_random_uuid(),
    sincronizado_at
  from deduplicated
  on conflict (seller_id, mlc) do update
  set
    sku = coalesce(excluded.sku, stored.sku),
    part_number = coalesce(excluded.part_number, stored.part_number),
    status = coalesce(excluded.status, stored.status),
    has_compatibilities = coalesce(excluded.has_compatibilities, stored.has_compatibilities),
    titulo = case
      when excluded.titulo <> '' then excluded.titulo
      else stored.titulo
    end,
    fecha_creacion = coalesce(excluded.fecha_creacion, stored.fecha_creacion),
    sincronizado_at = excluded.sincronizado_at;

  get diagnostics affected_rows = row_count;
  return affected_rows;
end;
$$;

revoke all on function public.upsert_publicaciones_ml_incremental(jsonb)
  from public, anon, authenticated;
grant execute on function public.upsert_publicaciones_ml_incremental(jsonb)
  to service_role;

comment on function public.upsert_publicaciones_ml_incremental(jsonb) is
  'Inserta o actualiza publicaciones notificadas con estado y compatibilidades, preservando sync_run_id y datos previos cuando no llegan valores nuevos.';

notify pgrst, 'reload schema';

commit;
