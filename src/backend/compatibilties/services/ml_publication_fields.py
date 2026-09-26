import re
from datetime import date
from typing import Any


PUBLICATION_DETAIL_ATTRIBUTES = "id,title,attributes,date_created"


def extract_publication_sku(
    item: dict[str, Any],
    *,
    fallback: str | None = None,
) -> str | None:
    attributes = item.get("attributes")
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict) or attribute.get("id") != "SELLER_SKU":
                continue

            value = str(attribute.get("value_name") or "").strip()
            if value:
                return value

    fallback_value = str(fallback or "").strip()
    return fallback_value or None


def extract_publication_creation_date(raw_value: Any) -> str | None:
    date_text = str(raw_value or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        return None

    try:
        date.fromisoformat(date_text)
    except ValueError:
        return None

    return date_text
