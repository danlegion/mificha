"""Vaccine registry: aggregate vaccine entries per family member.

A vaccine entry is a record of something already received, exactly as a
document states it. This module never computes due dates, never infers
a vaccine from a name alone, and never recommends a vaccine: the
registry is only what the documents show.
"""

import re
from datetime import datetime, timezone

from app.schemas import clave_biomarcador, nombre_display

_MESES = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5,
    "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dic": 12, "dec": 12,
    "enero": 1, "january": 1, "febrero": 2, "february": 2, "marzo": 3,
    "march": 3, "abril": 4, "april": 4, "mayo": 5, "may": 5, "junio": 6,
    "june": 6, "julio": 7, "july": 7, "agosto": 8, "august": 8,
    "septiembre": 9, "september": 9, "octubre": 10, "october": 10,
    "noviembre": 11, "november": 11, "diciembre": 12, "december": 12,
}


def parse_vaccine_date(raw):
    """Return YYYY-MM-DD for a recognizable date string, else None.

    Handles YYYY-MM-DD, DD/MM/YYYY and DD-MM-YYYY (day first, the
    Latin-American convention), and '15 Oct 2024' / '15 de octubre
    de 2024' style dates. Ambiguous MM/DD/YYYY is not guessed: it
    returns None and the caller keeps the original string.
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)),
                            int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Day-first: if the first number cannot be a month, it is the
        # day; if both could be, day-first is still the convention
        # used here, but only when the second is a valid month.
        if month < 1 or month > 12:
            return None
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None
    m = re.fullmatch(
        r"(\d{1,2})\s+(?:de\s+)?([A-Za-záéíóúñ]+)\s+(?:de\s+)?(\d{4})",
        s, re.IGNORECASE)
    if m:
        month = _MESES.get(m.group(2).lower())
        if month:
            try:
                return datetime(int(m.group(3)), month,
                                int(m.group(1))).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def _date_sort_key(entry):
    parsed = parse_vaccine_date(entry.get("date_administered"))
    if parsed:
        return (1, datetime.strptime(parsed, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc))
    return (0, datetime.min.replace(tzinfo=timezone.utc))


def collect_vaccines(docs):
    """Aggregate vaccine entries from documents, deduped, newest first.

    docs: dal document dicts (each with explanation.vaccines, member_id,
    id, created_at). Returns entries with a source_doc_id so the
    registry always links back to the document it came from.
    """
    seen = set()
    entries = []
    for d in docs or []:
        exp = d.get("explanation") or {}
        for v in exp.get("vaccines") or []:
            name = (v.get("name") or "").strip()
            if not name:
                continue
            date = (v.get("date_administered") or "").strip() or None
            dose = (v.get("dose") or "").strip() or None
            key = (clave_biomarcador(name), date or "", dose or "")
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "name": nombre_display(name),
                "date_administered": date,
                "dose": dose,
                "lot": (v.get("lot") or "").strip() or None,
                "manufacturer": (v.get("manufacturer") or "").strip() or None,
                "provider": (v.get("provider") or "").strip() or None,
                "source_doc_id": d["id"],
                "member_id": d.get("member_id"),
            })
    entries.sort(key=_date_sort_key, reverse=True)
    return entries


def group_vaccines_by_member(docs, members):
    """Registry groups: members in order, unassigned last."""
    all_entries = collect_vaccines(docs)
    groups = []
    for m in members or []:
        groups.append({
            "member_id": m["id"], "member_name": m["name"],
            "vaccines": [e for e in all_entries
                         if e["member_id"] == m["id"]],
        })
    groups.append({
        "member_id": "", "member_name": "",
        "vaccines": [e for e in all_entries if not e.get("member_id")],
    })
    return groups
