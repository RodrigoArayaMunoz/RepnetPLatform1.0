-- Mercado Libre's return-review deadline includes a time and UTC offset.
-- Keep fecha_revision (a date-only field) unchanged: it is not the deadline.
alter table public.devoluciones
  add column if not exists fecha_limite_revision timestamptz;

comment on column public.devoluciones.fecha_limite_revision is
  'Fecha y hora límite de la acción return_review informada por Mercado Libre.';
