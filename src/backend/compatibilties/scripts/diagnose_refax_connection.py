"""Diagnose outbound REFAX connectivity without logging requests or secrets."""

import json
import os
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit


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
    import httpx
    from config import settings

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


def check_packet_flow(address: str, port: int) -> None:
    # Observe TCP headers for this destination only; never capture payloads or a pcap.
    try:
        observer = subprocess.Popen(
            ["sudo", "-n", "timeout", "8", "tcpdump", "-n", "-i", "any",
             "-c", "12", f"host {address} and tcp port {port}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as error:
        report(check="tcp_packets", error_type=type(error).__name__)
        return
    try:
        time.sleep(0.3)
        check_socket(address, port, use_tls=False)
        captured, _stderr = observer.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        observer.kill()
        observer.communicate()
        report(check="tcp_packets", error_type="ObservationTimeout")
        return
    outbound = f"> {address}.{port}:"
    inbound = f"{address}.{port} >"
    headers = captured.splitlines()
    report(
        check="tcp_packets", return_code=observer.returncode,
        outbound_syn_packets=sum(outbound in line and "Flags [S]" in line for line in headers),
        inbound_syn_ack_packets=sum(inbound in line and "Flags [S.]" in line for line in headers),
        inbound_reset_packets=sum(inbound in line and "Flags [R" in line for line in headers),
        observed_tcp_headers=headers,
    )


def main() -> None:
    host_only = "--host" in sys.argv[1:]
    if host_only:
        base_url = "https://api.refax.com"
    else:
        from config import settings
        base_url = settings.refax_api_base_url
    parsed = urlsplit(base_url)
    host = parsed.hostname
    report(check="configuration", host=host,
           runtime="host" if host_only else "container",
           proxies={key: bool(os.environ.get(key)) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")})
    addresses = []
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        report(check="dns", addresses=list(dict.fromkeys(item[4][0] for item in addresses)))
    except OSError as error:
        report(check="dns", error_type=type(error).__name__)
    check_socket(host, parsed.port or 443, use_tls=True)
    check_socket(host, 80, use_tls=False)
    check_socket("api.mercadolibre.com", 443, use_tls=True)
    if not host_only:
        check_authentication(force_ipv4=False)
        check_authentication(force_ipv4=True)
    else:
        for command in (
            ["ip", "route", "get", addresses[0][4][0] if addresses else host],
            ["iptables", "-S", "OUTPUT"],
            ["iptables", "-S", "FORWARD"],
            ["iptables", "-S", "DOCKER-USER"],
            ["ufw", "status", "verbose"],
        ):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                report(check="host_network", command=command, return_code=result.returncode,
                       output=result.stdout.splitlines())
                if result.returncode and command[0] in {"iptables", "ufw"}:
                    elevated = subprocess.run(
                        ["sudo", "-n", *command], capture_output=True, text=True, timeout=5
                    )
                    report(check="host_firewall", command=command,
                           return_code=elevated.returncode,
                           output=elevated.stdout.splitlines())
            except (OSError, subprocess.SubprocessError) as error:
                report(check="host_network", command=command, error_type=type(error).__name__)
        if addresses:
            check_packet_flow(addresses[0][4][0], parsed.port or 443)


if __name__ == "__main__":
    main()
