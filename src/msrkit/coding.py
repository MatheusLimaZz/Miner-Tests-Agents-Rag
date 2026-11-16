"""Extraction form (Annex A) and gray-literature quality (§9) as coding sheets.

The form is declared in the protocol (`coding.fields`), so any study can define
its own dimensions. Each field has a type:

- `enum`: one value from `values`;
- `multi`: zero or more values, separated by `,` or `|`;
- `text`: free text, optionally limited by `max_length`;
- `bool`: x/s/sim/y/yes/1/true or n/nao/não/no/0/false (empty = not coded).

`vocabulary` takes the allowed values from the gazetteer (`tools`, `methods`)
or from the protocol's failure-mode catalog (`failure_modes`), so codes stay
consistent with extraction and analysis.

Sheets follow the screening conventions (CSV, `;`, UTF-8 with BOM). Automatic
suggestions from `msrkit extract` and `msrkit enrich` go in read-only `auto_*`
columns and are *not* copied into the form unless the coder asks
(`--prefill`), so double coding stays independent (§15). Imported codings are
appended to data/coding/<run_id>/codings.jsonl, one record per (item, coder);
the latest import of a coder for an item wins.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, model_validator

from msrkit.enrich import repo_of
from msrkit.extract import LEVEL_RANK
from msrkit.screening import DELIMITER, TRUE_MARKS, SheetError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from msrkit.extract import Detection
    from msrkit.gazetteer import Gazetteer
    from msrkit.models import Item

FieldType = Literal["enum", "multi", "text", "bool"]
Vocabulary = Literal["", "tools", "methods", "failure_modes"]
FALSE_MARKS = {"n", "nao", "no", "0", "false", "-"}
AUTO_COLUMNS = [
    "auto_sistema", "auto_nivel", "auto_ferramentas", "auto_metodos", "auto_evidencia",
    "auto_engajamento", "auto_manutencao",
]  # fmt: skip


def _fold(value: str) -> str:
    """Lowercase without accents, for tolerant matching of coded values."""
    text = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class CodingField(BaseModel):
    """One dimension of the extraction form."""

    name: str
    type: FieldType = "text"
    values: list[str] = []
    vocabulary: Vocabulary = ""
    required: bool = False
    max_length: int | None = None
    prefill: str = ""  # auto_* column copied into the field with --prefill
    help: str = ""

    @model_validator(mode="after")
    def _check(self) -> CodingField:
        if self.type in ("enum", "multi") and not (self.values or self.vocabulary):
            raise ValueError(f"field '{self.name}': {self.type} needs values or a vocabulary")
        if self.prefill and self.prefill not in AUTO_COLUMNS:
            raise ValueError(f"field '{self.name}': prefill must be one of {AUTO_COLUMNS}")
        return self


class CodingForm(BaseModel):
    """The whole form: fields plus the failure-mode catalog (§5.1, §5.2)."""

    fields: list[CodingField] = []
    failure_modes: dict[str, str] = {}
    # Double coding (§15): share of eligible items and the dimensions whose κ is reported
    # (empty = every categorical field).
    double_coding_rate: float = 0.2
    agreement: list[str] = []

    @model_validator(mode="after")
    def _unique(self) -> CodingForm:
        names = [f.name for f in self.fields]
        dup = sorted({n for n in names if names.count(n) > 1})
        clash = sorted(set(names) & {"item_id", "coder", "notes", *AUTO_COLUMNS})
        if dup or clash:
            raise ValueError(f"coding field names must be unique and not reserved: {dup + clash}")
        unknown = [a for a in self.agreement if a not in names]
        if unknown:
            raise ValueError(f"coding.agreement names unknown fields: {unknown}")
        if not 0 < self.double_coding_rate <= 1:
            raise ValueError("coding.double_coding_rate must be in (0, 1]")
        return self

    def allowed(self, field: CodingField, gazetteer: Gazetteer | None) -> list[str]:
        if field.vocabulary == "tools":
            return [t.id for t in gazetteer.tools] if gazetteer else []
        if field.vocabulary == "methods":
            return [m.id for m in gazetteer.methods] if gazetteer else []
        if field.vocabulary == "failure_modes":
            return list(self.failure_modes)
        return field.values


class CodingRecord(BaseModel):
    """One coder's form for one item."""

    item_id: str
    coder: str
    values: dict[str, Any]  # field -> str | list[str] | bool | None
    notes: str = ""
    coded_at: str
    sheet_sha256: str


