import json
import os
from typing import Any

from config import settings


class ProcessQueueErrorStore:
    def __init__(self) -> None:
        self.base_dir = os.path.join(settings.upload_dir, "process_queue_errors")

    def _get_path(self, row_id: int | str) -> str:
        safe_row_id = str(row_id).strip() or "unknown"
        return os.path.join(self.base_dir, f"{safe_row_id}.json")

    def save(self, row_id: int | str, payload: dict[str, Any]) -> None:
        path = self._get_path(row_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    def get(self, row_id: int | str) -> dict[str, Any] | None:
        path = self._get_path(row_id)
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)

        return data if isinstance(data, dict) else None

    def get_many(self, row_ids: list[int | str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for row_id in row_ids:
            payload = self.get(row_id)
            if payload:
                result[str(row_id)] = payload

        return result

    def clear(self, row_id: int | str) -> None:
        path = self._get_path(row_id)
        try:
            os.remove(path)
        except FileNotFoundError:
            return


process_queue_error_store = ProcessQueueErrorStore()
