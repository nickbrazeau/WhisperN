"""Deterministic normalization applied to a raw transcript before polishing.

CLAUDE.md §3.4 permits only low-risk transformations here:

  * collapse repeated whitespace
  * normalize sentence spacing
  * standardize Unicode apostrophes and quotation marks
  * preserve line breaks that encode structure

and explicitly forbids altering numbers, units, medication names,
abbreviations, or negation.

The guiding property is that this stage must be **meaning-preserving by
construction**, not by careful implementation. Every operation below is either
whitespace-only or a character-level substitution within a closed set of
typographic variants. Nothing here inspects words, so nothing here can change
one.
"""

from __future__ import annotations

import re
import unicodedata

# Typographic variants -> ASCII. These are presentation characters; swapping
# them cannot alter a number, a drug name, or a negation.
_PUNCT_MAP = {
    "‘": "'",   # left single quote
    "’": "'",   # right single quote / apostrophe
    "‚": "'",
    "‛": "'",
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "„": '"',
    "′": "'",   # prime
    "″": '"',   # double prime
    " ": " ",   # non-breaking space
    " ": " ",   # figure space
    " ": " ",   # narrow no-break space
    "​": "",    # zero-width space
    "﻿": "",    # BOM
}

# Deliberately NOT mapped:
#   en dash / em dash  - can carry range meaning ("2-3 days"); rewriting risks
#                        altering how a number range reads.
#   minus sign         - sign of a value.
#   micro sign         - a unit.
# Leaving these alone costs nothing; changing them could change meaning.

_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_LEADING_WS = re.compile(r"^[ \t]+", re.MULTILINE)
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_BLANK = re.compile(r"\n{3,}")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")

# Sentence spacing: exactly one space after terminal punctuation when followed
# by a capital letter. Restricted to a following capital so that decimals
# ("0.9"), abbreviations ("C. difficile"), and ellipses are untouched.
_SENTENCE_SPACING = re.compile(r"([.!?])[ \t]{2,}(?=[A-Z])")


def normalize_transcript(text: str) -> str:
    """Return `text` with whitespace and typography normalized.

    Guarantees, in the sense that the implementation cannot do otherwise:
      * no word is added, removed, or respelled
      * no digit is changed
      * no line break that separates non-empty lines is removed
    """
    if not text:
        return ""

    # NFC composes accents without reordering or dropping characters. NFKC is
    # deliberately avoided: it rewrites compatibility characters, and would
    # turn superscripts and some ligatures into different text.
    text = unicodedata.normalize("NFC", text)

    for src, dst in _PUNCT_MAP.items():
        text = text.replace(src, dst)

    # Normalize line endings before any line-oriented work.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _SENTENCE_SPACING.sub(r"\1 ", text)

    text = _TRAILING_WS.sub("", text)
    text = _LEADING_WS.sub("", text)

    # Collapse runs of blank lines to a single blank line. Paragraph structure
    # is preserved; only the amount of vertical space changes.
    text = _MULTI_BLANK.sub("\n\n", text)

    return text.strip()


def normalization_diff(raw: str, normalized: str) -> list[str]:
    """Describe what normalization changed, for auditing.

    Compares the two texts with all whitespace removed. Any difference at that
    level means normalization altered something beyond spacing and typography,
    which would be a defect in this module.
    """
    def skeleton(s: str) -> str:
        return re.sub(r"\s+", "", s)

    issues = []
    raw_sk = skeleton(raw)
    norm_sk = skeleton(normalized)

    if raw_sk != norm_sk:
        # Apply the same typographic mapping to the raw skeleton before
        # comparing, since those substitutions are expected.
        for src, dst in _PUNCT_MAP.items():
            raw_sk = raw_sk.replace(src, dst)
        raw_sk = unicodedata.normalize("NFC", raw_sk)
        if raw_sk != norm_sk:
            issues.append(
                "normalization altered non-whitespace content; this is a bug"
            )

    if len(re.findall(r"\d", raw)) != len(re.findall(r"\d", normalized)):
        issues.append("digit count changed during normalization")

    return issues