class CodingRecordList(list["CodingRecord"]):
    """List of coding records with audit metadata on skipped rows."""

    skipped: list[str] = []

    def __init__(
        self,
        iterable: Sequence[CodingRecord] = (),
        skipped: list[str] | None = None,
    ) -> None:
        super().__init__(iterable)
        self.skipped = skipped or []


# -- automatic suggestions --------------------------------------------------


def suggestions(
    items: list[Item], detections: list[Detection], system_concepts: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """auto_* columns per item from concepts, detections and repository signals.

    Repository-level detections (dependency, config, CI) are shared by every item
    of that repository.
    """
    by_item: dict[str, list[Detection]] = {}
    by_repo: dict[str, list[Detection]] = {}
    for d in detections:
        by_item.setdefault(d.item_id, []).append(d)
        if d.repo and d.signal != "text":
            by_repo.setdefault(d.repo, []).append(d)
    out: dict[str, dict[str, str]] = {}
    for it in items:
        dets = by_item.get(it.id, []) + [
            d for d in by_repo.get(repo_of(it) or "", []) if d.item_id != it.id
        ]
        best = max(dets, key=lambda d: LEVEL_RANK[d.level], default=None)
        eng = it.engagement.model_dump(exclude_none=True)
        tech = it.tech
        maint = {
            "ci": tech.has_ci,
            "tests": tech.has_tests,
            "contributors": tech.contributors,
            "months": tech.active_months,
        }
        out[it.id] = {
            "auto_sistema": ", ".join(c for c in system_concepts if c in it.concepts),
            "auto_nivel": best.level if best else "",
            "auto_ferramentas": ", ".join(
                sorted({d.entry_id for d in dets if d.entry_type == "tool"})
            ),
            "auto_metodos": ", ".join(
                sorted({d.entry_id for d in dets if d.entry_type == "method"})
            ),
            "auto_evidencia": best.evidence if best else "",
            "auto_engajamento": "; ".join(f"{k}={v}" for k, v in eng.items()),
            "auto_manutencao": "; ".join(
                f"{k}={str(v).lower()}" for k, v in maint.items() if v is not None
            ),
        }
    return out


# -- sheets -----------------------------------------------------------------

BASE_COLUMNS = ["item_id", "source", "kind", "url", "title"]
TAIL_COLUMNS = ["coder", "notes"]


def sheet_columns(form: CodingForm) -> list[str]:
    return [*BASE_COLUMNS, *AUTO_COLUMNS, *(f.name for f in form.fields), *TAIL_COLUMNS]


def export_form(
    items: list[Item],
    form: CodingForm,
    path: Path,
    auto: dict[str, dict[str, str]] | None = None,
    coder: str = "",
    prefill: bool = False,
) -> int:
    """Write the coding sheet; returns the number of rows."""
    auto = auto or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=sheet_columns(form), delimiter=DELIMITER)
        writer.writeheader()
        for it in items:
            sugg = auto.get(it.id, {})
            row: dict[str, Any] = {
                "item_id": it.id,
                "source": it.source,
                "kind": it.kind.value,
                "url": str(it.url),
                "title": it.title or "",
                **sugg,
                "coder": coder,
            }
            if prefill:
                for f in form.fields:
                    if f.prefill and sugg.get(f.prefill):
                        row[f.name] = sugg[f.prefill]
            writer.writerow(row)
    return len(items)


