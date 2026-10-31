#!/usr/bin/env python3
"""Verify a deployed Xingjian instance from the outside.

    python scripts/verify_deploy.py calhub.asia
    python scripts/verify_deploy.py calhub.asia --json

Checks, in order: system DNS -> public DNS -> TCP 443 -> TLS certificate
-> HTTP to HTTPS redirect -> login page -> /api/health ->
/api/agent/health -> /api/auth/me must be 401.

Public DNS lookups use DNS over HTTPS, so a machine behind a strict
resolver still gets a reliable answer. Standard library only.

Exit code is 0 only when every required check passes.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DOH_ENDPOINTS = (
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
)
UA = "xingjian-verify/1.0"


class Report:
    """Collect check results and print them as they happen."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str, bool]] = []

    def add(
        self, name: str, ok: bool, detail: str = "", required: bool = True
    ) -> None:
        self.rows.append((name, ok, detail, required))
        mark = "PASS" if ok else ("WARN" if not required else "FAIL")
        line = f"  {mark}  {name}"
        if detail:
            line += f"  -  {detail}"
        print(line)

    @property
    def failures(self) -> list[tuple[str, bool, str, bool]]:
        return [row for row in self.rows if not row[1] and row[3]]

    @property
    def passed(self) -> int:
        return sum(1 for row in self.rows if row[1])


