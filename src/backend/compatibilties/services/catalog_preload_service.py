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

    async def preload_all(self, access_token: str) -> GlobalCatalogDictionaries:
        brand_values = await self.call_ml(
            ml_client.get_top_values,
            access_token,
            "BRAND",
            metrics=self.metrics,
        )
        model_values = await self.call_ml(
            ml_client.get_top_values,
            access_token,
            "CAR_AND_VAN_MODEL",
            metrics=self.metrics,
        )
        year_values = await self.call_ml(
            ml_client.get_top_values,
            access_token,
            "YEAR",
            metrics=self.metrics,
        )
        version_values = await self.call_ml(
            ml_client.get_top_values,
            access_token,
            "CAR_AND_VAN_SUBMODEL",
            metrics=self.metrics,
        )
        engine_values = await self.call_ml(
            ml_client.get_top_values,
            access_token,
            "CAR_AND_VAN_ENGINE",
            metrics=self.metrics,
        )
        transmission_values = await self.call_ml(
            ml_client.get_top_values,
            access_token,
            "TRANSMISSION_CONTROL_TYPE",
            metrics=self.metrics,
        )

        self.data.brands = _build_name_to_id_map(brand_values)
        self.data.models = _build_name_to_id_map(model_values)
        self.data.years = _build_name_to_id_map(year_values)
        self.data.versions = _build_name_to_id_map(version_values)
        self.data.engines = _build_name_to_id_map(engine_values)
        self.data.transmissions = _build_name_to_id_map(transmission_values)

        return self.data

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