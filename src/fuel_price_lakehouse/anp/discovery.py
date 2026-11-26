"""Find out which files ANP currently publishes, and which of them to fetch.

File names on the source are irregular (see ``docs/sources.md``), so nothing
here builds a URL from a date. The landing page is the only catalogue: every
link under the data directory is read, and the period comes from the link
text, with the URL as a fallback.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

LANDING_URL = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "serie-historica-de-precos-de-combustiveis"
)
DATA_PATH_MARKER = "/arquivos/shpc/"


class Grain(StrEnum):
    SEMESTER = "semester"
    MONTH = "month"
    LAST_4_WEEKS = "last-4-weeks"


class Dataset(StrEnum):
    AUTOMOTIVE = "automotive"
    """Semester files with every automotive fuel together."""
    DIESEL_CNG = "diesel_cng"
    GASOLINE_ETHANOL = "gasoline_ethanol"
    LPG = "lpg"


@dataclass(frozen=True)
class RemoteFile:
    url: str
    filename: str
    dataset: Dataset
    grain: Grain
    period: str
    """``2026-S1``, ``2026-10`` or ``last-4-weeks``."""
    start: date | None
    end: date | None
    """First and last day the file can cover. ``None`` for the rolling file."""


@dataclass(frozen=True)
class Catalogue:
    files: tuple[RemoteFile, ...]
    unclassified: tuple[str, ...]
    """Data links whose dataset or period could not be worked out. Never silently dropped."""


_MONTH_NAMES = (
    "janeiro",
    "fevereiro",
    "marco",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
_MONTHS = {name: number for number, name in enumerate(_MONTH_NAMES, start=1)}
_SEMESTER_TEXT = re.compile(r"\b([12])\D{0,3}semestre\s+de\s+(\d{4})\b")
_MONTH_TEXT = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+de\s+(\d{4})\b")
_SEMESTER_URL = re.compile(r"(\d{4})-0([12])(?:\.\w+)?$")
_MONTH_URL = re.compile(r"/dsan/(\d{4})/(?:(\d{2})-[^/]*|[^/]*-(\d{2})(?:\.\w+)?)$")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text)))
            self._href = None


def _normalise(text: str) -> str:
    """Lower case, no accents, single spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


def _semester(year: int, half: int) -> tuple[str, date, date]:
    start = date(year, 1 if half == 1 else 7, 1)
    end = date(year, 6, 30) if half == 1 else date(year, 12, 31)
    return f"{year}-S{half}", start, end


def _month(year: int, month: int) -> tuple[str, date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}", date(year, month, 1), date(year, month, last_day)


def _monthly_dataset(filename: str) -> Dataset | None:
    name = filename.lower()
    if "diesel" in name or "gnv" in name:
        return Dataset.DIESEL_CNG
    if "gasolina" in name or "etanol" in name:
        return Dataset.GASOLINE_ETHANOL
    if "glp" in name:
        return Dataset.LPG
    return None


def _classify(url: str, text: str) -> RemoteFile | None:
    path = unquote(urlsplit(url).path)
    relative = path.split(DATA_PATH_MARKER, 1)[1]
    filename = relative.rsplit("/", 1)[-1]
    label = _normalise(text)

    if relative.startswith("qus/"):
        dataset = _monthly_dataset(filename)
        if dataset is None:
            return None
        return RemoteFile(
            url, filename, dataset, Grain.LAST_4_WEEKS, Grain.LAST_4_WEEKS.value, None, None
        )

    if relative.startswith("dsas/"):
        if relative.startswith("dsas/ca/"):
            dataset = Dataset.AUTOMOTIVE
        elif relative.startswith("dsas/glp/"):
            dataset = Dataset.LPG
        else:
            return None
        if found := _SEMESTER_TEXT.search(label):
            half, year = int(found.group(1)), int(found.group(2))
        elif found := _SEMESTER_URL.search(filename):
            year, half = int(found.group(1)), int(found.group(2))
        else:
            return None
        return RemoteFile(url, filename, dataset, Grain.SEMESTER, *_semester(year, half))

    if relative.startswith("dsan/"):
        dataset = _monthly_dataset(filename)
        if dataset is None:
            return None
        if found := _MONTH_TEXT.search(label):
            year, month = int(found.group(2)), _MONTHS[found.group(1)]
        elif found := _MONTH_URL.search(path):
            year, month = int(found.group(1)), int(found.group(2) or found.group(3))
        else:
            return None
        if not 1 <= month <= 12:
            return None
        return RemoteFile(url, filename, dataset, Grain.MONTH, *_month(year, month))

    return None


def parse_landing_page(html: str, base_url: str = LANDING_URL) -> Catalogue:
    """Every data file linked from the landing page, classified."""
    parser = _LinkParser()
    parser.feed(html)
    files: dict[str, RemoteFile] = {}
    unclassified: list[str] = []
    for href, text in parser.links:
        url = urljoin(base_url, href.strip())
        if DATA_PATH_MARKER not in urlsplit(url).path or url.lower().endswith(".pdf"):
            continue
        classified = _classify(url, text)
        if classified is None:
            unclassified.append(url)
        else:
            files.setdefault(url, classified)
    return Catalogue(tuple(files.values()), tuple(dict.fromkeys(unclassified)))


_FAMILIES: tuple[tuple[Dataset, tuple[Dataset, ...]], ...] = (
    (Dataset.AUTOMOTIVE, (Dataset.DIESEL_CNG, Dataset.GASOLINE_ETHANOL)),
    (Dataset.LPG, (Dataset.LPG,)),
)


def select(files: tuple[RemoteFile, ...] | list[RemoteFile], since: date) -> list[RemoteFile]:
    """The smallest set of files that covers everything from ``since`` onwards.

    The same weeks are published three times: in semester files, in monthly
    files and in the rolling last-four-weeks file. For each fuel family this
    takes the semester files, then a monthly file only when no semester file
    covers that month, and always the rolling files, which hold the newest
    weeks.
    """
    chosen: list[RemoteFile] = []
    for semester_dataset, monthly_datasets in _FAMILIES:
        semesters = [
            f
            for f in files
            if f.grain is Grain.SEMESTER and f.dataset is semester_dataset and f.start and f.end
        ]
        chosen += [f for f in semesters if f.end >= since]
        chosen += [
            f
            for f in files
            if f.grain is Grain.MONTH
            and f.dataset in monthly_datasets
            and f.start
            and f.end
            and f.end >= since
            and not any(s.start <= f.start and f.end <= s.end for s in semesters)
        ]
    chosen.sort(key=lambda f: (f.start or date.max, f.dataset.value))
    chosen += sorted(
        (f for f in files if f.grain is Grain.LAST_4_WEEKS), key=lambda f: f.dataset.value
    )
    return chosen
