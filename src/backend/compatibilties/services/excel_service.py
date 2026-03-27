import os
import re
import shutil
import unicodedata
import uuid
from typing import Any

import pandas as pd
from fastapi import UploadFile

from config import settings

os.makedirs(settings.upload_dir, exist_ok=True)

COLUMN_ALIASES = {
    "ASOCIACION ML": ["ASOCIACION ML", "ASOCIACIÓN ML"],
    "MARCA": ["MARCA"],
    "MODELO": ["MODELO"],
    "VERSION": ["VERSION", "VERSIÓN"],
    "CILINDRADA": ["CILINDRADA"],
    "TRANSMISION": ["TRANSMISION", "TRANSMISIÓN"],
    "AÑO": ["AÑO", "ANO"],
    "FAMILIA": ["FAMILIA"],
    "POSICION_DT": ["DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE"],
    "POSICION_ID": ["IZQUIERDA/DERECHA"],
}

COMPAT_REQUIRED_LOGICAL_COLUMNS = [
    "ASOCIACION ML",
    "MARCA",
    "MODELO",
    "VERSION",
    "CILINDRADA",
    "TRANSMISION",
    "AÑO",
]


def get_row_value(row: dict, column_name: str) -> Any:
    aliases = COLUMN_ALIASES.get(column_name, [column_name])
    for alias in aliases:
        if alias in row and row[alias] is not None:
            return row[alias]
    return ""


def _sanitize_filename(filename: str) -> str:
    filename = os.path.basename(filename or "archivo.xlsx").strip()
    if not filename:
        filename = "archivo.xlsx"
    filename = filename.replace(" ", "_")
    return filename


async def save_upload_file(upload_file: UploadFile, upload_dir: str) -> str:
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = _sanitize_filename(upload_file.filename)
    unique_name = f"{uuid.uuid4()}_{safe_name}"
    destination = os.path.join(upload_dir, unique_name)

    await upload_file.seek(0)
    with open(destination, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

    return destination


def _normalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _pick_existing_columns(df: pd.DataFrame) -> dict[str, str]:
    """
    Retorna un mapa:
    {
        "MARCA": "Marca",
        "MODELO": "MODELO",
        ...
    }
    donde la clave es la columna lógica y el valor es el nombre real encontrado.
    """
    found: dict[str, str] = {}
    cols = set(str(c).strip() for c in df.columns)

    for logical_col in COMPAT_REQUIRED_LOGICAL_COLUMNS:
        aliases = COLUMN_ALIASES.get(logical_col, [logical_col])
        for alias in aliases:
            if alias in cols:
                found[logical_col] = alias
                break

    return found


def validate_dataframe_columns(df: pd.DataFrame) -> list[str]:
    missing = []
    found = _pick_existing_columns(df)

    for logical_col in COMPAT_REQUIRED_LOGICAL_COLUMNS:
        if logical_col not in found:
            aliases = COLUMN_ALIASES.get(logical_col, [logical_col])
            missing.append(f"{logical_col} (aliases: {aliases})")

    return missing


def _rename_to_logical_columns(df: pd.DataFrame) -> pd.DataFrame:
    found = _pick_existing_columns(df)
    rename_map = {real_name: logical_name for logical_name, real_name in found.items()}
    return df.rename(columns=rename_map)


def load_excel_rows(file_path: str, sheet_name: str = "Hoja1") -> list[dict]:
    if not os.path.exists(file_path):
        raise ValueError(f"No existe el archivo: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".csv":
            df = pd.read_csv(file_path)
        elif ext in (".xlsx", ".xls"):
            try:
                df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
            except ValueError:
                excel_file = pd.ExcelFile(file_path, engine="openpyxl")
                if not excel_file.sheet_names:
                    raise ValueError("El archivo Excel no contiene hojas")
                df = pd.read_excel(
                    file_path,
                    sheet_name=excel_file.sheet_names[0],
                    engine="openpyxl",
                )
        else:
            raise ValueError("Formato no soportado. Solo se aceptan .xlsx, .xls o .csv")
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo: {str(e)}")

    df = _normalize_dataframe_columns(df)

    missing = validate_dataframe_columns(df)
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}")

    if len(df.index) == 0:
        raise ValueError("El archivo no tiene filas")

    df = _rename_to_logical_columns(df)

    return df.to_dict(orient="records")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_for_compare(value: Any) -> str:
    text = normalize_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_engine(value: Any) -> str:
    return normalize_text(value)


def normalize_transmission(value: Any) -> str:
    text = normalize_for_compare(value)
    mapping = {
        "manual": "Manual",
        "mecanico": "Manual",
        "mecanica": "Manual",
        "mecanico manual": "Manual",
        "mecanica manual": "Manual",
        "automatico": "Automática",
        "automatica": "Automática",
        "auto": "Automática",
        "cvt": "CVT",
    }
    return mapping.get(text, normalize_text(value))


def extract_item_id(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None

    match = re.search(r"(ML[AMCBU]\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return text.upper() if text else None


def parse_year_value(value: Any) -> int | None:
    text = normalize_text(value)
    if not text:
        return None

    try:
        year = int(float(text))
    except (TypeError, ValueError):
        return None

    if year < 1900 or year > 2100:
        return None

    return year