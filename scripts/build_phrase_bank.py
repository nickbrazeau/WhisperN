#!/usr/bin/env python3
"""Generate a phrase bank for a real-voice evaluation corpus.

WHY. The headline risk number for this project -- 62.5% medication-name
accuracy, "roughly one drug name in three is wrong" -- rests on five correct
observations out of eight. Its 95% confidence interval runs from 31% to 86%,
so it cannot distinguish a tool that mangles most drug names from one that
gets six in seven right. Every conservative decision in the project is priced
against a number nobody can currently read. This script exists to widen the
denominator.

WHAT IT PRODUCES. Constructed phrases to read aloud, one per line, in the
format scripts/record_baseline.sh consumes, plus a manifest recording which
terms each phrase was built to exercise. The manifest is what makes the
result diagnosable: without it you learn the error rate but not which class
of term drove it.

THE SPLIT THAT MATTERS. Whisper's initial prompt currently biases decoding
toward sixteen terms, and docs/phase2-results.md found that benefit does not
generalize beyond them. Sixteen of 13,789 medications is not a realistic
operating point, so every phrase is tagged `in_prompt` or `out_of_prompt` and
the two can be scored separately. Measuring them together would report a
number that is neither.

Deterministic given --seed, so a corpus can be regenerated and a result
reproduced.

Local-only. No network. Reads config/corpus_terms.yaml and nothing else.

Usage:
  scripts/build_phrase_bank.py --count 100 --out runtime/tmp/bank
  scripts/build_phrase_bank.py --count 100 --out runtime/tmp/bank --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Categories whose recognition accuracy is the point of the exercise. A phrase
# earns its recording time by containing at least one of these.
NAMED_ENTITY_SECTIONS = {
    "antibacterials", "antifungals", "antivirals", "antiretrovirals",
    "antimycobacterials", "organisms_gram_positive", "organisms_gram_negative",
    "organisms_anaerobe", "organisms_mycobacteria", "organisms_fungal",
    "organisms_other", "resistance_phenotypes",
}

DRUG_SECTIONS = {
    "antibacterials", "antifungals", "antivirals", "antiretrovirals",
    "antimycobacterials",
}


def load_terms(path: Path) -> dict[str, list[str]]:
    """Parse the term bank.

    Hand-rolled rather than PyYAML, matching scripts/build_asr_prompt.py: this
    runs on a machine that must work with the network disabled, and the file's
    structure is a flat mapping of section to list.
    """
    if not path.exists():
        sys.exit(f"error: term bank not found: {path}")

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith((" ", "-")) and line.strip().endswith(":"):
            current = line.strip()[:-1]
            sections[current] = []
        elif line.strip().startswith("- ") and current:
            sections[current].append(line.strip()[2:].strip())

    empty = [k for k, v in sections.items() if not v]
    if empty:
        sys.exit(f"error: empty sections in {path}: {', '.join(empty)}")
    if not sections:
        sys.exit(f"error: no sections parsed from {path}")
    return sections


def load_prompt_terms(path: Path) -> set[str]:
    """Terms currently biased in Whisper's initial prompt, lowercased.

    Absent prompt file is not an error: it means nothing is biased yet, and
    every phrase is then correctly tagged out_of_prompt.
    """
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    # The prompt is prose; the terms sit in the "includes ..." clause.
    m = re.search(r"includes\s+(.*?)\.\s*Preserve", text, re.S | re.I)
    body = m.group(1) if m else text
    return {t.strip().lower() for t in re.split(r",|\band\b", body) if t.strip()}


# Templates, grouped by the test group each satisfies (CLAUDE.md section 6,
# Phase 6). Slots in braces are filled from the term bank.
#
# Written to be read aloud without stumbling: a phrase the speaker garbles
# measures the speaker, not the model.
#
# THE WEIGHTS ARE THE DESIGN. The scarce resource is the reader's time, not
# phrases, and a sentence carrying no drug or organism name costs a recording
# without widening the denominator this corpus exists to widen. So templates
# carrying two named entities are weighted an order of magnitude above pure
# coverage lines.
#
# The low-weight groups are not decoration. Numbers, negation, laterality, and
# unsupported-content already score 100% / 0%, and a corpus that dropped them
# could not notice a regression. They need presence, not volume -- which is
# exactly what a low weight buys.
TEMPLATES: list[tuple[str, int, str]] = [
    # --- organism plus drug: two entities per recording, the best value ------
    ("organism_drug", 10, "{organism} bacteremia, treated with {drug}."),
    ("organism_drug", 10, "Cultures grew {organism}, so we started {drug}."),
    ("organism_drug", 10, "The {organism} was susceptible to {drug}."),
    ("organism_drug", 10, "We are treating {organism} with {drug} {dose} {freq}."),
    ("organism_drug", 10, "{organism} was resistant to {drug}, so we changed to {drug2}."),
    ("organism_drug",  9, "Given {organism}, continue {drug} for {duration}."),
    ("organism_drug",  9, "The {organism} isolate is being treated with {drug}."),

    # --- medication and dose ------------------------------------------------
    ("medication_dose", 9, "Continue {drug} {dose} {route} {freq}."),
    ("medication_dose", 9, "Start {drug} {dose} {route} {freq} for {duration}."),
    ("medication_dose", 9, "We will narrow therapy from {drug} to {drug2}."),
    ("medication_dose", 8, "Reduce {drug} to {dose} {freq} for renal dosing."),
    ("medication_dose", 8, "Stop {drug} and start {drug2} {dose} {route}."),
    ("medication_dose", 7, "Transition to oral {drug} to complete {duration}."),
    ("medication_dose", 6, "Hold {drug} pending {lab}."),

    # --- organisms ----------------------------------------------------------
    ("organism", 9, "The wound culture grew {organism} and {organism2}."),
    ("organism", 8, "Blood cultures grew {organism}."),
    ("organism", 8, "The isolate was identified as {organism}."),
    ("organism", 7, "Respiratory cultures are positive for {organism}."),
    ("organism", 7, "Two of four bottles grew {organism}."),
    ("organism", 6, "Susceptibilities on the {organism} are pending."),

    # --- negation, carrying an entity where it can ---------------------------
    ("negation", 5, "No {organism} was recovered from the {site}."),
    ("negation", 5, "There is no evidence of {syndrome} on {procedure}."),
    ("negation", 4, "Cultures show no growth of {organism} to date."),
    ("negation", 2, "The patient denies fever, chills, and night sweats."),
    ("negation", 2, "Repeat blood cultures remain negative at {duration}."),
    ("negation", 2, "The patient does not report dysuria or flank pain."),

    # --- uncertainty --------------------------------------------------------
    ("uncertainty", 5, "This may represent contaminant {organism}."),
    ("uncertainty", 5, "It is unclear whether the {organism} is a true pathogen."),
    ("uncertainty", 4, "Possible {syndrome}; we have started empiric {drug}."),
    ("uncertainty", 2, "{syndrome} remains possible but unconfirmed."),
    ("uncertainty", 2, "The differential includes {syndrome} and {syndrome2}."),
    ("uncertainty", 2, "Outside records suggest a probable {syndrome}."),

    # --- numbers and units --------------------------------------------------
    ("numbers", 3, "Laboratory studies show {lab}."),
    ("numbers", 2, "The patient has had {duration} of fevers."),
    ("numbers", 2, "Repeat imaging in {duration} if symptoms persist."),

    # --- laterality ---------------------------------------------------------
    ("laterality", 3, "There is erythema of the left {site}."),
    ("laterality", 2, "The right {site} is more swollen than the left."),
    ("laterality", 2, "{procedure} of the left {site} was unremarkable."),
    ("laterality", 2, "Bilateral {site} edema is unchanged."),

    # --- ambiguous abbreviations, which must NOT be expanded -----------------
    ("abbreviation", 3, "The patient will follow up with {abbrev}."),
    ("abbreviation", 3, "The outside note lists {abbrev} in the history."),

    # --- research terminology -----------------------------------------------
    ("research", 3, "We fit a {research} to the outcome data."),
    ("research", 3, "Isolates of {organism} were compared by {research}."),
    ("research", 2, "The analysis used {research} and {research2}."),

    # --- procedures and syndromes -------------------------------------------
    ("procedure", 2, "{procedure} showed no vegetation."),
    ("procedure", 2, "We are arranging {procedure} for source control."),
    ("syndrome",  2, "The working diagnosis is {syndrome} of the {site}."),
    ("syndrome",  2, "This presentation is most consistent with {syndrome}."),
]


class Filler:
    """Fills template slots, spreading terms evenly rather than sampling.

    Uniform random sampling with replacement gives a lumpy corpus: at n=100
    some drugs appear four times and a third of the list never appears at all.
    Since the whole point is to widen a denominator, every term should get a
    turn before any term gets a second. So each pool is shuffled and consumed
    in order, reshuffling only when exhausted.
    """

    def __init__(self, terms: dict[str, list[str]], rng: random.Random) -> None:
        self.terms = terms
        self.rng = rng
        self._queues: dict[str, list[str]] = {}

    def _pool(self, sections: list[str]) -> list[str]:
        out: list[str] = []
        for s in sections:
            out.extend(self.terms.get(s, []))
        return out

    def take(self, key: str, sections: list[str], exclude: set[str]) -> str:
        q = self._queues.get(key)
        if not q:
            q = self._pool(sections)
            self.rng.shuffle(q)
            self._queues[key] = q
        for i, term in enumerate(q):
            if term not in exclude:
                return q.pop(i)
        # Every remaining term is already in this phrase; refill and retry.
        q = self._pool(sections)
        self.rng.shuffle(q)
        self._queues[key] = q
        return q.pop()


ORGANISM_SECTIONS = ["organisms_gram_positive", "organisms_gram_negative",
                     "organisms_anaerobe", "organisms_mycobacteria",
                     "organisms_fungal", "organisms_other",
                     "resistance_phenotypes"]
DRUG_LIST = sorted(DRUG_SECTIONS)

SLOTS = {
    "drug":       DRUG_LIST,
    "drug2":      DRUG_LIST,
    "organism":   ORGANISM_SECTIONS,
    "organism2":  ORGANISM_SECTIONS,
    "syndrome":   ["syndromes"],
    "syndrome2":  ["syndromes"],
    "site":       ["sites"],
    "procedure":  ["procedures"],
    "abbrev":     ["abbreviations_ambiguous"],
    "research":   ["research_terms"],
    "research2":  ["research_terms"],
    "dose":       ["doses"],
    "route":      ["routes"],
    "freq":       ["frequencies"],
    "duration":   ["durations"],
    "lab":        ["lab_values"],
}

# Slots drawn from the same pool must not collide inside one phrase.
ALIASES = {"drug2": "drug", "organism2": "organism",
           "syndrome2": "syndrome", "research2": "research"}

# Which drug classes may treat which organism class.
#
# Without this the generator paired Streptococcus pneumoniae with terbinafine
# and narrowed oseltamivir to raltegravir. That is not merely untidy: Whisper
# is a language model and leans on context, so a drug name in an impossible
# clinical frame is recognised under conditions that never occur in real
# dictation. The measured error rate would then describe the corpus rather
# than the tool -- most likely pessimistically.
#
# Antivirals and antiretrovirals appear only in drug-only templates, since the
# organism bank holds no viruses to pair them with.
ORGANISM_TO_DRUG = {
    "organisms_gram_positive": ["antibacterials"],
    "organisms_gram_negative": ["antibacterials"],
    "organisms_anaerobe":      ["antibacterials"],
    "organisms_other":         ["antibacterials"],
    "resistance_phenotypes":   ["antibacterials"],
    "organisms_fungal":        ["antifungals"],
    "organisms_mycobacteria":  ["antimycobacterials"],
}

# Organisms are resolved before drugs so the drug pool can be narrowed to what
# could actually treat the organism just chosen; drugs before everything else
# so a second drug can be held to the first one's class.
SLOT_ORDER = ["organism", "organism2", "drug", "drug2"]


def build(terms: dict[str, list[str]], count: int, seed: int,
          prompt_terms: set[str], min_per_group: int = 3) -> list[dict]:
    rng = random.Random(seed)
    filler = Filler(terms, rng)

    # Expand each template into `weight` copies, then shuffle and consume. A
    # weighted pool rather than weighted random choice, so the realised mix
    # matches the intended one at n=100 instead of merely converging to it --
    # sampling with replacement leaves visible lumps at these sizes.
    pool: list[tuple[str, int, str]] = []
    for entry in TEMPLATES:
        pool.extend([entry] * entry[1])

    order: list[tuple[str, int, str]] = []
    while len(order) < count:
        batch = pool[:]
        rng.shuffle(batch)
        order.extend(batch)
    order = order[:count]

    # Guarantee a floor per test group. Pure weighting starves the rare groups
    # -- at n=100 it produced one `research` phrase and no `procedure` at all,
    # which would leave those groups unable to catch a regression. Top up from
    # whichever group is most over-represented, so the cost lands on the group
    # that can most afford it.
    by_group: dict[str, list[tuple[str, int, str]]] = {}
    for g, w, t in TEMPLATES:
        by_group.setdefault(g, []).append((g, w, t))

    for group in sorted(by_group):
        have = sum(1 for g, _, _ in order if g == group)
        for _ in range(max(0, min_per_group - have)):
            counts = Counter(g for g, _, _ in order)
            donor = max(counts, key=lambda g: counts[g])
            if counts[donor] - 1 < min_per_group:
                break   # nothing left to take without breaking another floor
            idx = next(i for i, (g, _, _) in enumerate(order) if g == donor)
            order[idx] = rng.choice(by_group[group])

    section_of = {t: s for s, items in terms.items() for t in items}
    phrases: list[dict] = []

    for i, (group, _weight, template) in enumerate(order, start=1):
        used: dict[str, set[str]] = {}
        values: dict[str, str] = {}

        slots = list(dict.fromkeys(re.findall(r"\{(\w+)\}", template)))
        slots.sort(key=lambda s: SLOT_ORDER.index(s) if s in SLOT_ORDER
                   else len(SLOT_ORDER))

        for slot in slots:
            pool_key = ALIASES.get(slot, slot)
            sections = SLOTS[slot]

            # Narrow the drug pool to something that could treat the organism
            # already chosen, and hold a second drug to the first one's class.
            if slot == "drug" and "organism" in values:
                org_section = section_of.get(values["organism"])
                sections = ORGANISM_TO_DRUG.get(org_section, sections)
            elif slot == "drug2" and "drug" in values:
                sections = [section_of.get(values["drug"], "antibacterials")]

            excl = used.setdefault(pool_key, set())
            term = filler.take(f"{pool_key}:{'+'.join(sections)}", sections, excl)
            excl.add(term)
            values[slot] = term

        text = template.format(**values)
        # Templates may open with a slot, which leaves the sentence starting in
        # lower case ("thoracentesis showed no vegetation.").
        text = text[0].upper() + text[1:]

        # Only named entities count as targets: a phrase's value is the drug
        # and organism names it puts on the record, not its adverbs.
        targets = [
            {
                "term": v,
                "section": section_of.get(v, "?"),
                "kind": ("drug" if section_of.get(v) in DRUG_SECTIONS
                         else "organism"),
                "in_prompt": v.lower() in prompt_terms,
            }
            for v in values.values()
            if section_of.get(v) in NAMED_ENTITY_SECTIONS
        ]

        phrases.append({
            "id": f"phrase_{i:03d}",
            "text": text,
            "group": group,
            "targets": targets,
            # A phrase counts as prompt-assisted if ANY of its named entities
            # is biased. Scoring the halves separately is the whole point.
            "in_prompt": any(t["in_prompt"] for t in targets),
        })

    return phrases


def report(phrases: list[dict]) -> None:
    groups = Counter(p["group"] for p in phrases)
    drugs = [t for p in phrases for t in p["targets"] if t["kind"] == "drug"]
    orgs = [t for p in phrases for t in p["targets"] if t["kind"] == "organism"]
    prompted = sum(1 for t in drugs + orgs if t["in_prompt"])

    print(f"\n{len(phrases)} phrases\n")
    print("  test group coverage")
    for g, n in sorted(groups.items(), key=lambda kv: -kv[1]):
        print(f"    {g:<18} {n:>4}")

    print("\n  named-entity observations (the denominators being widened)")
    print(f"    drug names       {len(drugs):>4}  ({len(set(t['term'] for t in drugs))} distinct)")
    print(f"    organism names   {len(orgs):>4}  ({len(set(t['term'] for t in orgs))} distinct)")
    print(f"    in Whisper's prompt      {prompted:>4}")
    print(f"    NOT in the prompt        {len(drugs) + len(orgs) - prompted:>4}"
          "   <- the realistic operating point")

    # What the corpus can actually resolve, so nobody over-reads the result.
    n = len(drugs)
    if n:
        from math import sqrt
        p, z = 0.625, 1.96
        half = z * sqrt(p * (1 - p) / n) / (1 + z * z / n) * 100
        print(f"\n  at n={n} drug names, a 62.5% result would carry roughly "
              f"+/-{half:.0f} points\n  (it is currently +/-28 at n=8)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=100,
                    help="phrases to generate (default 100)")
    ap.add_argument("--out", type=Path, required=True,
                    help="output directory for phrases.txt and manifest.json")
    ap.add_argument("--seed", type=int, default=1,
                    help="RNG seed; same seed gives the same bank")
    ap.add_argument("--min-per-group", type=int, default=3,
                    help="floor per test group, so rare groups keep regression "
                         "coverage (default 3)")
    ap.add_argument("--terms", type=Path,
                    default=REPO_ROOT / "config/corpus_terms.yaml")
    ap.add_argument("--prompt-file", type=Path,
                    default=REPO_ROOT / "runtime/tmp/asr_prompt_id.txt",
                    help="ASR prompt, to tag which terms are already biased")
    args = ap.parse_args()

    if args.count < 1:
        sys.exit("error: --count must be at least 1")

    terms = load_terms(args.terms)
    prompt_terms = load_prompt_terms(args.prompt_file)
    phrases = build(terms, args.count, args.seed, prompt_terms,
                    args.min_per_group)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "phrases.txt").write_text(
        "\n".join(p["text"] for p in phrases) + "\n", encoding="utf-8")
    (args.out / "manifest.json").write_text(
        json.dumps({"seed": args.seed, "count": len(phrases),
                    "phrases": phrases}, indent=2) + "\n", encoding="utf-8")

    report(phrases)
    print(f"  wrote {args.out}/phrases.txt")
    print(f"  wrote {args.out}/manifest.json\n")
    print("next:")
    print(f"  scripts/record_baseline.sh {args.out}/phrases.txt {args.out}")
    print(f"  scripts/transcribe_all.sh {args.out}")
    print(f"  scripts/benchmark.py {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
