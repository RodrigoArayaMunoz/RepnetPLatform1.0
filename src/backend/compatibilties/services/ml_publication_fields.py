import re
from datetime import date
from typing import Any


PUBLICATION_DETAIL_ATTRIBUTES = "id,title,attributes,date_created,status,tags"


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


def extract_publication_part_number(item: dict[str, Any]) -> str | None:
    attributes = item.get("attributes")
    if not isinstance(attributes, list):
        return None

    for attribute in attributes:
        if not isinstance(attribute, dict) or attribute.get("id") != "PART_NUMBER":
            continue
        value = attribute.get("value_name")
        if value is None:
            return None
        return str(value).strip() or None

    return None


def extract_publication_status(item: dict[str, Any]) -> str | None:
    return str(item.get("status") or "").strip() or None


def extract_publication_has_compatibilities(item: dict[str, Any]) -> bool | None:
    attributes = item.get("attributes")
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict) or attribute.get("id") != "HAS_COMPATIBILITIES":
                continue
            # The attribute itself signals compatibilities. Respect an explicit
            # negative value if ML provides one instead of the usual "Sí".
            value = str(attribute.get("value_name") or "").strip().casefold()
            return value not in {"no", "false"}

    tags = item.get("tags")
    if isinstance(tags, list) and "incomplete_compatibilities" in tags:
        return False

    # Missing evidence is not the same as confirmed missing compatibilities.
    return None


def extract_publication_creation_date(raw_value: Any) -> str | None:
    date_text = str(raw_value or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        return None

    try:
        date.fromisoformat(date_text)
    except ValueError:
        return None

    return date_text
