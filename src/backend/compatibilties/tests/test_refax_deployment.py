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
from scripts.diagnose_refax_connection import check_authentication, check_packet_flow


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

    def test_test_credentials_can_be_updated_without_changing_production(self):
        values = {
            "REFAX_TEST_PROVIDER_CODE": "test-provider",
            "REFAX_TEST_API_KEY": "private-test-key#@",
            "REFAX_TEST_API_BASE_URL": "http://apitest.refax.com:8098",
            "REFAX_TEST_COUNTRY_CODE": "1",
        }
        production_before = dotenv_values(self.env_path, interpolate=False)
        update_env(self.env_path, values)
        parsed = dotenv_values(self.env_path, interpolate=False)
        for key, value in production_before.items():
            self.assertEqual(parsed[key], value)
        for key, value in values.items():
            self.assertEqual(parsed[key], value)

    def test_incomplete_test_credentials_never_modify_env(self):
        with self.assertRaises(ValueError):
            update_env(self.env_path, {**self.values, "REFAX_TEST_PROVIDER_CODE": "test-provider"})
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), self.original)

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
    def test_packet_diagnostic_observes_only_refax_tcp_headers_without_payload_capture(self):
        output = StringIO()
        captured = (
            "IP 192.0.2.10.40000 > 203.0.113.5.443: Flags [S], length 0\n"
            "IP 192.0.2.10.40000 > 203.0.113.5.443: Flags [S], length 0\n"
            "IP 203.0.113.5.443 > 192.0.2.10.40000: Flags [S.], length 0\n"
        )
        with (
            patch("scripts.diagnose_refax_connection.subprocess.Popen") as popen,
            patch("scripts.diagnose_refax_connection.time.sleep"),
            patch("scripts.diagnose_refax_connection.check_socket") as connect,
            redirect_stdout(output),
        ):
            popen.return_value.communicate.return_value = (captured, "")
            popen.return_value.returncode = 124
            check_packet_flow("203.0.113.5", 443)
        command = popen.call_args.args[0]
        self.assertEqual(command[-1], "host 203.0.113.5 and tcp port 443")
        for flag in ("-w", "-A", "-X"):
            self.assertNotIn(flag, command)
        connect.assert_called_once_with("203.0.113.5", 443, use_tls=False)
        result = json.loads(output.getvalue())
        self.assertEqual(result["outbound_syn_packets"], 2)
        self.assertEqual(result["inbound_syn_ack_packets"], 1)

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