def _parse(
    field: CodingField, raw: str, allowed: list[str], problems: list[str], where: str
) -> Any:
    text = raw.strip()
    if not text:
        return [] if field.type == "multi" else None
    if field.type == "bool":
        folded = _fold(text)
        if folded in TRUE_MARKS:
            return True
        if folded in FALSE_MARKS:
            return False
        problems.append(f"{where}: {field.name} must be yes/no, got '{text}'")
        return None
    if field.type == "text":
        if field.max_length and len(text) > field.max_length:
            problems.append(f"{where}: {field.name} longer than {field.max_length} characters")
        return text
    folded_allowed = {_fold(v): v for v in allowed}
    parts = (
        [text]
        if field.type == "enum"
        else [p.strip() for p in re.split(r"[,;|]", text) if p.strip()]
    )
    values: list[str] = []
    for part in parts:
        if not part:
            continue
        match = folded_allowed.get(_fold(part))
        if match is None:
            problems.append(f"{where}: {field.name} has unknown value '{part}'")
        elif match not in values:
            values.append(match)
    if field.type == "enum":
        return values[0] if values else None
    return values


def import_form(
    path: Path,
    form: CodingForm,
    known_ids: set[str],
    gazetteer: Gazetteer | None = None,
    default_coder: str = "",
) -> CodingRecordList:
    """Parse and validate a filled coding sheet. Rows with no field filled are skipped.

    Raises SheetError listing every invalid row, so nothing is half-imported.
    """
    raw = path.read_bytes()
    sheet_hash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    import io

    delim = DELIMITER if text.split("\n", 1)[0].count(DELIMITER) else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    allowed = {f.name: form.allowed(f, gazetteer) for f in form.fields}

    now = datetime.now(UTC).isoformat()
    records: list[CodingRecord] = []
    problems: list[str] = []
    skipped: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        item_id = (row.get("item_id") or "").strip()
        if not any((row.get(f.name) or "").strip() for f in form.fields):
            if item_id:
                skipped.append(f"line {line_no} ({item_id})")
            continue
        coder = (row.get("coder") or default_coder).strip()
        where = f"line {line_no} ({item_id or 'no item_id'})"
        if item_id not in known_ids:
            problems.append(f"{where}: unknown item_id")
            continue
        if not coder:
            problems.append(f"{where}: coder is empty (fill the column or pass --coder)")
            continue
        values: dict[str, Any] = {}
        for f in form.fields:
            value = _parse(f, row.get(f.name) or "", allowed[f.name], problems, where)
            if f.required and value in (None, []):
                problems.append(f"{where}: {f.name} is required")
            values[f.name] = value
        records.append(
            CodingRecord(
                item_id=item_id,
                coder=coder,
                values=values,
                notes=(row.get("notes") or "").strip(),
                coded_at=now,
                sheet_sha256=sheet_hash,
            )
        )
    if problems:
        raise SheetError(problems, sheet_name="extraction sheet")
    return CodingRecordList(records, skipped=skipped)


def codings_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / "coding" / run_id / "codings.jsonl"


def append_codings(data_dir: Path, run_id: str, records: list[CodingRecord]) -> Path:
    path = codings_path(data_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json() + "\n")
    return path


def load_codings(data_dir: Path, run_id: str) -> dict[tuple[str, str], CodingRecord]:
    """Latest coding per (item_id, coder)."""
    latest: dict[tuple[str, str], CodingRecord] = {}
    if run_id == "consolidated":
        coding_dir = data_dir / "coding"
        if coding_dir.exists():
            for p in coding_dir.glob("*/codings.jsonl"):
                if p.parent.name != "consolidated":
                    with open(p, encoding="utf-8-sig") as f:
                        for line in f:
                            if line_s := line.strip():
                                r = CodingRecord.model_validate(json.loads(line_s))
                                latest[(r.item_id, r.coder)] = r
        path = codings_path(data_dir, "consolidated")
        if path.exists():
            with open(path, encoding="utf-8-sig") as f:
                for line in f:
                    if line_s := line.strip():
                        r = CodingRecord.model_validate(json.loads(line_s))
                        latest[(r.item_id, r.coder)] = r
        return latest

    path = codings_path(data_dir, run_id)
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                if line_s := line.strip():
                    r = CodingRecord.model_validate(json.loads(line_s))
                    latest[(r.item_id, r.coder)] = r
    return latest
