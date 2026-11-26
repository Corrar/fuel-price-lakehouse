import pytest

from fuel_price_lakehouse.manifest import Entry, Manifest


def entry(**overrides) -> Entry:
    values = {
        "source": "anp",
        "dataset": "lpg",
        "period": "2026-10",
        "url": "https://example.test/10-glp.csv",
        "filename": "10-glp.csv",
        "size_bytes": 100,
        "sha256": "a" * 64,
        "raw_path": "raw/anp/lpg/10-glp.csv",
        "fetched_at": "2026-11-26T01:00:00+00:00",
        "details": {"grain": "month"},
    }
    return Entry(**(values | overrides))


def test_a_missing_file_is_an_empty_manifest(tmp_path):
    assert Manifest.load(tmp_path / "nope.jsonl").entries == ()


def test_entries_survive_a_save_and_load(tmp_path):
    path = tmp_path / "manifest" / "collection.jsonl"
    manifest = Manifest.load(path)
    manifest.add(entry())
    manifest.add(entry(filename="posto-são-joão.csv", url="https://example.test/b"))
    manifest.save()

    assert Manifest.load(path).entries == manifest.entries
    assert "são" in path.read_text(encoding="utf-8")  # readable, not \u-escaped
    assert not path.with_suffix(".jsonl.tmp").exists()


def test_latest_returns_the_most_recent_version_of_a_url(tmp_path):
    manifest = Manifest(tmp_path / "m.jsonl")
    manifest.add(entry(sha256="b" * 64, fetched_at="2026-11-30T00:00:00+00:00"))
    manifest.add(entry(sha256="a" * 64, fetched_at="2026-11-23T00:00:00+00:00"))

    assert manifest.latest("https://example.test/10-glp.csv").sha256 == "b" * 64
    assert manifest.latest("https://example.test/other") is None


def test_has_matches_on_url_and_content_together(tmp_path):
    manifest = Manifest(tmp_path / "m.jsonl", [entry()])

    assert manifest.has("https://example.test/10-glp.csv", "a" * 64)
    assert not manifest.has("https://example.test/10-glp.csv", "c" * 64)
    assert not manifest.has("https://example.test/other", "a" * 64)


def test_for_dataset_filters_by_source_and_dataset(tmp_path):
    manifest = Manifest(
        tmp_path / "m.jsonl",
        [entry(), entry(source="bcb_sgs", dataset="sgs_1"), entry(dataset="automotive")],
    )

    assert [e.dataset for e in manifest.for_dataset("anp", "lpg")] == ["lpg"]


def test_a_corrupt_line_is_reported_with_its_line_number(tmp_path):
    path = tmp_path / "m.jsonl"
    Manifest(path, [entry()]).save()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(ValueError, match=r"m\.jsonl:2"):
        Manifest.load(path)
