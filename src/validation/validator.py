"""Deterministic validation of polished text against the raw transcript.

CLAUDE.md §4.2. The validator trusts neither the ASR nor the polishing model.
Both were observed fabricating content during development:

  * the ASR turned "in the past medical history" into "in the past Michael
    Hex 3" (docs/baseline-errors.md §7a)
  * the polisher turned "MS" into "methicillin-sensitive Staphylococcus
    aureus (MS)" (docs/phase3-results.md §3.1)

Neither stage can catch the other's invention, so this module is the only
component in the pipeline that would catch either.

Status values:
  safe_to_paste   -- no protected content changed
  review_required -- protected content changed; a human must look
  rejected        -- the output is unusable or unsupported by the raw text

An important limitation, stated plainly: the raw transcript is the reference,
so anything the *ASR* invented is inside the trusted baseline and cannot be
detected here. That gap belongs to voice-activity detection and human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .protected import Category, Token, counts_by_category, extract_all


class Status(str, Enum):
    SAFE = "safe_to_paste"
    REVIEW = "review_required"
    REJECTED = "rejected"


# Categories whose change is never acceptable without review. Changing any of
# these alters what happened to the patient.
_CRITICAL = {
    Category.NEGATION,
    Category.NUMBER,
    Category.DOSE,
    Category.LATERALITY,
    Category.ROUTE,
    Category.FREQUENCY,
}

# Categories that force outright rejection when they change, per CLAUDE.md
# §4.2's reject list.
_REJECT_ON_CHANGE = {
    Category.NEGATION,
    Category.LATERALITY,
    Category.NUMBER,
    Category.ROUTE,
    Category.FREQUENCY,
}


@dataclass
class ProtectedChange:
    category: str
    raw_value: str
    polished_value: str
    detail: str


@dataclass
class ValidationResult:
    status: Status
    raw_text: str
    polished_text: str
    protected_changes: list[ProtectedChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "raw_text": self.raw_text,
            "polished_text": self.polished_text,
            "protected_changes": [vars(c) for c in self.protected_changes],
            "warnings": self.warnings,
        }


def _sentence_count(text: str) -> int:
    parts = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    return len(parts)


def _content_words(text: str) -> set[str]:
    """Lowercased alphabetic words of length >= 4, for traceability checks."""
    return {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower())}


class Validator:
    """Compare raw and polished text for protected-content changes."""

    # System word list. web2 is an old dictionary and misses modern medical
    # vocabulary entirely (it has no "vancomycin"), which is exactly why it is
    # only ever consulted together with the medical dictionaries.
    _SYSTEM_WORDS = Path("/usr/share/dict/words")

    def __init__(
        self,
        medications: set[str] | None = None,
        organisms: set[str] | None = None,
        mode: str = "clinical_strict",
        known_words: set[str] | None = None,
    ) -> None:
        self.medications = medications or set()
        self.organisms = organisms or set()
        self.mode = mode
        # Union of ordinary English and every word appearing in the medical
        # vocabularies, including the parts of binomials and hyphenated names.
        self.known_words = known_words if known_words is not None else set()

    @staticmethod
    def _split_words(terms: set[str]) -> set[str]:
        out: set[str] = set()
        for term in terms:
            for part in re.split(r"[\s\-/,()]+", term):
                part = part.strip().lower()
                if len(part) >= 3 and part.isalpha():
                    out.add(part)
        return out

    def _load_known_words(self, extra_dictionaries: list[Path] | None = None) -> set[str]:
        words: set[str] = set()
        if self._SYSTEM_WORDS.exists():
            words = {
                w.strip().lower()
                for w in self._SYSTEM_WORDS.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if w.strip()
            }
        words |= self._split_words(self.medications)
        words |= self._split_words(self.organisms)

        # The curated clinical dictionaries carry modern vocabulary that web2,
        # a 1934 word list, predates entirely: methicillin, nephrolithiasis,
        # echocardiogram, Bayesian.
        for path in extra_dictionaries or []:
            if not path.exists():
                continue
            terms = {
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            }
            words |= self._split_words(terms)
        return words

    def _is_known(self, word: str) -> bool:
        """Membership test tolerant of regular English inflection.

        web2 lists base forms only, so "show" is present but "showed" is not.
        Without this, ordinary narrative verbs are reported as unrecognized
        medical terms -- and a validator that flags "showed" gets ignored,
        taking the real warnings down with it.
        """
        w = word.lower()
        if w in self.known_words:
            return True

        candidates: list[str] = []
        if w.endswith("s"):
            candidates.append(w[:-1])
        if w.endswith("es"):
            candidates.append(w[:-2])
        if w.endswith("ies"):
            candidates.append(w[:-3] + "y")
        if w.endswith("ied"):                      # "identified" -> "identify"
            candidates.append(w[:-3] + "y")
        if w.endswith("ed"):
            candidates += [w[:-2], w[:-1]]
            if len(w) > 4 and w[-3] == w[-4]:      # "stopped" -> "stop"
                candidates.append(w[:-3])
        if w.endswith("ing"):
            candidates += [w[:-3], w[:-3] + "e"]
            if len(w) > 5 and w[-4] == w[-5]:      # "running" -> "run"
                candidates.append(w[:-4])
        if w.endswith("ly"):
            candidates.append(w[:-2])
        if w.endswith("er"):                       # comparative: "larger"
            candidates += [w[:-1], w[:-2]]
        if w.endswith("est"):                      # superlative: "largest"
            candidates += [w[:-2], w[:-3]]
        if w.endswith("al"):
            candidates.append(w[:-2])

        return any(c in self.known_words for c in candidates)

    # -- vocabulary loading ------------------------------------------------

    @classmethod
    def from_dictionaries(
        cls, directory: Path, mode: str = "clinical_strict"
    ) -> "Validator":
        """Load the large validation vocabularies.

        These are the `validation` lists from config/specialties.yaml, not the
        `prompt` lists: ~21,000 terms whose whole purpose is to answer "is this
        a real drug/organism name?"
        """
        def load(name: str) -> set[str]:
            path = directory / name
            if not path.exists():
                return set()
            out = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.add(line.lower())
            return out

        inst = cls(
            medications=load("medications.txt"),
            organisms=load("microorganisms.txt"),
            mode=mode,
        )
        # Include the curated clinical dictionaries as known vocabulary.
        #
        # institutions_and_people.local.txt is the local, gitignored list of
        # proper nouns (CLAUDE.md §5). Without it the unknown-word check fires
        # on any proper noun of six or more letters that web2 does not carry --
        # "Google" in ordinary email, and every colleague and institution name.
        # That is review fatigue, and a review nobody reads is worse than no
        # review: it trains the habit of clicking through the dialog that also
        # guards mangled drug names.
        #
        # It is loaded last and, like the others, skipped when absent, so a
        # fresh checkout behaves exactly as before.
        extra = [directory / n for n in (
            "general_medicine.txt", "infectious_diseases.txt",
            "procedures.txt", "research_terms.txt",
            "institutions_and_people.local.txt",
        )]
        inst.known_words = inst._load_known_words(extra)
        return inst

    # -- name checks -------------------------------------------------------

    def _unrecognized(self, text: str) -> list[str]:
        """Words that are neither ordinary English nor known medical vocabulary.

        Suffix matching was tried first and is not sufficient: the real ASR
        output `pipercellum` (for piperacillin) retains no drug-like suffix at
        all, so a suffix rule misses precisely the badly-mangled names that
        matter most.

        Membership testing catches those. It cannot catch a *real* word used
        wrongly -- the ASR once produced `Pneumocystis cerevisiae`, and
        `cerevisiae` is a genuine organism -- so this is a floor on detection,
        not a ceiling. See docs/phase4-results.md.
        """
        if not self.known_words:
            return []
        return [w for w in re.findall(r"[A-Za-z]{6,}", text) if not self._is_known(w)]

    def unknown_medication_like(self, raw: str, polished: str) -> list[str]:
        """Unrecognized words that polishing introduced.

        Only words absent from the raw transcript are reported; anything the
        ASR produced is handled by `unknown_names_in_raw`.
        """
        raw_words = _content_words(raw)
        return [w for w in self._unrecognized(polished) if w.lower() not in raw_words]

    def unknown_names_in_raw(self, raw: str) -> list[str]:
        """Unrecognized words in the raw transcript.

        These indicate probable ASR error on a name. The validator cannot
        correct them -- guessing which antibiotic was meant is exactly what
        CLAUDE.md forbids -- but it can refuse to let them pass silently.
        """
        return self._unrecognized(raw)

    # -- main entry point --------------------------------------------------

    def validate(self, raw_text: str, polished_text: str) -> ValidationResult:
        warnings: list[str] = []
        changes: list[ProtectedChange] = []

        # --- hard rejections ---------------------------------------------
        if not polished_text or not polished_text.strip():
            return ValidationResult(
                Status.REJECTED, raw_text, polished_text,
                warnings=["polished text is empty"],
            )

        raw_sentences = _sentence_count(raw_text)
        pol_sentences = _sentence_count(polished_text)
        if pol_sentences > raw_sentences:
            return ValidationResult(
                Status.REJECTED, raw_text, polished_text,
                warnings=[
                    f"polished text added a sentence "
                    f"({raw_sentences} -> {pol_sentences}); content must be "
                    f"traceable to the raw transcript"
                ],
            )

        # --- protected token comparison ----------------------------------
        raw_tokens = extract_all(raw_text)
        pol_tokens = extract_all(polished_text)
        raw_counts = counts_by_category(raw_tokens)
        pol_counts = counts_by_category(pol_tokens)

        reject = False
        for category in set(raw_counts) | set(pol_counts):
            rc = raw_counts.get(category, {})
            pc = pol_counts.get(category, {})
            if rc == pc:
                continue

            for value, n in rc.items():
                if pc.get(value, 0) < n:
                    changes.append(ProtectedChange(
                        category.value, value,
                        ", ".join(sorted(pc)) or "(absent)",
                        f"{category.value} {value!r} lost or reduced "
                        f"({n} -> {pc.get(value, 0)})",
                    ))
                    if category in _REJECT_ON_CHANGE:
                        reject = True

            for value, n in pc.items():
                if rc.get(value, 0) < n:
                    changes.append(ProtectedChange(
                        category.value, ", ".join(sorted(rc)) or "(absent)",
                        value,
                        f"{category.value} {value!r} added or increased "
                        f"({rc.get(value, 0)} -> {n})",
                    ))
                    if category in _REJECT_ON_CHANGE:
                        reject = True

        # --- fabricated medication names ---------------------------------
        introduced = self.unknown_medication_like(raw_text, polished_text)
        if introduced:
            reject = True
            warnings.append(
                "polished text introduced drug-like words absent from the raw "
                f"transcript and absent from the approved vocabulary: "
                f"{', '.join(sorted(set(introduced)))}"
            )

        # --- unrecognised names already in the raw transcript -------------
        unknown_raw = self.unknown_names_in_raw(raw_text)
        if unknown_raw:
            warnings.append(
                "raw transcript contains drug-like words not in the approved "
                f"vocabulary (probable ASR error): "
                f"{', '.join(sorted(set(unknown_raw)))}. "
                "The correct name cannot be inferred; human review required."
            )

        # --- untraceable content -----------------------------------------
        raw_words = _content_words(raw_text)
        pol_words = _content_words(polished_text)
        added = pol_words - raw_words
        # Words the polisher may legitimately introduce are limited; anything
        # substantive that is new is unsupported by the raw transcript.
        if added:
            warnings.append(
                "polished text contains words not present in the raw "
                f"transcript: {', '.join(sorted(added))}"
            )
            if len(added) > 2:
                reject = True

        # --- decide -------------------------------------------------------
        if reject:
            return ValidationResult(
                Status.REJECTED, raw_text, polished_text, changes, warnings
            )

        if changes or unknown_raw:
            return ValidationResult(
                Status.REVIEW, raw_text, polished_text, changes, warnings
            )

        if warnings and self.mode == "clinical_strict":
            # In clinical_strict any warning forces review. CLAUDE.md §4.2:
            # never auto-paste a review_required result in this mode.
            return ValidationResult(
                Status.REVIEW, raw_text, polished_text, changes, warnings
            )

        return ValidationResult(
            Status.SAFE, raw_text, polished_text, changes, warnings
        )
