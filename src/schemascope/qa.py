"""QA gates over a finished profile.

Every run must clear these before the output is trusted. An ``error`` means the
profile is wrong or internally inconsistent (the run should not be shipped); a
``warning`` is worth a human glance but does not invalidate the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .validate import validate


@dataclass
class Issue:
    level: str   # "error" | "warning"
    check: str
    message: str


def _sum_delta(d: Optional[Dict[str, float]], target: float = 100.0) -> Optional[float]:
    if not d:
        return None
    return abs(sum(d.values()) - target)


def run_qa(profile: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []

    # 1) schema validity is a hard gate
    for err in validate(profile):
        issues.append(Issue("error", "schema", err))

    scale = profile.get("scale", {})
    pt = scale.get("patients_total")

    # 2) at least one patient
    if not pt or pt < 1:
        issues.append(Issue("error", "patients", "patients_total must be >= 1"))

    # 3) token sanity: clinical <= full, structure consistent
    full = scale.get("total_tokens")
    clin = scale.get("clinical_content_tokens")
    struct = scale.get("structure_tokens")
    if full is not None and clin is not None:
        if clin > full:
            issues.append(Issue("error", "tokens", f"clinical_content_tokens ({clin}) > total_tokens ({full})"))
        if struct is not None and struct != full - clin:
            issues.append(Issue("error", "tokens", "structure_tokens != total_tokens - clinical_content_tokens"))
        if full == 0 and pt:
            issues.append(Issue("warning", "tokens", "total_tokens is 0 despite patients present"))

    # 4) distribution: bins sum to the patient count; percentiles monotonic
    dist = scale.get("token_distribution") or {}
    bins = dist.get("bins") or []
    bin_sum = sum((b.get("patients") or 0) for b in bins)
    if bins and pt and bin_sum != pt:
        # patients_total is set from the token pass, so this is an exact invariant;
        # any mismatch means the merge split/dropped a patient — a corruption, not a note.
        issues.append(Issue("error", "tokens",
                            f"token bins sum to {bin_sum} but patients_total is {pt} "
                            f"(the per-patient merge split or dropped a patient)"))
    pct = dist.get("percentiles") or {}
    p50, p90, p99 = pct.get("p50"), pct.get("p90"), pct.get("p99")
    if None not in (p50, p90, p99) and not (p50 <= p90 <= p99):
        issues.append(Issue("error", "tokens", f"percentiles not monotonic: {p50} <= {p90} <= {p99}"))

    # 5) distributions that should sum to ~100%
    for name, d in (("gender_split_pct", (profile.get("demographics_scope") or {}).get("gender_split_pct")),
                    ("record_depth.stream_split_pct", (profile.get("record_depth") or {}).get("stream_split_pct")),
                    ("examination.outcome_pct", (profile.get("examination_scope") or {}).get("outcome_pct"))):
        delta = _sum_delta(d)
        if delta is not None and delta > 1.0:
            issues.append(Issue("warning", name, f"shares sum to {round(sum(d.values()),1)}%, expected ~100%"))

    ab = (profile.get("demographics_scope") or {}).get("age_bands_pct") or []
    if ab:
        s = sum((b.get("share_pct") or 0) for b in ab)
        if abs(s - 100.0) > 1.0:
            issues.append(Issue("warning", "age_bands_pct", f"bands sum to {round(s,1)}%, expected ~100%"))

    # 6) non-negative counts across the scale + inventory
    for k, v in scale.items():
        if isinstance(v, int) and v < 0:
            issues.append(Issue("error", "counts", f"scale.{k} is negative ({v})"))
    for row in profile.get("stream_inventory") or []:
        rc = row.get("row_count")
        if isinstance(rc, int) and rc < 0:
            issues.append(Issue("error", "counts", f"stream {row.get('stream')} row_count negative"))

    # 7) Part B present and non-empty
    patients = profile.get("patients") or []
    if not patients:
        issues.append(Issue("error", "partB", "no worked patient record"))
    elif not (patients[0].get("encounters")):
        issues.append(Issue("warning", "partB", "worked patient has no encounters"))

    return issues


def errors(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.level == "error"]
