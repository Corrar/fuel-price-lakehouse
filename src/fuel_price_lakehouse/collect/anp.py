"""ANP collector: station-level fuel price files, discovered from the landing page.

File names on the ANP site are irregular (five shapes in 2026 alone, one without
an extension), so nothing here builds a URL from a date. Every run reads the
landing page, takes the links it finds, and learns the period of each file from
the link text and the section it sits in.

The site sends no ``ETag`` or ``Last-Modified`` and refuses ``HEAD``. A file is
considered unchanged when its size (one-byte range request) still matches the
manifest; anything else is downloaded and compared by SHA-256. The rolling
"last four weeks" files are also checked against the "atualizado em" date shown
on the page, because their size alone could repeat by chance.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from fuel_price_lakehouse.collect.http import Fetcher, SourceError, TransientError
from fuel_price_lakehouse.collect.manifest import FileRecord, Manifest, Outcome
from fuel_price_lakehouse.collect.raw import CorruptFileError, Provenance, write_anp_csv
from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.layout import Layer, Source, downloads_dir, partition_dir

LANDING_PAGE = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "serie-historica-de-precos-de-combustiveis"
)
DATASET = "fuel_prices"

logger = logging.getLogger(__name__)

_FILES_MARKER = "/arquivos/shpc/"
_NOT_DATA = (".pdf",)

SEMESTER = "semester"
MONTHLY = "monthly"
LAST_FOUR_WEEKS = "last_four_weeks"

# A semester file of the key group holds the fuels of these monthly groups.
_SEMESTER_COVERS = {
    "automotive": {"diesel_cng", "gasoline_ethanol"},
    "lpg": {"lpg"},
}

_MONTH_NAMES = (
    "janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro"
)
_MONTHS = {name: number for number, name in enumerate(_MONTH_NAMES.split(), start=1)}
_SEMESTER_LABEL = re.compile(r"\b([12])\s*[o°]?\s*semestre de (\d{4})\b")
_MONTH_LABEL = re.compile(r"\b(" + "|".join(_MONTHS) + r") de (\d{4})\b")
_UPDATED_ON = re.compile(r"atualizado em (\d{1,2}/\d{1,2}/\d{4})")


@dataclass(frozen=True)
class AnpFile:
    """One data file linked from the landing page."""

    url: str
    kind: str
    fuel_group: str
    period: str
    """``2026-S1``, ``2026-10`` or ``latest``."""
    label: str
    period_start: date | None = None
    period_end: date | None = None
    published_hint: str = ""
    """The "atualizado em" date of the section, when the page shows one."""

    @property
    def details(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "fuel_group": self.fuel_group,
            "period": self.period,
            "label": self.label,
            "published_hint": self.published_hint,
        }


@dataclass
class Discovery:
    files: list[AnpFile] = field(default_factory=list)
    unrecognised: list[str] = field(default_factory=list)
    """Links to files under the dataset folder that could not be classified."""


def discover(html: str, base_url: str = LANDING_PAGE) -> Discovery:
    """Find the data files linked from the landing page."""
    parser = _LandingPage(base_url)
    parser.feed(html)
    parser.close()
    discovery = parser.discovery
    if not discovery.files:
        raise SourceError(f"{base_url}: no data files found; the page layout may have changed")
    return discovery


def select(files: list[AnpFile], since: date) -> list[AnpFile]:
    """Choose what to collect for a history starting at ``since``.

    - semester files whose period reaches ``since``;
    - monthly files from ``since`` on, only for months no semester file covers
      yet (ANP publishes the months first and the semester later);
    - the last-four-weeks files, always.

    Oldest first, so an interrupted first load resumes in a predictable order.
    """
    covered = {
        (monthly_group, file.period_start.year, month)
        for file in files
        if file.kind == SEMESTER and file.period_start and file.period_end
        for monthly_group in _SEMESTER_COVERS.get(file.fuel_group, ())
        for month in range(file.period_start.month, file.period_end.month + 1)
    }
    chosen = []
    for file in files:
        if file.kind == LAST_FOUR_WEEKS:
            chosen.append(file)
        elif file.period_end is None or file.period_end < since:
            continue
        elif (
            file.kind == SEMESTER
            or (file.fuel_group, file.period_end.year, file.period_end.month) not in covered
        ):
            chosen.append(file)
    order = {SEMESTER: 0, MONTHLY: 1, LAST_FOUR_WEEKS: 2}
    return sorted(chosen, key=lambda f: (order[f.kind], f.period_start or date.max, f.fuel_group))


def raw_path(file: AnpFile, root: Path) -> Path:
    directory = partition_dir(
        Layer.RAW,
        Source.ANP,
        DATASET,
        root,
        kind=file.kind,
        fuel_group=file.fuel_group,
        period=file.period,
    )
    # The URL hash keeps two links that land in the same partition apart.
    return directory / f"{hashlib.sha256(file.url.encode()).hexdigest()[:12]}.parquet"


def collect(
    fetcher: Fetcher,
    manifest: Manifest,
    root: Path,
    since: date,
    *,
    now: Clock,
    dry_run: bool = False,
    full_check: bool = False,
) -> list[Outcome]:
    """Bring the raw layer up to date with the ANP site.

    ``full_check`` downloads every selected file again and compares hashes,
    which also catches a file republished with exactly the same size.
    """
    try:
        page = fetcher.get(LANDING_PAGE)
        discovery = discover(page.text)
    except (TransientError, SourceError) as error:
        return [Outcome(LANDING_PAGE, "failed", detail=str(error))]
    for url in discovery.unrecognised:
        logger.warning("anp: link not recognised, skipped: %s", url)

    outcomes = []
    for file in select(discovery.files, since):
        try:
            outcome = _collect_file(fetcher, manifest, root, file, now, dry_run, full_check)
        except (TransientError, SourceError, CorruptFileError) as error:
            if not dry_run:
                at = now().isoformat()
                manifest.record_failure(
                    file.url, Source.ANP, file.url, str(error), at, file.details
                )
            outcome = Outcome(file.url, "failed", detail=str(error))
        if not dry_run:
            manifest.save()  # after every file: a run that dies keeps what it finished
        outcomes.append(outcome)
        logger.info(
            "anp: %-9s %s %s %s %s",
            outcome.action,
            file.kind,
            file.fuel_group,
            file.period,
            outcome.detail,
        )
    return outcomes


def _collect_file(
    fetcher: Fetcher,
    manifest: Manifest,
    root: Path,
    file: AnpFile,
    now: Clock,
    dry_run: bool,
    full_check: bool,
) -> Outcome:
    record = manifest.get(file.url)
    previous = record if record is not None and record.ingested else None
    size = fetcher.remote_size(file.url)
    if (
        previous is not None
        and not full_check
        and previous.size_bytes == size
        and previous.details.get("published_hint", "") == file.published_hint
    ):
        return Outcome(file.url, "unchanged")
    if dry_run:
        return Outcome(file.url, "planned", size_bytes=size, detail=f"{size:,} bytes")

    downloaded = downloads_dir(root) / hashlib.sha256(file.url.encode()).hexdigest()[:12]
    try:
        download = fetcher.download(file.url, downloaded)
        if previous is not None and previous.sha256 == download.sha256:
            # Same content after all. Remember the new hint so it is not fetched again.
            previous.details = {**previous.details, **file.details}
            return Outcome(file.url, "unchanged", download.size_bytes, detail="same hash")
        collected_at = now()
        destination = raw_path(file, root)
        copy = write_anp_csv(
            downloaded, destination, Provenance(file.url, download.sha256, collected_at)
        )
    finally:
        downloaded.unlink(missing_ok=True)

    manifest.put(
        FileRecord(
            key=file.url,
            source=Source.ANP,
            url=file.url,
            status="ok",
            collected_at=collected_at.isoformat(),
            size_bytes=download.size_bytes,
            sha256=download.sha256,
            rows=copy.rows,
            raw_path=destination.relative_to(root).as_posix(),
            revision=(record.revision if record else 0) + 1,
            details={
                **file.details,
                "encoding": copy.encoding,
                "csv_parsing": "lenient" if copy.lenient else "strict",
            },
            superseded=previous.history_with_current() if previous is not None else [],
        )
    )
    action = "updated" if previous is not None else "new"
    return Outcome(file.url, action, download.size_bytes, copy.rows, f"{copy.rows:,} rows")


class _LandingPage(HTMLParser):
    """Walks the page keeping track of the section each link sits in."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.discovery = Discovery()
        self._base_url = base_url
        self._heading: str | None = None  # tag of the heading being read
        self._section = ""  # text of the current <h2>
        self._subsection = ""  # text of the current <h3>
        self._hint = ""
        self._href: str | None = None
        self._text: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h2", "h3"):
            self._heading, self._text = tag, []
        elif tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data: str) -> None:
        if self._heading or self._href is not None:
            self._text.append(data)
        elif self._section:
            match = _UPDATED_ON.search(_plain(data))
            if match:
                self._hint = match.group(1)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._heading:
            text = _plain("".join(self._text))
            if tag == "h2":
                self._section, self._subsection, self._hint = text, "", ""
            else:
                self._subsection = text
            self._heading = None
        elif tag == "a" and self._href is not None:
            self._link(urljoin(self._base_url, self._href), " ".join("".join(self._text).split()))
            self._href = None

    def _link(self, url: str, label: str) -> None:
        if _FILES_MARKER not in url or url.lower().endswith(_NOT_DATA) or url in self._seen:
            return
        self._seen.add(url)
        file = _classify(url, label, self._section, self._subsection, self._hint)
        if file is None:
            self.discovery.unrecognised.append(url)
        else:
            self.discovery.files.append(file)


