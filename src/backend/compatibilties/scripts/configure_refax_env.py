"""Transfer REFAX secrets over SSH stdin and update only backend configuration."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile

REFAX_KEYS = (
    "REFAX_PROVIDER_CODE",
    "REFAX_API_KEY",
    "REFAX_API_BASE_URL",
    "REFAX_COUNTRY_CODE",
    "REFAX_TEST_PROVIDER_CODE",
    "REFAX_TEST_API_KEY",
    "REFAX_TEST_API_BASE_URL",
    "REFAX_TEST_COUNTRY_CODE",
)


def validate_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in REFAX_KEYS or not isinstance(value, str):
            raise ValueError("Configuracion REFAX no valida")
        if any(character in value for character in "\r\n\x00"):
            raise ValueError(f"{key} debe contener un valor de una sola linea")
    if not values:
        raise ValueError("Faltan las credenciales REFAX")
    for prefix in ("REFAX", "REFAX_TEST"):
        keys = (
            f"{prefix}_PROVIDER_CODE", f"{prefix}_API_KEY",
            f"{prefix}_API_BASE_URL", f"{prefix}_COUNTRY_CODE",
        )
        if not any(key in values for key in keys):
            continue
        if not values.get(keys[0]) or not values.get(keys[1]):
            raise ValueError(f"Configura ambos secretos {keys[0]} y {keys[1]}")
        country = values.get(keys[3])
        if country and (not country.isdigit() or int(country) < 1):
            raise ValueError(f"{keys[3]} debe ser un entero positivo")


def update_env(env_path: Path, values: dict[str, str]) -> None:
    validate_values(values)
    original = env_path.read_text(encoding="utf-8")
    # Match entire dotenv assignments, including an existing quoted multiline value.
    for key, value in values.items():
        assignment = re.compile(
            rf"^[ \t]*(?:export[ \t]+)?{key}[ \t]*=[ \t]*"
            r"(?:'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|[^\r\n]*)"
            r"[^\r\n]*(?:\r?\n|$)",
            re.MULTILINE | re.DOTALL,
        )
        original = assignment.sub("", original)
        # JSON quoting preserves quotes/backslashes; $$ prevents Compose interpolation.
        encoded = json.dumps(value, ensure_ascii=False).replace("$", "$$")
        original = original.rstrip("\r\n") + f"\n{key}={encoded}\n"

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=env_path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            temporary.write(original)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, env_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def sync_over_ssh(
    host: str, identity: str, app_dir: str, values: dict[str, str]
) -> None:
    validate_values(values)
    if not app_dir or not PurePosixPath(app_dir).is_absolute():
        raise ValueError("VPS_APP_DIR debe ser una ruta absoluta")
    env_path = str(PurePosixPath(app_dir) / "src/backend/compatibilties/.env")
    source = Path(__file__).read_text(encoding="utf-8")
    command = (
        f"python3 -c {shlex.quote(source)} --env-file {shlex.quote(env_path)}"
    )
    subprocess.run(
        ["ssh", "-i", identity, "-o", "BatchMode=yes", host, command],
        input=json.dumps(values),
        text=True,
        check=True,
        timeout=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host")
    parser.add_argument("--identity")
    parser.add_argument("--app-dir")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.ssh_host:
        values = {key: os.environ[key] for key in REFAX_KEYS if os.environ.get(key)}
        if not values:
            print("[REFAX] Sin secretos nuevos; se conserva la configuracion del VPS.")
            return
        if not args.identity or not args.app_dir:
            raise ValueError("Faltan los parametros SSH del VPS")
        sync_over_ssh(args.ssh_host, args.identity, args.app_dir, values)
    elif args.env_file:
        update_env(args.env_file, json.load(sys.stdin))
        print("[REFAX] Credenciales del backend configuradas.")
    else:
        parser.error("Indica --ssh-host o --env-file")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"[REFAX] {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, subprocess.SubprocessError):
        print("[REFAX] No se pudo actualizar la configuracion del VPS.", file=sys.stderr)
        sys.exit(1)
