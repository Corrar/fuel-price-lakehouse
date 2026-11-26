"""A fake ANP site and SGS API, so no test touches the network."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime

import httpx
import pytest

from fuel_price_lakehouse.collect import anp
from fuel_price_lakehouse.collect.http import RetryPolicy

FILES = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc"
SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs"

HEADER = (
    "Regiao - Sigla;Estado - Sigla;Municipio;Revenda;CNPJ da Revenda;Nome da Rua;Numero Rua;"
    "Complemento;Bairro;Cep;Produto;Data da Coleta;Valor de Venda;Valor de Compra;"
    "Unidade de Medida;Bandeira"
)


def anp_csv(*prices: str) -> bytes:
    """An ANP-shaped CSV: BOM, ``;``, CRLF, padded CNPJ, a quoted ``;``, empty fields."""
    rows = [
        f'SE;SP;SAO PAULO;POSTO {n} LTDA; 01.492.748/0003-83;RUA A;S/N;"LOTE: 04;";'
        f"CENTRO;01000-000;GASOLINA;05/10/2026;{price};;R$ / litro;BRANCA"
        for n, price in enumerate(prices, start=1)
    ]
    return ("﻿" + "\r\n".join([HEADER, *rows]) + "\r\n").encode("utf-8")


def zipped(content: bytes, name: str = "Pre\x87os semestrais - AUTOMOTIVOS_2026.01.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


PAGE = """
<html><body><div id="content">
<span>Atualizado em 23/11/2026 15h47</span>
<ul><li><a href="{files}/metadados-serie-historica-precos-combustiveis-1.pdf">Metadados
(atualizado em 6/3/2026)</a></li></ul>
<h2>Dados semanais agrupados por semestre</h2>
<h3>Combust&iacute;veis automotivos</h3>
<ul>
<li><a href="{files}/dsas/ca/ca-2026-01.zip">1&ordm; semestre de 2026</a></li>
<li><a href="{files}/dsas/ca/ca-2025-02.zip">2&deg; semestre de 2025 </a></li>
<li><a href="{files}/dsas/ca/precos-semestrais-ca.zip">1&ordm; semestre de 2022</a></li>
</ul>
<h3>GLP P13</h3>
<ul><li><a href="{files}/dsas/glp/glp-2026-01.csv">1&ordm; semestre de 2026</a></li></ul>
<h2>Dados semanais agrupados mensalmente</h2>
<h3>&Oacute;leo Diesel (S-500 e S-10) + GNV</h3>
<ul>
<li><a href="{files}/dsan/2026/10-diesel-gnv.csv">Outubro de 2026</a></li>
<li><a href="{files}/dsan/2026/03-dados-abertos-precos-diesel-gnv.csv">Mar&ccedil;o de 2026</a></li>
</ul>
<h3>Etanol Hidratado + Gasolina C</h3>
<ul>
<li><a href="{files}/dsan/2026/10-gasolina-etanol.csv">Outubro de 2026</a></li>
<li><a href="{files}/dsan/2026/04-dados-abertos-precos-gasolina-etanol">Abril de 2026</a></li>
<li><a href="{files}/dsan/2026/02-cados-abertos-preco-gasolina-etanol.csv">Fevereiro
de 2026</a></li>
</ul>
<h3>GLP P13</h3>
<ul>
<li><a href="{files}/dsan/2026/10-glp.csv">Outubro de 2026</a></li>
<li><a href="{files}/dsan/2026/novo-arquivo.csv">Arquivo sem periodo</a></li>
</ul>
<h2>Quatro &uacute;ltimas semanas</h2>
<p> (atualizado em {updated})</p>
<ul>
<li><a href="{files}/qus/ultimas-4-semanas-gasolina-etanol.csv"> Etanol Hidratado +
Gasolina C </a></li>
</ul>
</div><footer><a href="/anp/pt-br">ANP</a></footer></body></html>
"""

# What `make extract SINCE=2026-07` should collect from the page above.
OCTOBER_DIESEL = f"{FILES}/dsan/2026/10-diesel-gnv.csv"
OCTOBER_GASOLINE = f"{FILES}/dsan/2026/10-gasolina-etanol.csv"
OCTOBER_LPG = f"{FILES}/dsan/2026/10-glp.csv"
FOUR_WEEKS = f"{FILES}/qus/ultimas-4-semanas-gasolina-etanol.csv"
SEMESTER_ZIP = f"{FILES}/dsas/ca/ca-2026-01.zip"


class FakeWeb:
    """Serves the fake sources and records every request it gets."""

    def __init__(self) -> None:
        self.updated = "23/11/2026"
        self.files: dict[str, bytes] = {
            SEMESTER_ZIP: zipped(anp_csv("5,99", "6,09", "6,19")),
            f"{FILES}/dsas/ca/ca-2025-02.zip": zipped(anp_csv("5,50")),
            f"{FILES}/dsas/ca/precos-semestrais-ca.zip": zipped(anp_csv("7,00")),
            f"{FILES}/dsas/glp/glp-2026-01.csv": anp_csv("110,00"),
            OCTOBER_DIESEL: anp_csv("6,01", "6,02"),
            OCTOBER_GASOLINE: anp_csv("6,29", "6,39"),
            OCTOBER_LPG: anp_csv("109,99"),
            FOUR_WEEKS: anp_csv("6,30", "6,40", "6,50"),
        }
        self.sgs: dict[tuple[int, int], bytes] = {
            (
                1,
                2026,
            ): b'[{"data":"02/01/2026","valor":"5.4321"},{"data":"05/01/2026","valor":"5.5000"}]',
            (433, 2026): b'[{"data":"01/01/2026","valor":"0.16"}]',
        }
        self.failures: dict[str, list[BaseException | int]] = {}
        """Queued per URL: an exception to raise or a status to answer, once each."""
        self.truncate: set[str] = set()
        self.unknown_series: set[int] = set()
        self.requests: list[tuple[str, str]] = []
        """(kind, url) with kind in ``page``, ``probe``, ``download``, ``sgs``."""

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def count(self, kind: str) -> int:
        return sum(1 for seen, _ in self.requests if seen == kind)

    def downloads(self) -> list[str]:
        return [url for kind, url in self.requests if kind == "download"]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        queued = self.failures.get(url)
        if queued:
            failure = queued.pop(0)
            self.requests.append(("failed", url))
            if isinstance(failure, BaseException):
                raise failure
            return httpx.Response(failure)
        if url == anp.LANDING_PAGE:
            self.requests.append(("page", url))
            return httpx.Response(200, text=PAGE.format(files=FILES, updated=self.updated))
        if url.startswith(SGS):
            return self._sgs(request, url)
        if url not in self.files:
            return httpx.Response(404)
        content = self.files[url]
        if request.headers.get("Range") == "bytes=0-0":
            self.requests.append(("probe", url))
            headers = {"Content-Range": f"bytes 0-0/{len(content)}"}
            return httpx.Response(206, content=content[:1], headers=headers)
        self.requests.append(("download", url))
        if url in self.truncate:
            self.truncate.discard(url)
            return httpx.Response(
                200, content=content[:10], headers={"Content-Length": str(len(content))}
            )
        return httpx.Response(200, content=content)

    def _sgs(self, request: httpx.Request, url: str) -> httpx.Response:
        self.requests.append(("sgs", url))
        match = re.search(r"sgs\.(\d+)/dados\?formato=json&dataInicial=01/01/(\d{4})", url)
        if int(match.group(1)) in self.unknown_series:
            return httpx.Response(404, text="A serie nao existe!")
        body = self.sgs.get((int(match.group(1)), int(match.group(2))))
        if body is None:
            return httpx.Response(404, json={"erro": {"detail": "Value(s) not found"}})
        etag = f'W/"{hashlib.sha256(body).hexdigest()[:16]}"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        return httpx.Response(200, content=body, headers={"ETag": etag})


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def fast() -> dict:
    """Run options that never really sleep and use a fixed clock."""
    waits: list[float] = []
    return {
        "sleep": waits.append,
        "retry": RetryPolicy(attempts=3, initial_wait=2.0, max_wait=60.0),
        "now": lambda: datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
        "waits": waits,
    }
