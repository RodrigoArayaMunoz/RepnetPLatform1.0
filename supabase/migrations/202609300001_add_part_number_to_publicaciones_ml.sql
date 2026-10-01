alter table public.publicaciones_ml
  add column if not exists part_number text;

comment on column public.publicaciones_ml.part_number is
  'Valor textual de attributes[id=PART_NUMBER].value_name en el item de Mercado Libre.';

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
      coalesce(row.titulo, '') as titulo,
      row.fecha_creacion,
      coalesce(row.sincronizado_at, now()) as sincronizado_at
    from jsonb_to_recordset(publication_rows) as row(
      seller_id bigint,
      mlc text,
      sku text,
      part_number text,
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
    titulo,
    fecha_creacion,
    gen_random_uuid(),
    sincronizado_at
  from deduplicated
  on conflict (seller_id, mlc) do update
  set
    sku = coalesce(excluded.sku, stored.sku),
    part_number = coalesce(excluded.part_number, stored.part_number),
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
  'Inserta o actualiza publicaciones notificadas preservando sync_run_id y PART_NUMBER existente cuando no llega un valor nuevo.';

notify pgrst, 'reload schema';
