from datetime import date

import pytest

from fuel_price_lakehouse import cli
from fuel_price_lakehouse.report import Outcome, Report, Status


def test_collect_defaults():
    args = cli.build_parser().parse_args(["collect"])

    assert args.source == "all"
    assert args.since == date(2022, 1, 1)
    assert not args.dry_run and not args.force


def test_since_must_be_a_month():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["collect", "--since", "2022"])


def test_exit_code_reflects_failures(monkeypatch, capsys):
    report = Report([Outcome("anp", "lpg", "2026-10", Status.FAILED, detail="HTTP 503")])
    monkeypatch.setattr(cli, "run_collect", lambda args: report)

    assert cli.main(["collect", "--dry-run"]) == 1
    assert "1 failed" in capsys.readouterr().out


def test_exit_code_is_zero_when_everything_worked(monkeypatch, capsys):
    report = Report([Outcome("anp", "lpg", "2026-10", Status.DOWNLOADED, 1234, "10-glp.csv")])
    monkeypatch.setattr(cli, "run_collect", lambda args: report)

    assert cli.main(["collect"]) == 0
    assert "1,234 bytes downloaded" in capsys.readouterr().out
