import json
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from dotenv import dotenv_values

from scripts.configure_refax_env import sync_over_ssh, update_env
from scripts.check_refax_connection import check_connection
from scripts.diagnose_refax_connection import check_authentication


class RefaxEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.env_path = Path(self.directory) / ".env"
        self.original = (
            "# Existing backend configuration\n"
            "SUPABASE_SERVICE_ROLE_KEY='keep-this-private'\n"
            "ML_CLIENT_SECRET='keep-this-too'\n"
            "REFAX_PROVIDER_CODE=old-provider\n"
            "export REFAX_API_KEY='old\nmultiline-key' # old credential\n"
            "REFAX_COUNTRY_CODE=1\n"
        )
        self.env_path.write_text(self.original, encoding="utf-8")
        self.values = {
            "REFAX_PROVIDER_CODE": "test-provider",
            "REFAX_API_KEY": "private 'key' # $literal `text` \\ slash",
        }

    def test_updates_credentials_preserving_other_configuration_and_literal_values(self):
        update_env(self.env_path, self.values)

        parsed = dotenv_values(self.env_path, interpolate=False)
        self.assertEqual(parsed["SUPABASE_SERVICE_ROLE_KEY"], "keep-this-private")
        self.assertEqual(parsed["ML_CLIENT_SECRET"], "keep-this-too")
        self.assertEqual(parsed["REFAX_COUNTRY_CODE"], "1")
        for key, value in self.values.items():
            self.assertEqual(parsed[key].replace("$$", "$"), value)
        content = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("multiline-key", content)
        self.assertTrue(content.startswith("# Existing backend configuration\n"))
        if os.name != "nt":
            self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)

    def test_repeated_sync_does_not_accumulate_old_credentials(self):
        update_env(self.env_path, self.values)
        first = self.env_path.read_text(encoding="utf-8")
        update_env(self.env_path, self.values)
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), first)

    def test_invalid_or_incomplete_configuration_never_modifies_env(self):
        invalid_values = [
            {"REFAX_PROVIDER_CODE": "only-code"},
            {**self.values, "REFAX_API_KEY": "key\nML_CLIENT_SECRET=bad"},
            {**self.values, "REFAX_COUNTRY_CODE": "invalid"},
            {**self.values, "REFAX_COUNTRY_CODE": "0"},
            {**self.values, "ML_CLIENT_SECRET": "unexpected"},
        ]
        for values in invalid_values:
            with self.subTest(values=list(values)):
                with self.assertRaises(ValueError):
                    update_env(self.env_path, values)
                self.assertEqual(self.env_path.read_text(encoding="utf-8"), self.original)

    def test_failed_atomic_replace_keeps_original_and_removes_temporary_secret_file(self):
        with patch("scripts.configure_refax_env.os.replace", side_effect=OSError):
            with self.assertRaises(OSError):
                update_env(self.env_path, self.values)
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), self.original)
        self.assertEqual(list(Path(self.directory).iterdir()), [self.env_path])

    def test_ssh_transfers_credentials_only_in_stdin_and_quotes_remote_path(self):
        with patch("scripts.configure_refax_env.subprocess.run") as run:
            sync_over_ssh("deploy@example.test", "/tmp/deploy-key", "/srv/app with spaces", self.values)
        command = run.call_args.args[0]
        for value in self.values.values():
            self.assertNotIn(value, " ".join(command))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), self.values)
        remote_args = shlex.split(command[-1])
        self.assertEqual(remote_args[:2], ["python3", "-c"])
        self.assertEqual(remote_args[-2:], ["--env-file", "/srv/app with spaces/src/backend/compatibilties/.env"])

    def test_remote_program_runs_without_dependencies_and_never_prints_credentials(self):
        with patch("scripts.configure_refax_env.subprocess.run") as run:
            sync_over_ssh("deploy@example.test", "/tmp/key", "/srv/app", self.values)
        source = shlex.split(run.call_args.args[0][-1])[2]
        result = subprocess.run(
            [sys.executable, "-c", source, "--env-file", str(self.env_path)],
            input=json.dumps(self.values), text=True, capture_output=True, check=True,
        )
        for value in self.values.values():
            self.assertNotIn(value, result.stdout + result.stderr)
        parsed_key = dotenv_values(self.env_path, interpolate=False)["REFAX_API_KEY"]
        self.assertEqual(parsed_key.replace("$$", "$"), self.values["REFAX_API_KEY"])


class RefaxDeploymentCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_reconnects_and_returns_only_public_state(self):
        with patch("scripts.check_refax_connection.refax_connection_service") as connection:
            connection.connect = AsyncMock(return_value={
                "connected": True, "expires_at": "2026-10-02T18:00:00Z",
                "access_token": "must-not-be-printed",
            })
            result = await check_connection()
        connection.connect.assert_awaited_once()
        self.assertEqual(result, {"connected": True, "expires_at": "2026-10-02T18:00:00Z"})


class RefaxNetworkDiagnosticTests(unittest.TestCase):
    def test_network_diagnostic_never_prints_tokens_response_bodies_or_raw_errors(self):
        import httpx

        private = "must-not-appear-in-diagnostic-output"
        outcomes = [
            httpx.Response(200, json={"token": private, "details": private}),
            httpx.ConnectTimeout(private),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                output = StringIO()
                with patch("httpx.Client") as client, redirect_stdout(output):
                    post = client.return_value.__enter__.return_value.post
                    if isinstance(outcome, Exception):
                        post.side_effect = outcome
                    else:
                        post.return_value = outcome
                    check_authentication(force_ipv4=False)
                self.assertNotIn(private, output.getvalue())
                result = json.loads(output.getvalue())
                self.assertEqual(result["check"], "authentication")


if __name__ == "__main__":
    unittest.main()
