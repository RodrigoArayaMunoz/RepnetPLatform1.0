import asyncio
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from config import settings
from services.auth_guard import SupabaseAuthVerifier
from services.supabase_process_store import SupabaseProcessStore
from services.supabase_publications_store import SupabasePublicationsStore

RealClient = httpx.AsyncClient


class SupabaseRequestEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_requests_share_verified_identity_without_mixing_tokens(self):
        calls = []

        async def respond(request):
            calls.append(request.headers["Authorization"])
            await asyncio.sleep(0)
            return httpx.Response(200, json={"id": request.headers["Authorization"]})

        verifier = SupabaseAuthVerifier()
        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch.object(settings, "supabase_anon_key", "anon-test"),
            patch.object(settings, "backend_auth_cache_ttl_seconds", 60),
            patch("services.auth_guard.httpx.AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(respond))),
        ):
            results = await asyncio.gather(*[
                verifier.verify_authorization_header("Bearer first-user") for _ in range(20)
            ])
            self.assertTrue(all(result["id"] == "Bearer first-user" for result in results))
            self.assertEqual(len(calls), 1)
            self.assertEqual((await verifier.verify_authorization_header("Bearer second-user"))["id"], "Bearer second-user")
            self.assertEqual(len(calls), 2)
            key = verifier._token_cache_key("first-user")
            verifier._cache[key] = (time.monotonic() - 1, {"id": "stale"})
            self.assertEqual((await verifier.verify_authorization_header("Bearer first-user"))["id"], "Bearer first-user")
            self.assertEqual(len(calls), 3)

    async def test_rejected_tokens_are_briefly_cached_but_new_tokens_and_server_errors_are_not(self):
        calls = []

        def respond(request):
            token = request.headers["Authorization"]
            calls.append(token)
            if token == "Bearer expired":
                return httpx.Response(401)
            if token == "Bearer server-error":
                return httpx.Response(503)
            return httpx.Response(200, json={"id": "user"})

        verifier = SupabaseAuthVerifier()
        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch.object(settings, "supabase_anon_key", "anon-test"),
            patch("services.auth_guard.httpx.AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(respond))),
        ):
            for _ in range(10):
                with self.assertRaises(HTTPException) as error:
                    await verifier.verify_authorization_header("Bearer expired")
                self.assertEqual(error.exception.status_code, 401)
            self.assertEqual(calls, ["Bearer expired"])
            self.assertEqual((await verifier.verify_authorization_header("Bearer refreshed"))["id"], "user")
            for _ in range(2):
                with self.assertRaises(HTTPException):
                    await verifier.verify_authorization_header("Bearer server-error")
            self.assertEqual(calls.count("Bearer server-error"), 2)
            for i in range(2100):
                verifier._remember(str(i), None, 10)
            self.assertLessEqual(len(verifier._cache), 2048)

    async def test_cancelling_one_request_does_not_cancel_shared_authentication(self):
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []

        async def respond(request):
            calls.append(request)
            started.set()
            await finish.wait()
            return httpx.Response(200, json={"id": "user"})

        verifier = SupabaseAuthVerifier()
        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch.object(settings, "supabase_anon_key", "anon-test"),
            patch("services.auth_guard.httpx.AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(respond))),
        ):
            first = asyncio.create_task(verifier.verify_authorization_header("Bearer token"))
            await started.wait()
            second = asyncio.create_task(verifier.verify_authorization_header("Bearer token"))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            finish.set()
            self.assertEqual((await second)["id"], "user")
            self.assertEqual(len(calls), 1)

    async def test_publication_presence_cache_refreshes_on_sync_change_and_expiry(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json=[{"mlc": "MLC1"}] if len(calls) > 1 else [])

        store = SupabasePublicationsStore()
        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch.object(settings, "supabase_service_role_key", "test"),
            patch("services.supabase_publications_store.httpx.AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(respond))),
        ):
            values = await asyncio.gather(*[store.has_rows(cache_key=("old-run",)) for _ in range(20)])
            self.assertEqual(values, [False] * 20)
            self.assertEqual(len(calls), 1)
            self.assertTrue(await store.has_rows(cache_key=("new-run",)))
            self.assertEqual(len(calls), 2)
            key, _, value = store._presence_cache
            store._presence_cache = (key, time.monotonic() - 1, value)
            self.assertTrue(await store.has_rows(cache_key=("new-run",)))
            self.assertEqual(len(calls), 3)
            self.assertTrue(await store.has_rows())
            self.assertEqual(len(calls), 4)

    async def test_process_status_updates_do_not_download_the_updated_row(self):
        calls = []

        def respond(request):
            calls.append(request)
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(request.headers["Prefer"], "return=minimal")
            self.assertEqual(request.url.params["id"], "eq.process-1")
            return httpx.Response(204)

        with (
            patch.object(settings, "supabase_url", "https://database.example.test"),
            patch.object(settings, "supabase_service_role_key", "test"),
            patch("services.supabase_process_store.httpx.AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(respond))),
        ):
            await SupabaseProcessStore().update_process_status("process-1", "Procesado")
        self.assertEqual(len(calls), 1)
