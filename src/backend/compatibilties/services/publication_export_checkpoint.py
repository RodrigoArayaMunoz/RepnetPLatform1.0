from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass
class PublicationExportCheckpointState:
    processed_rows: int = 0
    descriptions_found: int = 0
    descriptions_missing: int = 0
    descriptions_failed: int = 0
    cache_hits: int = 0
    api_items_queried: int = 0

    def add(self, row: dict[str, Any]) -> None:
        self.processed_rows += 1
        result = row.get("result")
        if result == "found":
            self.descriptions_found += 1
        elif result == "missing":
            self.descriptions_missing += 1
        else:
            self.descriptions_failed += 1

        source = row.get("source")
        if source == "cache":
            self.cache_hits += 1
        elif source == "api":
            self.api_items_queried += 1


class PublicationExportCheckpoint:
    VERSION = 1

    def __init__(
        self,
        *,
        path: str,
        source_fingerprint: str,
        total_rows: int,
    ) -> None:
        self.path = Path(path)
        self.source_fingerprint = source_fingerprint
        self.total_rows = int(total_rows)

    def load_or_initialize(
        self,
        publications: list[dict[str, Any]],
    ) -> PublicationExportCheckpointState:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._initialize()
            return PublicationExportCheckpointState()

        state = PublicationExportCheckpointState()
        valid_offset = 0
        should_reset = False

        with self.path.open("r+b") as checkpoint_file:
            metadata_line = checkpoint_file.readline()
            metadata = self._decode_line(metadata_line)
            if not self._is_valid_metadata(metadata):
                should_reset = True
            else:
                valid_offset = checkpoint_file.tell()
                while state.processed_rows < len(publications):
                    line = checkpoint_file.readline()
                    if not line:
                        break

                    row = self._decode_line(line)
                    expected_publication = publications[state.processed_rows]
                    if not self._is_valid_row(row, expected_publication):
                        break

                    state.add(row)
                    valid_offset = checkpoint_file.tell()

                trailing_data = checkpoint_file.read(1)
                if trailing_data or checkpoint_file.tell() > valid_offset:
                    checkpoint_file.seek(valid_offset)
                    checkpoint_file.truncate()
                    checkpoint_file.flush()
                    os.fsync(checkpoint_file.fileno())

        if should_reset:
            self._initialize()
            return PublicationExportCheckpointState()

        return state

    def append_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        encoded_rows = [
            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            for row in rows
        ]
        if not encoded_rows:
            return

        payload = ("\n".join(encoded_rows) + "\n").encode("utf-8")
        with self.path.open("ab") as checkpoint_file:
            checkpoint_file.write(payload)
            checkpoint_file.flush()
            os.fsync(checkpoint_file.fileno())

    def iter_rows(self) -> Iterator[dict[str, Any]]:
        with self.path.open("rb") as checkpoint_file:
            checkpoint_file.readline()
            for line in checkpoint_file:
                row = self._decode_line(line)
                if isinstance(row, dict):
                    yield row

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

    def _initialize(self) -> None:
        metadata = {
            "type": "publication_export_checkpoint",
            "version": self.VERSION,
            "source_fingerprint": self.source_fingerprint,
            "total_rows": self.total_rows,
            "created_at": time.time(),
        }
        temporary_path = self.path.with_name(
            f"{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary_path.open("wb") as checkpoint_file:
                checkpoint_file.write(
                    (
                        json.dumps(
                            metadata,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                checkpoint_file.flush()
                os.fsync(checkpoint_file.fileno())
            os.replace(temporary_path, self.path)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _is_valid_metadata(self, metadata: Any) -> bool:
        try:
            metadata_total_rows = int(metadata.get("total_rows") or -1)
        except (AttributeError, TypeError, ValueError):
            return False

        return (
            isinstance(metadata, dict)
            and metadata.get("type") == "publication_export_checkpoint"
            and metadata.get("version") == self.VERSION
            and metadata.get("source_fingerprint")
            == self.source_fingerprint
            and metadata_total_rows == self.total_rows
        )

    @staticmethod
    def _is_valid_row(
        row: Any,
        publication: dict[str, Any],
    ) -> bool:
        if not isinstance(row, dict):
            return False
        if row.get("result") not in {"found", "missing", "failed"}:
            return False
        if row.get("source") not in {"api", "cache", "invalid"}:
            return False
        if any(
            key not in row
            for key in ("mlc", "sku", "titulo", "descripcion")
        ):
            return False
        return str(row.get("mlc") or "") == str(
            publication.get("mlc") or ""
        )

    @staticmethod
    def _decode_line(line: bytes) -> Any:
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
