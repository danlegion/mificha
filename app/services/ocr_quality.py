"""Garbled-OCR check: is Tesseract's output plausible text, or noise?

This is a character-level quality gate, not a spell checker. It catches
the OCR failure mode where a page comes back as symbol soup, broken
tokens, and long consonant runs — the case where feeding the text to
the explainer would produce an explanation of garbage.

It deliberately does NOT catch plausible-but-wrong substitutions
("rn" read as "m", "cl" as "d"): those read as clean words, and no
heuristic here can tell them apart from correct text.

The gate is conservative on purpose: medical documents are full of
numbers, units (mg/dL), abbreviations (HDL, TSH, CBC), codes, and
dates, so those are all treated as clean. A page is only flagged when
at least half of its classified tokens are junk AND there are at
least 8 classified tokens — short headers and lab lines never trip it.
"""

import re

_VOCALES = set("aeiouáéíóúAEIOUÁÉÍÓÚ")
_SIMBOLOS_RAROS = set("|\\_~`#@$^*[]{}<>!?&")
_MIN_TOKENS = 8
_MAX_JUNK_RATIO = 0.5


def _consonant_run(token: str) -> int:
    best = run = 0
    for ch in token:
        if ch.isalpha() and ch not in _VOCALES:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _repeated_run(token: str) -> int:
    best = run = 1
    prev = None
    for ch in token:
        run = run + 1 if ch == prev else 1
        best = max(best, run)
        prev = ch
    return best


def _classify(token: str) -> str:
    """'clean', 'junk', or 'neutral' (separators, not evidence either way)."""
    t = token.strip(".,;:()[]{}<>\"'«»")
    if not t:
        return "neutral"
    if not any(ch.isalnum() for ch in t):
        # Pure punctuation: a dash or bullet is neutral; symbol soup is junk.
        return "junk" if any(ch in _SIMBOLOS_RAROS for ch in t) else "neutral"
    if re.fullmatch(r"[0-9]+([.,/:\-][0-9]+)*%?", t):
        return "clean"  # numbers, dates, ranges, percentages
    if re.fullmatch(r"[A-Za-záéíóúñÁÉÍÓÚÑ]+", t):
        if len(t) == 1:
            return "clean"  # "a", "y", "e"
        if any(ch in _VOCALES for ch in t):
            return "clean"
        if len(t) <= 4:
            return "clean"  # abbreviations: HDL, LDL, TSH, CBC
        return "junk"  # long vowel-less token: consonant gibberish
    if re.fullmatch(r"[A-Za-záéíóúñÁÉÍÓÚÑ]+/[A-Za-záéíóúñÁÉÍÓÚÑ]+", t) \
            and len(t) <= 10:
        return "clean"  # mg/dL, mmol/L
    # Mixed tokens (HbA1c, COVID-19, A+): clean only if short, with a
    # vowel, no weird symbols, no long consonant run, and few
    # digit/letter transitions.
    if len(t) > 30:
        return "junk"
    if any(ch in _SIMBOLOS_RAROS for ch in t):
        return "junk"
    if _consonant_run(t) >= 7 or _repeated_run(t) >= 4:
        return "junk"
    transiciones = sum(
        1 for a, b in zip(t, t[1:])
        if a.isdigit() != b.isdigit() and (a.isalnum() and b.isalnum()))
    if transiciones > 2:
        return "junk"
    if any(ch in _VOCALES for ch in t):
        return "clean"
    return "junk"


def garble_ratio(text: str) -> float:
    """Fraction of classified tokens that are junk (0.0 when too short)."""
    counts = {"clean": 0, "junk": 0}
    for token in (text or "").split():
        cls = _classify(token)
        if cls in counts:
            counts[cls] += 1
    total = counts["clean"] + counts["junk"]
    if total < _MIN_TOKENS:
        return 0.0
    return counts["junk"] / total


def is_garbled(text: str) -> bool:
    """True only for clearly garbled OCR output (see module docstring)."""
    return garble_ratio(text) >= _MAX_JUNK_RATIO
