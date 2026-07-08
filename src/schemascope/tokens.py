"""Exact token counting with tiktoken.

Every patient record is tokenised twice on two axes:

* **full-record** vs **clinical-content** (per :mod:`schemascope.model`), and
* **o200k_base** (primary) vs **cl100k_base** (independent cross-check).

The accumulator is *streaming*: it takes one patient at a time and keeps only
running totals plus a compact per-patient array for the distribution, so an exact
pass over a billion-token corpus never loads more than one record at once.
"""

from __future__ import annotations

from array import array
from typing import Dict, List, Optional

import tiktoken

PRIMARY = "o200k_base"
SECONDARY = "cl100k_base"

# Tokens-per-patient bins — exactly the ladder in the intake template.
_BIN_LABELS = ["<1k", "1k-3k", "3k-5k", "5k-10k", "10k-25k", "25k-50k",
               "50k-100k", "100k-250k", "250k-500k", "500k-1M", "1M-5M", "5M+"]
_BIN_UPPER = [1_000, 3_000, 5_000, 10_000, 25_000, 50_000,
              100_000, 250_000, 500_000, 1_000_000, 5_000_000, float("inf")]


def _bin_index(n: int) -> int:
    for i, upper in enumerate(_BIN_UPPER):
        if n < upper:
            return i
    return len(_BIN_UPPER) - 1  # unreachable (last upper is inf)


def _percentile(sorted_vals: List[int], q: float) -> Optional[float]:
    """Linear-interpolated percentile of a pre-sorted list; ``None`` if empty."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = q * (len(sorted_vals) - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 < len(sorted_vals):
        return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])
    return float(sorted_vals[lo])


class TokenAccumulator:
    """Streaming, exact token statistics over a corpus."""

    def __init__(self, primary: str = PRIMARY, secondary: str = SECONDARY):
        self.primary_name = primary
        self.secondary_name = secondary
        self._enc_primary = tiktoken.get_encoding(primary)
        self._enc_secondary = tiktoken.get_encoding(secondary)

        self.patients = 0
        self.full_primary = 0
        self.full_secondary = 0
        self.clinical_primary = 0
        self.clinical_secondary = 0
        # per-patient full-record primary counts, for the distribution (8 bytes each)
        self._per_patient = array("q")
        self._bins = [0] * len(_BIN_LABELS)

    @staticmethod
    def _n(enc, text: str) -> int:
        # encode_ordinary: treat every byte as ordinary text (no special-token
        # handling), which is exactly what a raw token count needs.
        return len(enc.encode_ordinary(text)) if text else 0

    def add(self, full_text: str, clinical_text: str) -> int:
        """Tokenise one patient's full and clinical text; return its full o200k
        count (handy for the caller's Part-B / logging)."""
        fp = self._n(self._enc_primary, full_text)
        fs = self._n(self._enc_secondary, full_text)
        cp = self._n(self._enc_primary, clinical_text)
        cs = self._n(self._enc_secondary, clinical_text)

        self.patients += 1
        self.full_primary += fp
        self.full_secondary += fs
        self.clinical_primary += cp
        self.clinical_secondary += cs
        self._per_patient.append(fp)
        self._bins[_bin_index(fp)] += 1
        return fp

    # ------------------------------------------------------------------ #
    def result(self) -> Dict:
        """Finalise into the ``scale`` token fields (primary = o200k_base).

        Includes clinical-content extension fields alongside the schema's
        standard token fields — extra keys the intake schema tolerates.
        """
        vals = sorted(self._per_patient)
        n = len(vals)
        mean = (self.full_primary / n) if n else None
        median = _percentile(vals, 0.50)
        clinical_pct = round(100.0 * self.clinical_primary / self.full_primary, 1) \
            if self.full_primary else None

        return {
            "tokeniser": f"tiktoken {self.primary_name}",
            "total_tokens": self.full_primary,
            "mean_tokens_per_patient": round(mean, 2) if mean is not None else None,
            "median_tokens_per_patient": round(median, 2) if median is not None else None,
            "token_distribution": {
                "min_tokens_per_patient": (vals[0] if n else None),
                "max_tokens_per_patient": (vals[-1] if n else None),
                "percentiles": {
                    "p50": _round(median),
                    "p90": _round(_percentile(vals, 0.90)),
                    "p99": _round(_percentile(vals, 0.99)),
                },
                "bins": [{"range": lbl, "patients": cnt}
                         for lbl, cnt in zip(_BIN_LABELS, self._bins)],
            },
            # --- clinical-content extension (full vs clinical) --------------- #
            "clinical_content_tokens": self.clinical_primary,
            "structure_tokens": self.full_primary - self.clinical_primary,
            "clinical_content_pct": clinical_pct,
            # --- independent cross-check encoder ----------------------------- #
            "tokeniser_cross_check": f"tiktoken {self.secondary_name}",
            "total_tokens_cross_check": self.full_secondary,
            "clinical_content_tokens_cross_check": self.clinical_secondary,
        }


def _round(x: Optional[float]) -> Optional[float]:
    return round(x, 1) if x is not None else None
