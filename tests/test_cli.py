"""The command line: parsing, and a whole offline run on a collected history."""

from __future__ import annotations

import json
from datetime import date

import pytest

from fuel_price_lakehouse import cli
from fuel_price_lakehouse.collect.run import run as collect
from fuel_price_lakehouse.stage.cnpj import has_valid_check_digits

from .conftest import FOUR_WEEKS, OCTOBER_DIESEL, OCTOBER_GASOLINE, OCTOBER_LPG, anp_csv


def valid_cnpj(base: str) -> str:
    """A CNPJ with correct check digits for a 12-digit base (synthetic, not a real station)."""
    return next(
        f"{base}{check:02d}" for check in range(100) if has_valid_check_digits(f"{base}{check:02d}")
    )


def three_stations() -> bytes:
    rows = anp_csv("6,10", "6,20", "6,30").decode("utf-8").split("\r\n")
    for index, base in enumerate(("112223330001", "114447770001", "119998880001"), start=1):
        rows[index] = rows[index].replace("11.222.333/0001-81", valid_cnpj(base))
    return "\r\n".join(rows).encode("utf-8")


def test_since_must_be_year_month():
    args = cli.build_parser().parse_args(["extract", "--since", "2026-07"])
    assert args.since == date(2026, 7, 1)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["extract", "--since", "07/2026"])


def test_every_step_has_a_subcommand():
    parser = cli.build_parser()
    for command in ("run", "extract", "stage", "curate", "report", "site", "docs"):
        assert parser.parse_args([command]).handler is not None


def test_offline_run_builds_the_site_and_writes_the_summary(web, tmp_path, fast):
    root, site, summary = tmp_path / "data", tmp_path / "site", tmp_path / "summary.md"
    # Enough distinct stations for a public cell (small cells are suppressed).
    for url in (OCTOBER_DIESEL, OCTOBER_GASOLINE, OCTOBER_LPG, FOUR_WEEKS):
        web.files[url] = three_stations()
    with web.client() as client:
        settings = {key: fast[key] for key in ("sleep", "retry", "now")}
        collect(root, date(2026, 7, 1), client=client, **settings)

    status = cli.main(
        [
            *("--root", str(root), "run", "--offline", "--since", "2026-07"),
            *("--site", str(site), "--summary", str(summary), "--code-version", "abc1234"),
        ]
    )

    # The fake history is old by now, so freshness checks warn; warnings do not close the gate.
    assert status == cli.OK
    report = json.loads((root / "quality" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] in {"pass", "warn"}
    assert report["run"]["code_version"] == "abc1234"
    assert (site / "data" / "meta.json").is_file()
    assert (site / ".nojekyll").is_file()
    assert summary.read_text(encoding="utf-8").startswith("## Data quality")


def test_failed_quality_gate_publishes_nothing(web, tmp_path, fast):
    root, site = tmp_path / "data", tmp_path / "site"
    # Nothing was ever collected: the report fails, so the site must not be built.
    status = cli.main(["--root", str(root), "run", "--offline", "--site", str(site)])

    assert status == cli.QUALITY_GATE_FAILED
    assert not site.exists()