def fetch(
    url: str, headers: dict[str, str] | None = None, timeout: float = 15
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url, headers=headers or {"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def system_dns(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def doh_lookup(host: str) -> tuple[list[str], str]:
    """Resolve through DNS over HTTPS; returns (addresses, detail)."""
    last = "no endpoint reachable"
    for endpoint in DOH_ENDPOINTS:
        url = f"{endpoint}?name={host}&type=A"
        try:
            status, _, body = fetch(
                url, {"Accept": "application/dns-json", "User-Agent": UA}
            )
            payload = json.loads(body.decode("utf-8"))
        except (OSError, ValueError, ssl.SSLError) as error:
            last = f"{endpoint.split('/')[2]}: {type(error).__name__}"
            continue
        if status != 200:
            last = f"{endpoint.split('/')[2]}: HTTP {status}"
            continue
        answers = [
            item.get("data", "")
            for item in payload.get("Answer", [])
            if item.get("type") in (1, 5, 28)
        ]
        code = payload.get("Status")
        name = endpoint.split("/")[2]
        if answers:
            return answers, f"via {name}"
        return [], f"via {name}, status {code} (3 = NXDOMAIN)"
    return [], last


def tls_check(host: str, timeout: float, port: int) -> tuple[bool, str]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as sock:
                cert = sock.getpeercert()
    except ssl.SSLCertVerificationError as error:
        return False, f"certificate rejected: {error.verify_message}"
    except (socket.timeout, OSError) as error:
        return False, f"connection failed: {error}"

    not_after = cert.get("notAfter", "")
    subject = dict(item[0] for item in cert.get("issuer", ()))
    issuer = subject.get("organizationName", "?")
    try:
        expires = datetime.strptime(
            not_after, "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return True, f"expires {not_after}"
    days = (expires - datetime.now(timezone.utc)).days
    return days > 7, f"{days} days left, issuer {issuer}"


def check_app(report: Report, base: str, timeout: float) -> None:
    try:
        status, _, body = fetch(f"{base}/", timeout=timeout)
        text = body.decode("utf-8", "replace")
        report.add(
            "login page served",
            status == 200 and "行间" in text,
            f"HTTP {status}, {len(body)} bytes",
        )
        report.add(
            "frontend loads auth and agent",
            "auth.js" in text and "agent.js" in text,
        )
    except Exception as error:  # noqa: BLE001 - report, never crash
        report.add(
            "login page served", False, f"{type(error).__name__}: {error}"
        )

    try:
        status, _, body = fetch(f"{base}/api/health", timeout=timeout)
        payload = json.loads(body.decode("utf-8")) if body else {}
        report.add(
            "/api/health",
            status == 200 and payload.get("ok") is True,
            json.dumps(payload, ensure_ascii=False)[:120],
        )
    except Exception as error:  # noqa: BLE001
        report.add("/api/health", False, f"{type(error).__name__}: {error}")

    try:
        status, _, body = fetch(f"{base}/api/agent/health", timeout=timeout)
        payload = json.loads(body.decode("utf-8")) if body else {}
        configured = payload.get("configured")
        detail = (
            f"model {payload.get('model')}, "
            f"AI {'configured' if configured else 'OFFLINE (no API key)'}"
        )
        report.add(
            "/api/agent/health reports configured",
            status == 200 and configured is True,
            detail,
        )
    except Exception as error:  # noqa: BLE001
        report.add(
            "/api/agent/health reports configured",
            False,
            f"{type(error).__name__}: {error}",
        )

    try:
        status, _, _ = fetch(f"{base}/api/auth/me", timeout=timeout)
        report.add(
            "/api/auth/me rejects anonymous",
            status == 401,
            f"HTTP {status}",
        )
    except Exception as error:  # noqa: BLE001
        report.add(
            "/api/auth/me rejects anonymous",
            False,
            f"{type(error).__name__}: {error}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check a deployed Xingjian instance."
    )
    parser.add_argument("host", help="hostname, e.g. calhub.asia")
    parser.add_argument("--scheme", default="https", choices=["https", "http"])
    parser.add_argument("--port", type=int, default=0, help="override 443/80")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", help="machine readable")
    args = parser.parse_args()

    host = args.host.strip().lower()
    port = args.port or (443 if args.scheme == "https" else 80)
    base = f"{args.scheme}://{host}" + (f":{port}" if args.port else "")
    report = Report()

    print(f"\n== {host} ==")
    print("\n[1] name resolution")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False

    addresses = system_dns(host)
    report.add(
        "system DNS resolves",
        bool(addresses),
        ", ".join(addresses) or "NXDOMAIN / no A record",
    )
    if is_ip:
        report.add(
            "public DNS lookup", True, "skipped (host is an IP)",
            required=False,
        )
    else:
        doh, detail = doh_lookup(host)
        report.add(
            "public DNS resolves",
            bool(doh),
            ", ".join(doh) if doh else detail,
            required=False,
        )

    if args.scheme == "https":
        print("\n[2] transport")
        try:
            with socket.create_connection((host, port), timeout=args.timeout):
                report.add(f"TCP {port} reachable", True)
        except OSError as error:
            report.add(f"TCP {port} reachable", False, str(error))
        ok, detail = tls_check(host, args.timeout, port)
        report.add("TLS certificate valid", ok, detail)

        if args.port:
            report.add(
                "http redirects to https",
                True,
                "skipped (custom port)",
                required=False,
            )
        else:
            try:
                status_http, headers, _ = fetch(
                    f"http://{host}/", timeout=args.timeout
                )
                location = headers.get("Location", "")
                report.add(
                    "http redirects to https",
                    status_http in (301, 302, 307, 308)
                    and "https" in location,
                    f"{status_http} -> {location}",
                )
            except Exception as error:  # noqa: BLE001
                report.add("http redirects to https", False, str(error))

    print("\n[3] application")
    check_app(report, base, args.timeout)

    failures = report.failures
    if args.json:
        print(
            json.dumps(
                {
                    "host": host,
                    "passed": report.passed,
                    "total": len(report.rows),
                    "checks": [
                        {
                            "name": name,
                            "ok": ok,
                            "required": required,
                            "detail": detail,
                        }
                        for name, ok, detail, required in report.rows
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif failures:
        print(
            f"\n{report.passed}/{len(report.rows)} checks passed, "
            f"{len(failures)} failed"
        )
        for name, _, detail, _ in failures:
            print(f"  - {name}: {detail}")
    else:
        print(f"\nALL CHECKS PASSED ({report.passed}/{len(report.rows)})")
        print(f"  {base} is live")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
