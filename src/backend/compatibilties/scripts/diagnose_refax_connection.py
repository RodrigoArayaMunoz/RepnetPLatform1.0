"""Diagnose outbound REFAX connectivity without logging requests or secrets."""

import json
import os
import socket
import ssl
import time
from urllib.parse import urlsplit

import httpx

from config import settings


def report(**result) -> None:
    print(json.dumps(result), flush=True)


def check_socket(host: str, port: int, *, use_tls: bool) -> None:
    started = time.monotonic()
    stage = "tcp"
    try:
        with socket.create_connection((host, port), timeout=5.0) as connection:
            if use_tls:
                stage = "tls"
                with ssl.create_default_context().wrap_socket(
                    connection, server_hostname=host
                ):
                    pass
    except (OSError, ssl.SSLError) as error:
        report(host=host, port=port, stage=stage, success=False,
               error_type=type(error).__name__, elapsed=round(time.monotonic() - started, 2))
    else:
        report(host=host, port=port, stage=stage, success=True,
               elapsed=round(time.monotonic() - started, 2))


def check_authentication(*, force_ipv4: bool) -> None:
    started = time.monotonic()
    options = {"timeout": httpx.Timeout(10.0, connect=5.0)}
    if force_ipv4:
        options.update(trust_env=False, transport=httpx.HTTPTransport(local_address="0.0.0.0"))
    try:
        with httpx.Client(**options) as client:
            response = client.post(
                settings.refax_api_base_url.rstrip("/") + "/api/Autenticacion/GetToken",
                json={"codigo": settings.refax_provider_code,
                      "clave": settings.refax_api_key, "pais": settings.refax_country_code},
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        report(check="authentication", force_ipv4=force_ipv4,
               http_status=response.status_code,
               token_received=isinstance(body, dict) and bool(body.get("token")),
               elapsed=round(time.monotonic() - started, 2))
    except httpx.HTTPError as error:
        report(check="authentication", force_ipv4=force_ipv4,
               error_type=type(error).__name__,
               cause_type=type(error.__cause__).__name__,
               elapsed=round(time.monotonic() - started, 2))


def main() -> None:
    parsed = urlsplit(settings.refax_api_base_url)
    host = parsed.hostname
    report(check="configuration", host=host,
           credentials_configured=bool(settings.refax_provider_code and settings.refax_api_key),
           proxies={key: bool(os.environ.get(key)) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")})
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        report(check="dns", addresses=list(dict.fromkeys(item[4][0] for item in addresses)))
    except OSError as error:
        report(check="dns", error_type=type(error).__name__)
    check_socket(host, parsed.port or 443, use_tls=True)
    check_socket(host, 80, use_tls=False)
    check_socket("api.mercadolibre.com", 443, use_tls=True)
    check_authentication(force_ipv4=False)
    check_authentication(force_ipv4=True)


if __name__ == "__main__":
    main()
