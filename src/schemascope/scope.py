"""Part A scope metrics (A1–A12), computed as SQL aggregates over the whole DB.

Each ``a*`` method returns the dict for its schema section. Streams that the
mapping marks absent yield ``present: false`` / null sections rather than errors,
so a dataset that lacks (say) radiology still profiles cleanly. Date/year handling
is dialect-aware (SQLite for tests; SQL Server / Postgres in production).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from .io import Db
from .mapping import Mapping, StreamMap
from .model import STREAM_ORDER, gender_bucket, to_date_str

# stream -> documentation area, for record_depth.stream_split_pct
_AREA = {
    "encounters": "consultation", "triage_vitals": "consultation",
    "history_notes": "consultation", "physical_exam": "consultation",
    "region_findings": "consultation", "impression_notes": "consultation",
    "diagnoses": "consultation",
    "prescriptions": "treatment", "pharmacy_requests": "treatment",
    "procedures": "treatment", "referrals": "treatment",
    "immunizations": "treatment", "allergies": "treatment",
    "lab_requests": "investigations", "lab_results": "investigations",
    "radiology": "investigations",
}

_ICD_CHAPTERS = {
    "A": "Infectious", "B": "Infectious", "C": "Neoplasms", "D": "Blood/neoplasms",
    "E": "Endocrine", "F": "Mental", "G": "Nervous", "H": "Eye & ear",
    "I": "Circulatory", "J": "Respiratory", "K": "Digestive", "L": "Skin",
    "M": "Musculoskeletal", "N": "Genitourinary", "O": "Pregnancy", "P": "Perinatal",
    "Q": "Congenital", "R": "Symptoms", "S": "Injury", "T": "Injury/external",
    "V": "Transport", "W": "Falls", "X": "External", "Y": "Misadventure",
    "Z": "Health factors", "U": "Special",
}


class ScopeProfiler:
    def __init__(self, db: Db, mapping: Mapping, patients_total: Optional[int] = None):
        self.db = db
        self.m = mapping
        self.keys = mapping.keys
        # The token pass is the authoritative patient count (distinct patients seen
        # across every stream); reuse it so scale.patients_total and the token
        # distribution can never disagree. Fall back to a query if not supplied.
        self.patients_total = patients_total if patients_total is not None else self._patients_total()
        self.encounters_total = self._encounters_total()

    # ---- helpers -------------------------------------------------------- #
    def _year(self, col_sql: str) -> str:
        """Year as an integer, robust to the granularity the source carries: full
        dates, datetimes, ``YYYY-MM`` and year-only ``YYYY`` (post-HIPAA). Built-in
        date functions (strftime / YEAR / EXTRACT) choke on a bare ``'2023'``, so we
        take the leading 4-digit year from the text form. Implausible values are
        filtered by the caller (see :meth:`longitudinal`)."""
        d = self.db.engine.dialect.name
        if d == "mssql":
            # CONVERT(..., 126) forces ISO-8601 for real date/datetime columns (a
            # bare CAST to NVARCHAR renders 'Jan  1 2023' for legacy datetime, whose
            # first 4 chars aren't a year); it's a passthrough for text year columns.
            return f"TRY_CONVERT(INT, LEFT(LTRIM(CONVERT(NVARCHAR(30), {col_sql}, 126)), 4))"
        t = self.db.text_cast(col_sql)
        if d == "postgresql":
            return f"NULLIF(substring(btrim({t}) from '^[0-9]{{4}}'), '')::int"
        if d in ("mysql", "mariadb"):
            # CAST(... AS INTEGER) isn't valid MySQL (needs SIGNED); a non-year
            # string coerces to 0, which the caller's plausible-year filter drops.
            return f"CAST(SUBSTR(LTRIM(RTRIM({t})), 1, 4) AS SIGNED)"
        if d == "oracle":
            return f"TO_NUMBER(SUBSTR(LTRIM(RTRIM({t})), 1, 4) DEFAULT NULL ON CONVERSION ERROR)"
        if d == "duckdb":
            # DuckDB casts are strict (a non-numeric string raises), so TRY_CAST.
            return f"TRY_CAST(substr(LTRIM(RTRIM({t})), 1, 4) AS INTEGER)"
        # sqlite / default: substring the first 4 chars and cast
        return f"CAST(substr(LTRIM(RTRIM({t})), 1, 4) AS INTEGER)"

    def _valid_pid(self, sm: StreamMap) -> str:
        """Predicate matching the streaming merge's patient filter (non-NULL,
        non-blank id), so patient/encounter counts don't include orphan rows the
        merge — and therefore Part B — skips."""
        return self.db.nonblank_predicate(self.db.qi(sm.pid(self.keys)))

    def _col(self, sm: StreamMap, field: str) -> Optional[str]:
        return sm.columns.get(field)

    def _where(self, sm: StreamMap, *extra: str) -> str:
        """A ``WHERE`` clause combining the stream's own ``where`` filter with any
        query-specific conditions. Every aggregate over a stream's table goes
        through this, so a stream filter is honored uniformly — the token pass and
        row counts already apply it, and the scope metrics must agree."""
        conds = []
        if sm.where:
            conds.append(f"({sm.where})")
        conds.extend(extra)
        return (" WHERE " + " AND ".join(conds)) if conds else ""

    def _nonnull(self, sm: StreamMap, field: str) -> Optional[int]:
        """Count values that are actually populated — non-NULL and not blank/
        whitespace — so coverage/parse metrics don't treat '' as present."""
        col = self._col(sm, field)
        if not col:
            return None
        return int(self.db.scalar(
            f"SELECT {self.db.nonblank_count(self.db.qi(col))} "
            f"FROM {self.db.qtable(sm.table)}{self._where(sm)}") or 0)

    def _distinct(self, sm: StreamMap, field: str) -> Optional[int]:
        """Distinct populated values — blank/whitespace is not a distinct code,
        analyte, specialty, or region."""
        col = self._col(sm, field)
        if not col:
            return None
        return int(self.db.scalar(
            f"SELECT {self.db.nonblank_distinct(self.db.qi(col))} "
            f"FROM {self.db.qtable(sm.table)}{self._where(sm)}") or 0)

    def _pct(self, num: Optional[int], den: Optional[int]) -> Optional[float]:
        if not den or num is None:
            return None
        return round(100.0 * num / den, 1)

    def _populated_cells(self, sm: StreamMap) -> int:
        cols = self.db.columns(sm.table)
        if not cols:
            return 0
        # non-blank so '' / whitespace cells aren't counted as populated depth.
        expr = " + ".join(self.db.nonblank_count(self.db.qi(c)) for c in cols)
        return int(self.db.scalar(f"SELECT {expr} FROM {self.db.qtable(sm.table)}{self._where(sm)}") or 0)

    # ---- A1 (counts only; token fields merged in profile) --------------- #
    def _patients_total(self) -> int:
        for name in ("demographics", "encounters", *STREAM_ORDER):
            sm = self.m.get(name)
            if sm:
                return int(self.db.scalar(
                    f"SELECT {self.db.nonblank_distinct(self.db.qi(sm.pid(self.keys)))} "
                    f"FROM {self.db.qtable(sm.table)}{self._where(sm)}") or 0)
        return 0

    def _encounters_total(self) -> Optional[int]:
        sm = self.m.get("encounters")
        if not sm:
            return None
        eid = sm.eid(self.keys)
        if eid and eid in self.db.columns(sm.table):
            # Count distinct (patient_id, encounter_id) pairs, not distinct
            # encounter_id alone: encounter IDs are often unique only within a
            # patient, so a bare COUNT(DISTINCT encounter_id) would collapse
            # different patients' encounters that happen to share an id. The
            # subquery form is portable across SQLite / SQL Server / Postgres.
            # fold the patient key (as the merge does) so a case/whitespace-variant
            # id isn't counted as a second patient's encounter.
            pidf = self.db.fold(self.db.qi(sm.pid(self.keys)))
            eidq = self.db.qi(eid)
            return int(self.db.scalar(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT {pidf} AS p, {eidq} AS e "
                f"FROM {self.db.qtable(sm.table)}{self._where(sm, self._valid_pid(sm))}) t") or 0)
        return self.db.table_row_count(sm)

    def scale_counts(self) -> Dict[str, Any]:
        present = self.m.present_streams()
        # de-dup by (table, where) so two streams off one physical table don't
        # double-count that table's rows.
        seen, source_rows = set(), 0
        for sm in present:
            key = (sm.table, sm.where)
            if key not in seen:
                seen.add(key)
                source_rows += self.db.table_row_count(sm)
        tables = {sm.table for sm in present}
        return {
            "patients_total": self.patients_total,
            "encounters_total": self.encounters_total,
            "admissions_total": None,
            "source_rows_total": source_rows,
            "linked_tables": len(tables),
        }

    # ---- A2 ------------------------------------------------------------- #
    def stream_inventory(self) -> List[Dict[str, Any]]:
        out = []
        for name in STREAM_ORDER:
            sm = self.m.get(name)
            if sm:
                out.append({"stream": name, "present": True,
                            "row_count": self.db.table_row_count(sm)})
            else:
                out.append({"stream": name, "present": False, "row_count": None})
        return out

    # ---- A3 ------------------------------------------------------------- #
    def record_depth(self) -> Dict[str, Any]:
        enc = self.encounters_total or 0
        present = self.m.present_streams()
        # dedup populated-cell counts by physical (table, where): a table shared by
        # two streams (e.g. encounters + diagnoses) must be counted once, exactly as
        # source_rows_total and the token pass dedup it.
        seen_pop = set()
        pop = 0
        for sm in present:
            if _AREA.get(sm.stream) != "consultation":
                continue
            k = (sm.table, sm.where)
            if k in seen_pop:
                continue
            seen_pop.add(k)
            pop += self._populated_cells(sm)
        fields_per_enc = round(pop / enc, 1) if enc else None

        mean = round(self.encounters_total / self.patients_total, 2) \
            if (self.encounters_total and self.patients_total) else None
        median = self._visits_median()

        cells_by_area: Dict[str, int] = {}
        seen_area = set()
        for sm in present:
            k = (sm.table, sm.where)
            if k in seen_area:
                continue
            seen_area.add(k)
            area = _AREA.get(sm.stream, "other")
            cells_by_area[area] = cells_by_area.get(area, 0) + self._populated_cells(sm)
        total = sum(cells_by_area.values())
        split = {a: round(100.0 * c / total, 1) for a, c in cells_by_area.items()} if total else None

        return {"fields_per_encounter_avg": fields_per_enc,
                "visits_per_patient_median": median,
                "visits_per_patient_mean": mean,
                "stream_split_pct": split}

    def _visits_median(self) -> Optional[float]:
        sm = self.m.get("encounters")
        if not sm:
            return None
        pidf = self.db.fold(self.db.qi(sm.pid(self.keys)))
        eid = sm.eid(self.keys)
        w = self._where(sm, self._valid_pid(sm))
        if eid and eid in self.db.columns(sm.table):
            # distinct encounters per patient counted the SAME way as
            # encounters_total (distinct (folded pid, encounter_id)) — so a NULL
            # encounter_id still counts as one visit, not zero.
            eidq = self.db.qi(eid)
            rows = self.db.rows(
                f"SELECT p, COUNT(*) AS c FROM (SELECT DISTINCT {pidf} AS p, {eidq} AS e "
                f"FROM {self.db.qtable(sm.table)}{w}) t GROUP BY p")
        else:
            rows = self.db.rows(
                f"SELECT {pidf} AS p, COUNT(*) AS c FROM {self.db.qtable(sm.table)}{w} GROUP BY {pidf}")
        counts = [int(r["c"]) for r in rows]
        # the mean is encounters_total / patients_total (denominator includes
        # encounter-less patients); pad the median with those 0-visit patients so
        # both statistics describe the SAME population.
        counts.extend([0] * max(0, (self.patients_total or 0) - len(counts)))
        if not counts:
            return None
        counts.sort()
        n = len(counts)
        mid = n // 2
        return float(counts[mid]) if n % 2 else (counts[mid - 1] + counts[mid]) / 2.0

    # ---- A4 ------------------------------------------------------------- #
    def longitudinal(self) -> Dict[str, Any]:
        sm = self.m.get("encounters")
        if not sm or not sm.date_column:
            return {"first_encounter_date": None, "last_encounter_date": None,
                    "years_covered": None, "encounters_by_year": []}
        dcol = self.db.qi(sm.date_column)
        tbl = self.db.qtable(sm.table)
        yr = self._year(dcol)
        nn = f"{dcol} IS NOT NULL"
        vp = self._valid_pid(sm)
        pidf = self.db.fold(self.db.qi(sm.pid(self.keys)))
        eid = sm.eid(self.keys)
        has_eid = bool(eid and eid in self.db.columns(sm.table))
        # first/last must agree with the year histogram: bound MIN/MAX to rows whose
        # year is plausible, so a sentinel/garbage date ('0000-00-00', '1809-…',
        # 'unknown') can't corrupt the headline range while the histogram stays clean.
        plausible = (f"{yr} >= {_MIN_PLAUSIBLE_YEAR}", f"{yr} <= {_max_plausible_year()}")
        pw = self._where(sm, nn, vp, *plausible)
        first = self.db.scalar(f"SELECT MIN({dcol}) FROM {tbl}{pw}")
        last = self.db.scalar(f"SELECT MAX({dcol}) FROM {tbl}{pw}")
        by_year: Dict[int, Dict[str, Any]] = {}
        # Each distinct encounter (folded patient, encounter_id) is assigned to ONE
        # year — the year of its earliest date — so an encounter whose rows straddle
        # a year boundary is counted once and sum(encounters_by_year) never exceeds
        # encounters_total. (Patient-local/duplicate ids stay distinct via the pair.)
        if has_eid:
            eidq = self.db.qi(eid)
            enc_rows = self.db.rows(
                f"SELECT y, COUNT(*) AS c FROM (SELECT {self._year('m')} AS y FROM "
                f"(SELECT {pidf} AS p, {eidq} AS e, MIN({dcol}) AS m FROM {tbl}"
                f"{self._where(sm, nn, vp)} GROUP BY {pidf}, {eidq}) enc) t GROUP BY y")
        else:
            enc_rows = self.db.rows(
                f"SELECT {yr} AS y, COUNT(*) AS c FROM {tbl}{self._where(sm, nn, vp)} GROUP BY {yr}")
        for r in enc_rows:
            if not _plausible_year(r["y"]):   # skip null / unparseable / implausible year
                continue
            y = int(r["y"])
            by_year[y] = {"year": y, "encounters": int(r["c"]), "new_patients": None}
        for r in self.db.rows(
                f"SELECT {self._year('m')} AS y, COUNT(*) AS c FROM "
                f"(SELECT {pidf} AS p, MIN({dcol}) AS m FROM {tbl}{self._where(sm, nn, vp)} GROUP BY {pidf}) t "
                f"GROUP BY {self._year('m')}"):
            if not _plausible_year(r["y"]):
                continue
            y = int(r["y"])
            by_year.setdefault(y, {"year": y, "encounters": None, "new_patients": None})
            by_year[y]["new_patients"] = int(r["c"])
        years = sorted(by_year)
        return {"first_encounter_date": to_date_str(first), "last_encounter_date": to_date_str(last),
                "years_covered": (years[-1] - years[0] + 1) if years else None,
                "encounters_by_year": [by_year[y] for y in years]}

    # ---- A5 ------------------------------------------------------------- #
    def geography(self) -> Dict[str, Any]:
        sm = self.m.get("encounters")
        fac = self._col(sm, "facility_id") if sm else None
        reg = self._col(sm, "region") if sm else None
        if not sm or not (fac or reg):
            return {"facilities_total": None, "regions_covered": None, "distribution": []}
        tbl = self.db.qtable(sm.table)
        w = self._where(sm)
        facilities_total = int(self.db.scalar(
            f"SELECT {self.db.nonblank_distinct(self.db.qi(fac))} FROM {tbl}{w}") or 0) if fac else None
        regions_covered = int(self.db.scalar(
            f"SELECT {self.db.nonblank_distinct(self.db.qi(reg))} FROM {tbl}{w}") or 0) if reg else None
        distribution = []
        if reg:
            # blank/whitespace regions are not a region; exclude them so the
            # distribution has no null-region row (schema requires a string).
            wr = self._where(sm, self.db.nonblank_predicate(self.db.qi(reg)))
            total = self.db.scalar(f"SELECT COUNT(*) FROM {tbl}{wr}") or 0
            # per-region facilities must count the SAME way as facilities_total
            # (non-blank distinct), else blank/whitespace variants make a region
            # report more facilities than the whole corpus.
            fac_expr = self.db.nonblank_distinct(self.db.qi(fac)) if fac else "NULL"
            # group by the TRIMMED region so 'North'/' North' merge into one row and
            # len(distribution) matches regions_covered (which trims via nonblank_distinct).
            regt = f"LTRIM(RTRIM({self.db.text_cast(self.db.qi(reg))}))"
            for r in self.db.rows(
                    f"SELECT {regt} AS region, {fac_expr} AS facilities, "
                    f"COUNT(*) AS n FROM {tbl}{wr} GROUP BY {regt} "
                    f"ORDER BY n DESC, {regt}"):
                distribution.append({"region": _str(r["region"]),
                                     "facilities": _int(r["facilities"]),
                                     "share_pct": round(100.0 * r["n"] / total, 1) if total else None})
        return {"facilities_total": facilities_total, "regions_covered": regions_covered,
                "distribution": distribution}

    # ---- A6 ------------------------------------------------------------- #
    def demographics_scope(self) -> Dict[str, Any]:
        sm = self.m.get("demographics")
        if not sm:
            return {}
        tbl = self.db.qtable(sm.table)
        # exclude orphan (null/blank-pid) demographic rows, consistent with the
        # patient merge, so they don't skew the gender/age distributions.
        vp = self._valid_pid(sm)
        w = self._where(sm, vp)
        gcol = self._col(sm, "gender")
        gender = None
        if gcol:
            gmap = sm.gender_map()
            total = 0
            buckets = {"female": 0, "male": 0, "other_unknown": 0}
            for r in self.db.rows(
                    f"SELECT {self.db.qi(gcol)} AS g, COUNT(*) AS n FROM {tbl}{w} GROUP BY {self.db.qi(gcol)}"):
                total += r["n"]
                buckets[gender_bucket(r["g"], gmap)] += r["n"]
            gender = {k: round(100.0 * v / total, 1) for k, v in buckets.items()} if total else None

        acol = self._col(sm, "age_years")
        mean_age = age_parse = age_bands = None
        if acol:
            q = self.db.qi(acol)
            # Age may be stored as text; compare it numerically so bands are right,
            # and exclude blank/whitespace from the mean and the bands too (not only
            # the parse-rate denominator) — otherwise a text '' coerces to 0 and both
            # the average and the '0-4' band are corrupted. safe_real avoids a hard
            # conversion error on a non-numeric row that the WHERE didn't filter
            # first (SQL Server / Postgres don't guarantee WHERE-before-projection).
            qn = self.db.safe_real(q)
            # only a genuine number is a parseable age: 'unknown' and bands like
            # '90+' must not count toward parse rate, mean, or the numeric bands.
            nb = self.db.numeric_predicate(q)
            # A usable age is a plain number within a plausible human range. Numeric-
            # but-implausible values (e.g. 150) are excluded from the mean, the bands,
            # AND the parse-rate count together, so the three stay mutually consistent
            # (the bands still sum to ~100%).
            aw = self._where(sm, vp, nb, f"{qn} >= {_AGE_MIN_YEARS}", f"{qn} <= {_AGE_MAX_YEARS}")
            mean_age = self.db.scalar(f"SELECT AVG({qn}) FROM {tbl}{aw}")
            mean_age = round(float(mean_age), 1) if mean_age is not None else None
            with_age = int(self.db.scalar(f"SELECT COUNT(*) FROM {tbl}{aw}") or 0)
            demo_rows = int(self.db.scalar(f"SELECT COUNT(*) FROM {tbl}{w}") or 0)
            age_parse = self._pct(with_age, demo_rows)
            case = _age_band_case(qn)
            age_bands = []
            for r in self.db.rows(
                    f"SELECT {case} AS band, COUNT(*) AS n FROM {tbl}{aw} GROUP BY {case}"):
                age_bands.append({"band": r["band"],
                                  "share_pct": round(100.0 * r["n"] / with_age, 1) if with_age else None})
            age_bands.sort(key=lambda b: _BAND_ORDER.get(b["band"], 99))
        return {"gender_split_pct": gender, "mean_age_years": mean_age,
                "age_parse_rate_pct": age_parse, "age_bands_pct": age_bands or []}

    # ---- A7 ------------------------------------------------------------- #
    def diagnoses_scope(self) -> Dict[str, Any]:
        sm = self.m.get("diagnoses")
        if not sm:
            return {}
        tbl = self.db.qtable(sm.table)
        where = f" WHERE {sm.where}" if sm.where else ""
        code_col = self._col(sm, "icd10_code")
        name_col = self._col(sm, "diagnosis_name")
        text_col = self._col(sm, "diagnosis_text")
        # "coded records" = rows that actually carry a code (blank codes don't count);
        # fall back to all rows only when no code column is mapped.
        coded_expr = self.db.nonblank_count(self.db.qi(code_col)) if code_col else "COUNT(*)"
        coded = int(self.db.scalar(f"SELECT {coded_expr} FROM {tbl}{where}") or 0)

        distinct_codes = self._distinct(sm, "icd10_code")
        by_chapter: Dict[str, int] = {}
        shape_ok = 0
        if code_col:
            import re
            shape = re.compile(r"^[A-Za-z][0-9]{2}")
            for r in self.db.rows(
                    f"SELECT {self.db.qi(code_col)} AS code, COUNT(*) AS n FROM {tbl}{where} "
                    f"GROUP BY {self.db.qi(code_col)}"):
                code = _str(r["code"]) or ""
                if shape.match(code):
                    shape_ok += r["n"]
                    ch = code[0].upper()
                    by_chapter[ch] = by_chapter.get(ch, 0) + r["n"]
        paired = None
        if name_col or text_col:
            col = name_col or text_col
            # "% of CODED records that also carry free text" — the numerator must be
            # rows that have BOTH a code and a name, else uncoded free-text rows push
            # the rate over 100%.
            if code_col:
                conds = [self.db.nonblank_predicate(self.db.qi(code_col)),
                         self.db.nonblank_predicate(self.db.qi(col))]
                if sm.where:
                    conds.insert(0, f"({sm.where})")
                num = int(self.db.scalar(
                    f"SELECT COUNT(*) FROM {tbl} WHERE {' AND '.join(conds)}") or 0)
            else:
                num = int(self.db.scalar(
                    f"SELECT {self.db.nonblank_count(self.db.qi(col))} FROM {tbl}{where}") or 0)
            paired = self._pct(num, coded)

        top = []
        if name_col:
            rows = self.db.rows(
                f"SELECT {self.db.qi(name_col)} AS name, COUNT(*) AS n FROM {tbl}{where} "
                f"GROUP BY {self.db.qi(name_col)} ORDER BY n DESC, {self.db.qi(name_col)}")
            top = list(dict.fromkeys(x for x in (_str(r["name"]) for r in rows) if x))[:20]

        return {
            "coding_system": "ICD-10" if code_col else None,
            "coded_records": coded, "distinct_codes": distinct_codes,
            "icd10_shape_match_pct": self._pct(shape_ok, coded) if code_col else None,
            "paired_free_text_pct": paired,
            "by_chapter": [{"chapter": ch, "label": _ICD_CHAPTERS.get(ch),
                            "records": by_chapter[ch]} for ch in sorted(by_chapter)],
            "top_conditions": top,
        }

    # ---- A8 ------------------------------------------------------------- #
    def laboratory_scope(self) -> Dict[str, Any]:
        sm = self.m.get("lab_results")
        if not sm:
            return {}
        tbl = self.db.qtable(sm.table)
        w = self._where(sm)
        if sm.layout == "wide" and sm.analyte_columns:
            # non-blank so '' / whitespace cells aren't counted as real results
            counts = {c: int(self.db.scalar(
                        f"SELECT {self.db.nonblank_count(self.db.qi(c))} FROM {tbl}{w}") or 0)
                      for c in sm.analyte_columns}
            present_cols = {c: n for c, n in counts.items() if n > 0}
            results = sum(present_cols.values())
            orders = self.db.table_row_count(sm)
            top = sorted(present_cols, key=lambda c: present_cols[c], reverse=True)[:10]
            return {"distinct_analytes": len(present_cols), "analyte_results": results,
                    "orders": orders, "carry_units_pct": None,
                    "carry_reference_range_pct": None, "top_analytes": top}
        # long layout
        an = self._col(sm, "analyte_name")
        # a result needs an actual analyte name; a blank-name row isn't a result.
        results = (int(self.db.scalar(
            f"SELECT {self.db.nonblank_count(self.db.qi(an))} FROM {tbl}{w}") or 0)
            if an else self.db.table_row_count(sm))
        top = []
        if an:
            rows = self.db.rows(
                f"SELECT {self.db.qi(an)} AS a, COUNT(*) AS n FROM {tbl}{w} "
                f"GROUP BY {self.db.qi(an)} ORDER BY n DESC, {self.db.qi(an)}")
            top = list(dict.fromkeys(x for x in (_str(r["a"]) for r in rows) if x))[:10]
        req = self.m.get("lab_requests")
        orders = self.db.table_row_count(req) if req else self._distinct(sm, "order_id")
        # coverage over ALL lab rows (a bounded denominator) — using `results`
        # (non-blank-analyte rows) could be exceeded by unit rows and top 100%.
        rows_total = self.db.table_row_count(sm)
        return {
            "distinct_analytes": self._distinct(sm, "analyte_name"),
            "analyte_results": results, "orders": orders,
            "carry_units_pct": self._pct(self._nonnull(sm, "unit"), rows_total),
            "carry_reference_range_pct": self._pct(self._nonnull(sm, "reference_range"), rows_total),
            "top_analytes": top,
        }

    # ---- A9 ------------------------------------------------------------- #
    def vitals_scope(self) -> Dict[str, Any]:
        sm = self.m.get("triage_vitals")
        if not sm:
            return {}
        rows = self.db.table_row_count(sm)
        vitals = ["temperature_c", "blood_pressure", "pulse_rate", "weight_kg",
                  "height_cm", "bmi", "respiratory_rate", "random_blood_sugar"]
        alias = {"temperature_c": "temperature", "weight_kg": "weight", "height_cm": "height"}
        coverage = {}
        for v in vitals:
            pct = self._pct(self._nonnull(sm, v), rows)
            if pct is not None:
                coverage[alias.get(v, v)] = pct
        return {"triage_rows": rows, "coverage_pct": coverage or None}

    # ---- A10 ------------------------------------------------------------ #
    def examination_scope(self) -> Dict[str, Any]:
        sm = self.m.get("region_findings")
        if not sm:
            return {}
        cells = self.db.table_row_count(sm)
        rcol = self._col(sm, "region")
        regions = self._distinct(sm, "region")
        ocol = self._col(sm, "outcome")
        outcome = None
        if ocol:
            buckets = {"not_examined": 0, "normal": 0, "abnormal": 0}
            total = 0
            for r in self.db.rows(
                    f"SELECT {self.db.qi(ocol)} AS o, COUNT(*) AS n FROM {self.db.qtable(sm.table)}{self._where(sm)} "
                    f"GROUP BY {self.db.qi(ocol)}"):
                total += r["n"]
                buckets[_outcome_bucket(r["o"])] += r["n"]
            outcome = {k: round(100.0 * v / total, 1) for k, v in buckets.items()} if total else None
        return {"regions_in_grid": regions, "region_cells_total": cells, "outcome_pct": outcome}

    # ---- A11 ------------------------------------------------------------ #
    def medications_scope(self) -> Dict[str, Any]:
        sm = self.m.get("prescriptions")
        if not sm:
            return {}
        lines = self.db.table_row_count(sm)
        item_col = self._col(sm, "generic_name") or self._col(sm, "brand_name")
        distinct_items = None
        if item_col:
            distinct_items = int(self.db.scalar(
                f"SELECT {self.db.nonblank_distinct(self.db.qi(item_col))} "
                f"FROM {self.db.qtable(sm.table)}{self._where(sm)}") or 0)
        sig = {}
        for f, key in (("frequency", "frequency"), ("route", "route"), ("duration", "duration")):
            pct = self._pct(self._nonnull(sm, f), lines)
            if pct is not None:
                sig[key] = pct
        return {"prescription_lines": lines, "distinct_items": distinct_items,
                "sig_coverage_pct": sig or None}

    # ---- A12 ------------------------------------------------------------ #
    def specialties_scope(self) -> Dict[str, Any]:
        sm = self.m.get("encounters")
        if not sm:
            return {}
        return {"distinct_specialties": self._distinct(sm, "specialty_id")}

    # ---- run all -------------------------------------------------------- #
    def run(self) -> Dict[str, Any]:
        return {
            "_scale_counts": self.scale_counts(),
            "stream_inventory": self.stream_inventory(),
            "record_depth": self.record_depth(),
            "longitudinal": self.longitudinal(),
            "geography": self.geography(),
            "demographics_scope": self.demographics_scope(),
            "diagnoses_scope": self.diagnoses_scope(),
            "laboratory_scope": self.laboratory_scope(),
            "vitals_scope": self.vitals_scope(),
            "examination_scope": self.examination_scope(),
            "medications_scope": self.medications_scope(),
            "specialties_scope": self.specialties_scope(),
        }