def _classify(url: str, label: str, section: str, subsection: str, hint: str) -> AnpFile | None:
    plain_label = _plain(label)
    if "ultimas semanas" in section:
        group = _fuel_group(plain_label)
        if group is None:
            return None
        return AnpFile(url, LAST_FOUR_WEEKS, group, "latest", label, published_hint=hint)
    group = _fuel_group(subsection)
    if group is None:
        return None
    if "semestre" in section:
        match = _SEMESTER_LABEL.search(plain_label)
        if not match:
            return None
        half, year = int(match.group(1)), int(match.group(2))
        start = date(year, 1 if half == 1 else 7, 1)
        end = date(year, 6, 30) if half == 1 else date(year, 12, 31)
        return AnpFile(url, SEMESTER, group, f"{year}-S{half}", label, start, end, hint)
    if "mensal" in section:
        match = _MONTH_LABEL.search(plain_label)
        if not match:
            return None
        month, year = _MONTHS[match.group(1)], int(match.group(2))
        start = date(year, month, 1)
        end = (start + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        return AnpFile(url, MONTHLY, group, f"{year}-{month:02d}", label, start, end, hint)
    return None


def _fuel_group(text: str) -> str | None:
    if "glp" in text:
        return "lpg"
    if "diesel" in text:
        return "diesel_cng"
    if "gasolina" in text or "etanol" in text:
        return "gasoline_ethanol"
    if "automotiv" in text:
        return "automotive"
    return None


def _plain(text: str) -> str:
    """Lower case, no accents, single spaces: page text is not typed consistently."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.lower().split())
