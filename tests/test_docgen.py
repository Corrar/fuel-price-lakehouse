from pathlib import Path

import pytest

from fuel_price_lakehouse import docgen

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", sorted(docgen.DOCUMENTS))
def test_committed_document_matches_the_code(relative):
    committed = (REPOSITORY / relative).read_text(encoding="utf-8")
    assert committed == docgen.DOCUMENTS[relative](), f"{relative} is stale: run `make docs`"


def test_every_rule_appears_once_in_the_rules_document():
    from fuel_price_lakehouse.stage import anp, bcb_sgs

    document = docgen.quality_rules()
    for rule in (*anp.RULES, *bcb_sgs.RULES):
        assert document.count(f"| `{rule.id}` |") == 1
