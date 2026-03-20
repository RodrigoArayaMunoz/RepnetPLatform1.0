import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from config import settings


class TokenStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return {}
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, tokens: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(tokens, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, user_id: int | str) -> dict[str, Any] | None:
        return self.load().get(str(user_id))

    def set(self, user_id: int | str, token_data: dict[str, Any]) -> None:
        all_tokens = self.load()
        all_tokens[str(user_id)] = token_data
        self.save(all_tokens)

    def remove(self, user_id: int | str) -> None:
        all_tokens = self.load()
        all_tokens.pop(str(user_id), None)
        self.save(all_tokens)

    def first_user_id(self) -> str | None:
        all_tokens = self.load()
        return next(iter(all_tokens.keys()), None)

    @staticmethod
    def build_payload(token_data: dict[str, Any], user_id: int | str) -> dict[str, Any]:
        expires_in = int(token_data.get("expires_in", 0))
        now = int(time.time())

        return {
            "user_id": str(user_id),
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "bearer"),
            "scope": token_data.get("scope"),
            "expires_in": expires_in,
            "expires_at": now + expires_in - 60 if expires_in > 60 else now + expires_in,
        }


token_store = TokenStore(settings.tokens_file)


def require_ml_env() -> None:
    if not settings.ml_client_id:
        raise HTTPException(status_code=500, detail="Falta ML_CLIENT_ID")
    if not settings.ml_client_secret:
        raise HTTPException(status_code=500, detail="Falta ML_CLIENT_SECRET")
    if not settings.ml_redirect_uri:
        raise HTTPException(status_code=500, detail="Falta ML_REDIRECT_URI")