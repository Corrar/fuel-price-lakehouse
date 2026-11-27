"""The TLS settings of the HTTP client the collectors use."""

from __future__ import annotations

import ssl

from fuel_price_lakehouse.collect import http


def test_tls_context_still_checks_certificates_and_host_names():
    context = http.tls_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED  # TLS 1.3 kept


def test_tls_context_offers_only_the_classic_key_exchange(monkeypatch):
    chosen: list[str] = []

    class Recording(ssl.SSLContext):
        def set_ecdh_curve(self, name: str) -> None:
            chosen.append(name)
            super().set_ecdh_curve(name)

    monkeypatch.setattr(
        http.httpx, "create_ssl_context", lambda: Recording(ssl.PROTOCOL_TLS_CLIENT)
    )

    http.tls_context()

    assert chosen == ["prime256v1"]  # accepted by both www.gov.br and Banco Central


def test_default_client_uses_the_tls_context(monkeypatch):
    sentinel = ssl.create_default_context()
    monkeypatch.setattr(http, "tls_context", lambda: sentinel)
    seen: dict = {}
    real = http.httpx.Client

    def client(**options):
        seen.update(options)
        return real(**options)

    monkeypatch.setattr(http.httpx, "Client", client)

    with http.default_client():
        pass

    assert seen["verify"] is sentinel
