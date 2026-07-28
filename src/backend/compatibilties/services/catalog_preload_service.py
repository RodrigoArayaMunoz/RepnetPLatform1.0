import asyncio
from dataclasses import dataclass, field

from services.excel_service import normalize_for_compare
from services.ml_client import ml_client


def _build_name_to_id_map(values: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}

    for value in values or []:
        value_id = value.get("id")
        value_name = value.get("name")

        if value_id is None or not value_name:
            continue

        key = normalize_for_compare(value_name)
        if key:
            result[key] = str(value_id)

    return result


def _normalize_snapshot_map(data: dict | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (data or {}).items():
        if key is None or value is None:
            continue
        result[str(key)] = str(value)
    return result


@dataclass
class GlobalCatalogDictionaries:
    brands: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    years: dict[str, str] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    engines: dict[str, str] = field(default_factory=dict)
    transmissions: dict[str, str] = field(default_factory=dict)

    def stats(self) -> dict:
        return {
            "brands": len(self.brands),
            "models": len(self.models),
            "years": len(self.years),
            "versions": len(self.versions),
            "engines": len(self.engines),
            "transmissions": len(self.transmissions),
        }

    def to_dict(self) -> dict:
        return {
            "brands": self.brands,
            "models": self.models,
            "years": self.years,
            "versions": self.versions,
            "engines": self.engines,
            "transmissions": self.transmissions,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "GlobalCatalogDictionaries":
        data = data or {}
        return cls(
            brands=_normalize_snapshot_map(data.get("brands")),
            models=_normalize_snapshot_map(data.get("models")),
            years=_normalize_snapshot_map(data.get("years")),
            versions=_normalize_snapshot_map(data.get("versions")),
            engines=_normalize_snapshot_map(data.get("engines")),
            transmissions=_normalize_snapshot_map(data.get("transmissions")),
        )


class CatalogPreloadService:
    def __init__(self, call_ml, metrics):
        self.call_ml = call_ml
        self.metrics = metrics
        self.data = GlobalCatalogDictionaries()
        self._context_values: dict[tuple, list[dict]] = {}
        self._context_tasks: dict[tuple, asyncio.Task] = {}

    async def preload_all(
        self,
        access_token: str,
        *,
        user_id: int | str | None = None,
    ) -> GlobalCatalogDictionaries:
        brand_values = await self._get_contextual_values(
            access_token=access_token,
            user_id=user_id,
            attribute_id="BRAND",
            known_attributes=[],
        )
        self.data.brands = _build_name_to_id_map(brand_values)
        return self.data

    async def _get_contextual_values(
        self,
        *,
        access_token: str,
        user_id: int | str | None,
        attribute_id: str,
        known_attributes: list[dict[str, str]],
    ) -> list[dict]:
        if self.call_ml is None:
            raise RuntimeError(
                "El catálogo contextual no tiene un cliente de Mercado Libre configurado"
            )

        context_key = (
            attribute_id,
            tuple(
                (str(attribute["id"]), str(attribute["value_id"]))
                for attribute in known_attributes
            ),
        )
        cached = self._context_values.get(context_key)
        if cached is not None:
            return cached

        task = self._context_tasks.get(context_key)
        if task is None:
            task = asyncio.create_task(
                self.call_ml(
                    ml_client.get_top_values,
                    access_token,
                    attribute_id,
                    known_attributes=(
                        [dict(attribute) for attribute in known_attributes]
                        if known_attributes
                        else None
                    ),
                    user_id=user_id,
                    metrics=self.metrics,
                )
            )
            self._context_tasks[context_key] = task

        try:
            values = await task
        except Exception:
            self._context_tasks.pop(context_key, None)
            raise

        normalized_values = [
            value for value in (values or []) if isinstance(value, dict)
        ]
        self._context_values[context_key] = normalized_values
        self._context_tasks.pop(context_key, None)
        return normalized_values

    async def resolve_vehicle_attribute_ids(
        self,
        *,
        access_token: str,
        user_id: int | str | None,
        brand_name: str,
        model_name: str,
        year: int,
        version_name: str,
        engine_name: str,
        transmission_name: str,
    ) -> dict[str, str | None]:
        requested_attributes = [
            ("brand_id", "BRAND", brand_name),
            ("model_id", "CAR_AND_VAN_MODEL", model_name),
            ("year_id", "YEAR", str(year)),
            ("version_id", "CAR_AND_VAN_SUBMODEL", version_name),
            ("engine_id", "CAR_AND_VAN_ENGINE", engine_name),
            (
                "transmission_id",
                "TRANSMISSION_CONTROL_TYPE",
                transmission_name,
            ),
        ]
        resolved: dict[str, str | None] = {
            result_key: None
            for result_key, _attribute_id, _value_name in requested_attributes
        }
        known_attributes: list[dict[str, str]] = []

        for result_key, attribute_id, value_name in requested_attributes:
            if attribute_id == "BRAND" and self.data.brands:
                value_id = self.data.brands.get(normalize_for_compare(value_name))
            else:
                values = await self._get_contextual_values(
                    access_token=access_token,
                    user_id=user_id,
                    attribute_id=attribute_id,
                    known_attributes=known_attributes,
                )
                value_id = _build_name_to_id_map(values).get(
                    normalize_for_compare(value_name)
                )

            if not value_id:
                break

            resolved[result_key] = str(value_id)
            known_attributes.append(
                {
                    "id": attribute_id,
                    "value_id": str(value_id),
                }
            )

        return resolved

    def to_snapshot(self) -> dict:
        return self.data.to_dict()

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "CatalogPreloadService":
        service = cls(call_ml=None, metrics=None)
        service.data = GlobalCatalogDictionaries.from_dict(snapshot)
        return service

    def resolve_brand_id(self, brand_name: str) -> str | None:
        return self.data.brands.get(normalize_for_compare(brand_name))

    def resolve_model_id(self, model_name: str) -> str | None:
        return self.data.models.get(normalize_for_compare(model_name))

    def resolve_year_id(self, year: int) -> str | None:
        return self.data.years.get(normalize_for_compare(str(year)))

    def resolve_version_id(self, version_name: str) -> str | None:
        if not version_name:
            return None
        return self.data.versions.get(normalize_for_compare(version_name))

    def resolve_engine_id(self, engine_name: str) -> str | None:
        if not engine_name:
            return None
        return self.data.engines.get(normalize_for_compare(engine_name))

    def resolve_transmission_id(self, transmission_name: str) -> str | None:
        if not transmission_name:
            return None
        return self.data.transmissions.get(normalize_for_compare(transmission_name))
