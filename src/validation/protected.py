"""Extraction of clinically protected tokens from text.

CLAUDE.md §4.1 lists the categories that must be protected. This module finds
them; `validator.py` decides what to do when they differ.

Two design decisions here come directly from measured behaviour, not from
theory:

**Numbers are compared by value, not by surface form.** The ASR renders spoken
"two grams" as "2 grams" and "seventy-two hours" as "72 hours"
(docs/baseline-errors.md §4.2). A validator comparing strings would fire on
every dose in every dictation, and an alert that always fires is ignored --
which would defeat the protection it exists to provide.

**Units are compared by meaning.** The same transcripts contain both
"milligrams per deciliter" and "mg per deciliter", and the Phase 3 polisher
rewrote "intravenously" to "IV" (docs/phase3-results.md §3.2). These are the
same clinical fact in different words. The *value* attached to them is what
must not move.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    NEGATION = "negation"
    NUMBER = "number"
    PERCENTAGE = "percentage"
    DATE_TIME = "date_time"
    DOSE = "dose"
    UNIT = "unit"
    ROUTE = "route"
    FREQUENCY = "frequency"
    LATERALITY = "laterality"
    UNCERTAINTY = "uncertainty"
    ATTRIBUTION = "attribution"
    ALLERGY = "allergy"
    MEDICATION = "medication"
    ORGANISM = "organism"


@dataclass(frozen=True)
class Token:
    """A protected item found in text.

    `normalized` is the comparison key. Two tokens with equal category and
    normalized value are the same clinical fact regardless of spelling.
    """

    category: Category
    surface: str
    normalized: str
    start: int
    end: int


# --------------------------------------------------------------------------
# Number handling
# --------------------------------------------------------------------------

_NUMBER_WORDS: dict[str, float] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_MULTIPLIERS = {"hundred": 100, "thousand": 1000, "million": 1_000_000}

_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")


def _clean_number(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _spoken_number_value(words: list[str]) -> float | None:
    """Evaluate a spoken number phrase such as "two hundred ten"."""
    total = 0.0
    current = 0.0
    seen = False
    i = 0
    while i < len(words):
        w = words[i]
        if w in _NUMBER_WORDS:
            current += _NUMBER_WORDS[w]
            seen = True
        elif w in _MULTIPLIERS:
            if current == 0:
                current = 1
            current *= _MULTIPLIERS[w]
            if _MULTIPLIERS[w] >= 1000:
                total += current
                current = 0
            seen = True
        elif w == "point":
            # "zero point nine" -> 0.9
            frac_digits = []
            j = i + 1
            while j < len(words) and words[j] in _NUMBER_WORDS:
                v = _NUMBER_WORDS[words[j]]
                if v >= 10:
                    break
                frac_digits.append(str(int(v)))
                j += 1
            if not frac_digits:
                return None
            value = current + total + float("0." + "".join(frac_digits))
            return value
        elif w == "and":
            pass  # "one hundred and five"
        else:
            break
        i += 1
    if not seen:
        return None
    return total + current


def extract_numbers(text: str) -> list[Token]:
    """Find numeric values, whether written as digits or spoken words."""
    tokens: list[Token] = []
    lowered = text.lower()

    for m in _DIGITS.finditer(text):
        val = _clean_number(m.group())
        if val is None:
            continue
        tokens.append(Token(Category.NUMBER, m.group(), _fmt(val), m.start(), m.end()))

    # Spoken numbers: scan word runs made only of number words.
    words = list(re.finditer(r"[a-z]+", lowered))
    i = 0
    while i < len(words):
        if words[i].group() not in _NUMBER_WORDS and words[i].group() not in _MULTIPLIERS:
            i += 1
            continue
        j = i
        run = []
        while j < len(words) and (
            words[j].group() in _NUMBER_WORDS
            or words[j].group() in _MULTIPLIERS
            or words[j].group() in ("point", "and")
        ):
            run.append(words[j].group())
            j += 1
        while run and run[-1] in ("and",):
            run.pop()
            j -= 1
        if run:
            val = _spoken_number_value(run)
            if val is not None:
                start, end = words[i].start(), words[j - 1].end()
                tokens.append(
                    Token(Category.NUMBER, text[start:end], _fmt(val), start, end)
                )
        i = max(j, i + 1)

    return tokens


def _fmt(value: float) -> str:
    """Canonical numeric string so 2 and 2.0 compare equal."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


# --------------------------------------------------------------------------
# Closed vocabularies for categories where surface form varies
# --------------------------------------------------------------------------

# Route synonyms -> canonical form. "intravenously" and "IV" are the same
# route; the validator must not treat that rewrite as a change of fact.
_ROUTES = {
    "po": "oral", "orally": "oral", "oral": "oral", "by mouth": "oral",
    "iv": "intravenous", "intravenous": "intravenous",
    "intravenously": "intravenous",
    "im": "intramuscular", "intramuscular": "intramuscular",
    "intramuscularly": "intramuscular",
    "sc": "subcutaneous", "sq": "subcutaneous",
    "subcutaneous": "subcutaneous", "subcutaneously": "subcutaneous",
    "inhaled": "inhaled", "nebulized": "inhaled",
    "topical": "topical", "topically": "topical",
    "pr": "rectal", "rectally": "rectal",
    "intrathecal": "intrathecal", "intrathecally": "intrathecal",
}