# --------------------------------------------------------------------------- #
# A real patient age falls in this range. Numeric-but-implausible values (e.g. a
# 150, or a data-entry sentinel like 999) are excluded from the mean, the bands,
# and the parse rate — mirrors the plausible-year bound used in longitudinal().
_AGE_MIN_YEARS, _AGE_MAX_YEARS = 0, 120

_BAND_ORDER = {"0-4": 0, "5-14": 1, "15-24": 2, "25-34": 3, "35-44": 4,
               "45-54": 5, "55-64": 6, "65+": 7}


# Plausible calendar range for an encounter year. The floor drops a garbled
# extraction (e.g. year -4707 from a non-date); the ceiling is the current year
# plus one year of slack — clinical encounters can't be in the future, so an
# obvious junk date like 2099 is dropped while a legitimate current-year record is
# always kept. Computed per call so the ceiling stays correct as years pass.
_MIN_PLAUSIBLE_YEAR = 1900


def _max_plausible_year() -> int:
    return date.today().year + 1


def _plausible_year(y: Any) -> bool:
    """A parsed year worth keeping: present and within a sane calendar range, so a
    garbled extraction (year -4707) or a future junk date (2099) is dropped."""
    if y is None:
        return False
    try:
        return _MIN_PLAUSIBLE_YEAR <= int(y) <= _max_plausible_year()
    except (TypeError, ValueError):
        return False


def _age_band_case(col: str) -> str:
    return (f"CASE WHEN {col} < 5 THEN '0-4' WHEN {col} < 15 THEN '5-14' "
            f"WHEN {col} < 25 THEN '15-24' WHEN {col} < 35 THEN '25-34' "
            f"WHEN {col} < 45 THEN '35-44' WHEN {col} < 55 THEN '45-54' "
            f"WHEN {col} < 65 THEN '55-64' ELSE '65+' END")


def _outcome_bucket(v: Any) -> str:
    s = (_str(v) or "").lower()
    if "abnorm" in s or "anorm" in s:
        return "abnormal"
    if "normal" in s:
        return "normal"
    return "not_examined"


def _str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


