import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from services.refax_connection_service import (
    RefaxConnectionError,
    RefaxConnectionService,
)


class FakeRefaxStore:
    def __init__(self, row=None):
        self.row = row
        self.saved_payload = None
        self.failures = []

    async def get(self):
        return dict(self.row) if self.row else None

    async def save_token(self, payload):
        self.saved_payload = payload
        self.row = {
            "id": 1,
            "is_active": True,
            "last_error": None,
            **payload,
        }
        return dict(self.row)

    async def record_failure(self, message, *, deactivate):
        self.failures.append((message, deactivate))
        if self.row:
            self.row["last_error"] = message
            if deactivate:
                self.row["is_active"] = False


class RefaxConnectionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    def build_row(self, *, refresh_delta, expires_delta):
        return {
            "id": 1,
            "is_active": True,
            "access_token": "private-token",
            "obtained_at": (self.now - timedelta(hours=1)).isoformat(),
            "refresh_at": (self.now + refresh_delta).isoformat(),
            "expires_at": (self.now + expires_delta).isoformat(),
            "last_error": None,
        }

    async def test_connect_persists_refresh_and_expiration_dates(self):
        store = FakeRefaxStore()
        service = RefaxConnectionService(store)
        service._now = lambda: self.now
        service._request_token = AsyncMock(return_value="private-token")

        result = await service.connect()

        self.assertTrue(result["connected"])
        self.assertNotIn("access_token", result)
        self.assertEqual(
            datetime.fromisoformat(store.saved_payload["refresh_at"]),
            self.now + timedelta(hours=7, minutes=45),
        )
        self.assertEqual(
            datetime.fromisoformat(store.saved_payload["expires_at"]),
            self.now + timedelta(hours=8),
        )

    async def test_status_renews_a_due_token(self):
        store = FakeRefaxStore(
            self.build_row(
                refresh_delta=timedelta(seconds=-1),
                expires_delta=timedelta(minutes=10),
            )
        )
        service = RefaxConnectionService(store)
        service._now = lambda: self.now
        renewed_row = self.build_row(
            refresh_delta=timedelta(hours=7, minutes=45),
            expires_delta=timedelta(hours=8),
        )
        renewed_row["access_token"] = "renewed-token"
        service._authenticate = AsyncMock(return_value=renewed_row)

        result = await service.status()

        self.assertTrue(result["connected"])
        service._authenticate.assert_awaited_once()

    async def test_failed_renewal_keeps_unexpired_token_available(self):
        store = FakeRefaxStore(
            self.build_row(
                refresh_delta=timedelta(minutes=-1),
                expires_delta=timedelta(minutes=14),
            )
        )
        service = RefaxConnectionService(store)
        service._now = lambda: self.now
        service._authenticate = AsyncMock(
            side_effect=RefaxConnectionError("REFAX temporalmente no disponible")
        )

        result = await service.status()

        self.assertTrue(result["connected"])
        self.assertEqual(len(store.failures), 1)
        self.assertFalse(store.failures[0][1])

    async def test_failed_renewal_disconnects_an_expired_token(self):
        store = FakeRefaxStore(
            self.build_row(
                refresh_delta=timedelta(minutes=-16),
                expires_delta=timedelta(seconds=-1),
            )
        )
        service = RefaxConnectionService(store)
        service._now = lambda: self.now
        service._authenticate = AsyncMock(
            side_effect=RefaxConnectionError("REFAX temporalmente no disponible")
        )

        result = await service.status()

        self.assertFalse(result["connected"])
        self.assertEqual(len(store.failures), 1)
        self.assertTrue(store.failures[0][1])


if __name__ == "__main__":
    unittest.main()
