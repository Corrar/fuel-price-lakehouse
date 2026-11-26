from datetime import UTC, datetime

import pandas as pd
import pytest

from fuel_price_lakehouse.stage.rules import FileContext, Rule, Severity, evaluate

CONTEXT = FileContext(
    "k", "https://example.test/f.csv", "0" * 64, datetime(2026, 1, 1, tzinfo=UTC), {}
)


def rule(rule_id: str, severity: Severity, column: str) -> Rule:
    return Rule(rule_id, severity, "d", "d", lambda frame, _context: frame[column] > 0)


def test_rows_collect_every_rule_they_break():
    frame = pd.DataFrame({"a": [1, -1, -1], "b": [1, 1, -1], "c": [-1, 1, 1]})
    rules = [
        rule("a_positive", Severity.ERROR, "a"),
        rule("b_positive", Severity.ERROR, "b"),
        rule("c_positive", Severity.WARNING, "c"),
    ]

    result = evaluate(rules, frame, CONTEXT)

    assert result.error_ids.tolist() == [[], ["a_positive"], ["a_positive", "b_positive"]]
    assert result.warning_ids.tolist() == [["c_positive"], [], []]
    assert result.rejected.tolist() == [False, True, True]
    assert result.error_counts == {"a_positive": 2, "b_positive": 1}
    assert result.warning_counts == {"c_positive": 1}


def test_clean_rules_are_reported_with_zero():
    frame = pd.DataFrame({"a": [1, 2]})
    result = evaluate([rule("a_positive", Severity.ERROR, "a")], frame, CONTEXT)
    assert result.error_counts == {"a_positive": 0}


def test_index_is_preserved():
    frame = pd.DataFrame({"a": [1, -1]}, index=[10, 20])
    result = evaluate([rule("a_positive", Severity.ERROR, "a")], frame, CONTEXT)
    assert result.error_ids.index.tolist() == [10, 20]


def test_empty_frame():
    frame = pd.DataFrame({"a": pd.Series([], dtype="int64")})
    result = evaluate([rule("a_positive", Severity.ERROR, "a")], frame, CONTEXT)
    assert len(result.error_ids) == 0
    assert result.error_counts == {"a_positive": 0}


def test_duplicate_ids_are_rejected():
    frame = pd.DataFrame({"a": [1]})
    rules = [rule("same", Severity.ERROR, "a"), rule("same", Severity.WARNING, "a")]
    with pytest.raises(ValueError, match="duplicate rule ids"):
        evaluate(rules, frame, CONTEXT)


def test_rule_returning_non_boolean_is_a_bug():
    frame = pd.DataFrame({"a": [1]})
    broken = Rule("broken", Severity.ERROR, "d", "d", lambda f, _c: f["a"])
    with pytest.raises(TypeError, match="broken"):
        evaluate([broken], frame, CONTEXT)


def test_rule_returning_rows_in_another_order_is_a_bug():
    frame = pd.DataFrame({"a": [1, -1]})
    shuffled = Rule("shuffled", Severity.ERROR, "d", "d", lambda f, _c: (f["a"] > 0).iloc[::-1])
    with pytest.raises(TypeError, match="aligned"):
        evaluate([shuffled], frame, CONTEXT)
