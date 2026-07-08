from schemascope.tokens import TokenAccumulator, _bin_index, _percentile


def test_clinical_never_exceeds_full():
    acc = TokenAccumulator()
    acc.add('{"a":"hello world","id":"P1"}', "hello world")
    r = acc.result()
    assert r["total_tokens"] > 0
    assert 0 < r["clinical_content_tokens"] <= r["total_tokens"]
    assert r["structure_tokens"] == r["total_tokens"] - r["clinical_content_tokens"]


def test_dual_encoder_both_counted():
    acc = TokenAccumulator()
    acc.add("The quick brown fox jumps over the lazy dog.", "quick brown fox")
    r = acc.result()
    assert r["tokeniser"] == "tiktoken o200k_base"
    assert r["tokeniser_cross_check"] == "tiktoken cl100k_base"
    assert r["total_tokens_cross_check"] > 0


def test_bins_and_percentiles():
    acc = TokenAccumulator()
    # three patients with increasing size
    for text in ("a", "a b c " * 100, "a b c " * 5000):
        acc.add(text, text)
    r = acc.result()
    assert sum(b["patients"] for b in r["token_distribution"]["bins"]) == 3
    p = r["token_distribution"]["percentiles"]
    assert p["p50"] <= p["p90"] <= p["p99"]
    assert r["token_distribution"]["min_tokens_per_patient"] <= r["token_distribution"]["max_tokens_per_patient"]


def test_empty_clinical_is_zero():
    acc = TokenAccumulator()
    acc.add('{"id":"P1"}', "")           # structure only, no clinical content
    r = acc.result()
    assert r["clinical_content_tokens"] == 0
    assert r["clinical_content_pct"] == 0.0


def test_bin_index_edges():
    assert _bin_index(0) == 0
    assert _bin_index(999) == 0
    assert _bin_index(1000) == 1
    assert _bin_index(50_000) == 6
    assert _bin_index(9_000_000) == 11


def test_percentile_monotone():
    vals = list(range(1, 101))
    assert _percentile(vals, 0.5) <= _percentile(vals, 0.9) <= _percentile(vals, 0.99)
    assert _percentile([], 0.5) is None