_UNITS = {
    "mg": "milligram", "milligram": "milligram", "milligrams": "milligram",
    "g": "gram", "gm": "gram", "gram": "gram", "grams": "gram",
    "mcg": "microgram", "microgram": "microgram", "micrograms": "microgram",
    "ug": "microgram",
    "kg": "kilogram", "kilogram": "kilogram", "kilograms": "kilogram",
    "ml": "milliliter", "milliliter": "milliliter", "milliliters": "milliliter",
    "l": "liter", "liter": "liter", "liters": "liter",
    "dl": "deciliter", "deciliter": "deciliter", "deciliters": "deciliter",
    "unit": "unit", "units": "unit",
    "meq": "milliequivalent", "milliequivalent": "milliequivalent",
    "mmol": "millimole", "millimole": "millimole",
    "percent": "percent", "%": "percent",
    "celsius": "celsius", "centigrade": "celsius",
    "fahrenheit": "fahrenheit",
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
    "day": "day", "days": "day", "week": "week", "weeks": "week",
    "month": "month", "months": "month", "year": "year", "years": "year",
    "minute": "minute", "minutes": "minute", "min": "minute",
}

_FREQUENCIES = {
    "daily": "q24h", "once daily": "q24h", "qd": "q24h", "every day": "q24h",
    "bid": "q12h", "twice daily": "q12h", "twice a day": "q12h",
    "every twelve hours": "q12h", "every 12 hours": "q12h", "q12h": "q12h",
    "tid": "q8h", "three times daily": "q8h", "every eight hours": "q8h",
    "every 8 hours": "q8h", "q8h": "q8h",
    "qid": "q6h", "four times daily": "q6h", "every six hours": "q6h",
    "every 6 hours": "q6h", "q6h": "q6h",
    "q4h": "q4h", "every four hours": "q4h", "every 4 hours": "q4h",
    "weekly": "qweek", "once weekly": "qweek",
    "prn": "prn", "as needed": "prn",
}

_NEGATION = {
    "no", "not", "without", "denies", "denied", "negative", "never",
    "unlikely", "absent", "ruled out", "free of", "none",
}

_UNCERTAINTY = {
    "possible", "possibly", "probable", "probably", "suspected", "suspect",
    "may", "might", "could", "presumed", "apparent", "questionable",
    "concerning for", "cannot exclude", "unconfirmed", "equivocal",
    "differential",
    # Conversational hedges. A rewrite was measured dropping or swapping
    # these ("maybe 30%" -> "around 30%", "I think" -> "I believe"), so they
    # are protected too. Raw-vs-raw validation is unaffected.
    "maybe", "perhaps", "likely", "i think", "i guess", "not sure", "unsure",
}

_ATTRIBUTION = {
    "patient reports", "patient states", "patient denies", "family states",
    "family reports", "outside records indicate", "outside note",
    "per patient", "reportedly", "does not report", "reports that",
}

_LATERALITY = {
    "left": "left", "right": "right", "bilateral": "bilateral",
    "bilaterally": "bilateral", "unilateral": "unilateral",
}


def _find_phrases(text: str, phrases: dict | set, category: Category) -> list[Token]:
    """Find whole-word phrase matches, longest first."""
    lowered = text.lower()
    items = sorted(phrases, key=len, reverse=True)
    tokens: list[Token] = []
    claimed: list[tuple[int, int]] = []

    for phrase in items:
        for m in re.finditer(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", lowered):
            span = (m.start(), m.end())
            # Skip overlaps so "every eight hours" is not also matched as "hours".
            if any(s < span[1] and span[0] < e for s, e in claimed):
                continue
            claimed.append(span)
            norm = phrases[phrase] if isinstance(phrases, dict) else phrase
            tokens.append(
                Token(category, text[span[0]:span[1]], norm, span[0], span[1])
            )
    return tokens


def extract_all(text: str) -> list[Token]:
    """Extract every protected token from `text`."""
    tokens: list[Token] = []
    tokens += extract_numbers(text)
    tokens += _find_phrases(text, _ROUTES, Category.ROUTE)
    tokens += _find_phrases(text, _FREQUENCIES, Category.FREQUENCY)
    tokens += _find_phrases(text, _UNITS, Category.UNIT)
    tokens += _find_phrases(text, _NEGATION, Category.NEGATION)
    tokens += _find_phrases(text, _UNCERTAINTY, Category.UNCERTAINTY)
    tokens += _find_phrases(text, _ATTRIBUTION, Category.ATTRIBUTION)
    tokens += _find_phrases(text, _LATERALITY, Category.LATERALITY)
    return sorted(tokens, key=lambda t: (t.start, t.category.value))


def counts_by_category(tokens: list[Token]) -> dict[Category, dict[str, int]]:
    """Multiset of normalized values per category, for order-insensitive diffing."""
    out: dict[Category, dict[str, int]] = {}
    for t in tokens:
        out.setdefault(t.category, {})
        out[t.category][t.normalized] = out[t.category].get(t.normalized, 0) + 1
    return out
