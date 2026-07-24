import unittest
from unittest.mock import AsyncMock, Mock, patch

from routers.publications_router import _get_sync_state
from services.publication_sync_store import publication_sync_store
from services.supabase_publications_store import (
    SupabasePublicationsStore,
    supabase_publications_store,
)


class _FakeResponse:
    def __init__(self, rows):
        self.status_code = 200
        self.text = ""
        self._rows = rows

    def json(self):
        return self._rows


class _FakeAsyncClient:
    def __init__(self, rows):
        self._response = _FakeResponse(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, *args, **kwargs):
        return self._response


class SupabasePublicationsVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def _has_rows(self, rows):
        store = SupabasePublicationsStore()
        store._headers = Mock(return_value={})

        with patch(
            "services.supabase_publications_store.httpx.AsyncClient",
            return_value=_FakeAsyncClient(rows),
        ):
            return await store.has_rows()

    async def test_empty_table_is_reported_as_false(self):
        self.assertFalse(await self._has_rows([]))

    async def test_table_with_one_row_is_reported_as_true(self):
        self.assertTrue(await self._has_rows([{"mlc": "MLC123"}]))

    async def test_sync_state_includes_has_publications(self):
        with (
            patch.object(
                publication_sync_store,
                "get_state",
                return_value={"running": False, "status": "idle"},
            ),
            patch.object(
                supabase_publications_store,
                "has_rows",
                new=AsyncMock(return_value=True),
            ),
        ):
            state = await _get_sync_state()

        self.assertTrue(state["has_publications"])
        self.assertFalse(state["running"])


if __name__ == "__main__":
    unittest.main()
