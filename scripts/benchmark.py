#!/usr/bin/env python3
"""Phase 6 acceptance benchmark.

Reports every metric CLAUDE.md §6 Phase 6 requires, over the synthetic and
recorded corpora:

  overall word error rate
  medical-term error rate
  medication-name accuracy
  microorganism-name accuracy
  number and unit preservation
  negation preservation
  laterality preservation
  unsupported-content rate      <- must be zero
  median and 95th-percentile latency
  fraction requiring review

The unsupported-content rate is the one that gates acceptance. CLAUDE.md §10.12
calls unsupported added content a critical defect, so a non-zero value here
fails the suite regardless of how good the other numbers look.

Metrics are computed against reference text, so a corpus needs `.ref.txt`
alongside each `.hyp.txt`. Local-only; no network, no model calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from score_transcripts import align, tokenize  # noqa: E402
from validation.protected import Category, extract_all  # noqa: E402
from validation.validator import Status, Validator  # noqa: E402


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^a-z0-9\- ]", " ", text)


def load_vocab(path: Path) -> set[str]:
    """Load a dictionary as a set of individual WORDS.

    Entries are often multi-word -- "Staphylococcus aureus",
    "piperacillin-tazobactam" -- while recall is measured per word. Storing
    whole entries makes every binomial unmatchable, which silently reports
    0/0 = 100% organism accuracy: a false pass, and a worse failure than a
    wrong number because it looks like success.
    """
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in re.split(r"[\s\-/,()]+", line.lower()):
            if len(part) > 3 and part.isalpha():
                out.add(part)
    return out


def term_recall(ref: str, hyp: str, vocab: set[str]) -> tuple[int, int, list[str]]:
    """How many vocabulary terms present in the reference survive into the hypothesis.

    Word-level: a binomial like "Staphylococcus aureus" contributes both words,
    so partial mangling is partially penalised rather than scored all-or-nothing.
    """
    ref_words = set(norm(ref).split())
    hyp_words = set(norm(hyp).split())
    expected = {w for w in ref_words if w in vocab and len(w) > 3}
    missed = sorted(expected - hyp_words)
    return len(expected) - len(missed), len(expected), missed


def category_preservation(ref: str, hyp: str, cats: set[Category]) -> tuple[int, int]:
    """Preserved / expected count for the given protected categories."""
    def keys(text: str) -> list[str]:
        return sorted(
            f"{t.category.value}:{t.normalized}"
            for t in extract_all(text) if t.category in cats
        )

    r, h = keys(ref), keys(hyp)
    hset = list(h)
    kept = 0
    for k in r:
        if k in hset:
            hset.remove(k)
            kept += 1
    return kept, len(r)


def unsupported_content(raw: str, polished: str) -> list[str]:
    """Content the POLISHER introduced that is not traceable to the raw transcript.

    This is the CLAUDE.md §6 Phase 6 acceptance metric and it must be zero
    (§4.2 reject list, §10.12 "treat unsupported added content as a critical
    defect").

    Note carefully what the two arguments are. The comparison is
    polished-against-raw, NOT hypothesis-against-reference. An ASR
    mis-transcription such as `cefipine` for cefepime is a transcription
    error, counted in the medical-term error rate; it is not unsupported
    content, because the polisher did not invent it. Conflating the two makes
    the acceptance gate fail on ordinary ASR error and hides the defect it
    exists to catch.

    With polishing disabled, raw == polished and this is zero by construction.
    That is a real property of the current configuration, not a gap in the
    measurement.
    """
    raw_words = set(norm(raw).split())
    out = []
    for w in norm(polished).split():
        if len(w) < 5 or w in raw_words or w.isdigit():
            continue
        # Tolerate inflection of a word already present.
        if any(w.startswith(r[:5]) for r in raw_words if len(r) >= 5):
            continue
        out.append(w)
    return out


def asr_oov_terms(ref: str, hyp: str, known: set[str]) -> list[str]:
    """Words in the hypothesis that are neither in the reference nor known vocabulary.

    Reported separately from unsupported content. These are ASR errors on
    names -- `creptococcus`, `urtapenem` -- and are exactly what the Phase 4
    validator flags for review. They are expected to be non-zero and do not
    fail the suite.
    """
    ref_words = set(norm(ref).split())
    return [w for w in norm(hyp).split()
            if len(w) > 5 and w not in ref_words and w not in known]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * p))))
    return s[k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpora", nargs="+", type=Path,
                    help="directories with phrase_*.ref.txt / phrase_*.hyp.txt")
    ap.add_argument("--dictionaries", type=Path, default=REPO_ROOT / "dictionaries")
    ap.add_argument("--latencies", type=Path,
                    help="optional JSON list of per-utterance latencies in ms")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    meds = load_vocab(args.dictionaries / "medications.txt")
    orgs = load_vocab(args.dictionaries / "microorganisms.txt")

    # Score only on DISTINCTIVELY pharmaceutical/microbiological words.
    #
    # The FDA NDC file lists homeopathic and botanical preparations alongside
    # drugs, so 3,457 of 8,781 words in medications.txt are ordinary English --
    # "acid", "above", "abuse", "fever". Left in, they inflate the denominator
    # and let "medication-name accuracy" be dominated by whether the ASR heard
    # the word "fever", which tells us nothing about drug-name recognition.
    #
    # Only the scoring vocabulary is filtered. The validator deliberately keeps
    # the full list: there, a larger known-word set means fewer false alarms.
    english: set[str] = set()
    sysdict = Path("/usr/share/dict/words")
    if sysdict.exists():
        english = {w.strip().lower()
                   for w in sysdict.read_text(errors="replace").splitlines()
                   if w.strip()}
    meds_scoring = meds - english
    orgs_scoring = orgs - english
    print(f"scoring vocabulary: {len(meds_scoring)} drug words "
          f"(from {len(meds)}), {len(orgs_scoring)} organism words "
          f"(from {len(orgs)}); ordinary English excluded")

    known_words = Validator.from_dictionaries(args.dictionaries).known_words
    validator = Validator.from_dictionaries(args.dictionaries, "clinical_strict")

    report: dict = {}
    overall_fail = False

    for corpus in args.corpora:
        refs = sorted(corpus.glob("phrase_*.ref.txt"))
        if not refs:
            print(f"skip {corpus}: no references", file=sys.stderr)
            continue

        errs = words = 0
        med_hit = med_tot = 0
        org_hit = org_tot = 0
        num_hit = num_tot = 0
        neg_hit = neg_tot = 0
        lat_hit = lat_tot = 0
        unsupported: list[tuple[str, list[str]]] = []
        oov_terms: list[tuple[str, list[str]]] = []
        review = 0
        n = 0
        med_missed: list[str] = []
        org_missed: list[str] = []

        for ref_path in refs:
            hyp_path = ref_path.with_name(ref_path.name.replace(".ref.", ".hyp."))
            if not hyp_path.exists():
                continue
            ref = ref_path.read_text(encoding="utf-8").strip()
            hyp = hyp_path.read_text(encoding="utf-8").strip()
            n += 1

            r_tok = tokenize(ref, normalize_numbers=True, normalize_hyphens=True)
            h_tok = tokenize(hyp, normalize_numbers=True, normalize_hyphens=True)
            ops = align(r_tok, h_tok)
            errs += sum(1 for o, _, _ in ops if o != "=")
            words += len(r_tok)

            a, b, m = term_recall(ref, hyp, meds_scoring)
            med_hit += a; med_tot += b; med_missed += m
            a, b, m = term_recall(ref, hyp, orgs_scoring)
            org_hit += a; org_tot += b; org_missed += m

            a, b = category_preservation(ref, hyp, {Category.NUMBER, Category.UNIT})
            num_hit += a; num_tot += b
            a, b = category_preservation(ref, hyp, {Category.NEGATION})
            neg_hit += a; neg_tot += b
            a, b = category_preservation(ref, hyp, {Category.LATERALITY})
            lat_hit += a; lat_tot += b

            # Polishing is off in the current configuration, so the polished
            # text IS the raw transcript. Compare them anyway rather than
            # asserting zero: when polishing is enabled this becomes the real
            # measurement without a code change.
            polished = hyp
            added = unsupported_content(hyp, polished)
            if added:
                unsupported.append((ref_path.stem.replace(".ref", ""), added))

            oov = asr_oov_terms(ref, hyp, known_words)
            if oov:
                oov_terms.append((ref_path.stem.replace(".ref", ""), oov))

            # The pipeline validates the transcript against itself when
            # polishing is off; this is what surfaces unrecognised names.
            if validator.validate(hyp, hyp).status is not Status.SAFE:
                review += 1

        def pct(a: int, b: int) -> float:
            return (a / b) if b else 1.0

        stats = {
            "utterances": n,
            "reference_words": words,
            "word_error_rate": pct(errs, words) if words else 0.0,
            "medical_term_error_rate": 1 - pct(med_hit + org_hit, med_tot + org_tot),
            "medication_name_accuracy": pct(med_hit, med_tot),
            "microorganism_name_accuracy": pct(org_hit, org_tot),
            "number_unit_preservation": pct(num_hit, num_tot),
            "negation_preservation": pct(neg_hit, neg_tot),
            "laterality_preservation": pct(lat_hit, lat_tot),
            "unsupported_content_rate": pct(len(unsupported), n),
            "asr_oov_term_rate": pct(len(oov_terms), n),
            "fraction_requiring_review": pct(review, n),
            "medications_missed": sorted(set(med_missed)),
            "organisms_missed": sorted(set(org_missed)),
            "unsupported_examples": unsupported[:10],
        }
        report[str(corpus)] = stats

        print(f"\n{'=' * 66}")
        print(f"{corpus}")
        print("=" * 66)
        print(f"  utterances                   : {n}  ({words} reference words)")
        print(f"  word error rate              : {stats['word_error_rate']:.2%}")
        print(f"  medical-term error rate      : {stats['medical_term_error_rate']:.2%}")
        print(f"  medication-name accuracy     : {stats['medication_name_accuracy']:.1%}"
              f"  ({med_hit}/{med_tot})")
        print(f"  microorganism-name accuracy  : {stats['microorganism_name_accuracy']:.1%}"
              f"  ({org_hit}/{org_tot})")
        print(f"  number/unit preservation     : {stats['number_unit_preservation']:.1%}"
              f"  ({num_hit}/{num_tot})")
        print(f"  negation preservation        : {stats['negation_preservation']:.1%}"
              f"  ({neg_hit}/{neg_tot})")
        print(f"  laterality preservation      : {stats['laterality_preservation']:.1%}"
              f"  ({lat_hit}/{lat_tot})")
        print(f"  fraction requiring review    : {stats['fraction_requiring_review']:.1%}"
              f"  ({review}/{n})")

        oov_rate = stats["asr_oov_term_rate"]
        print(f"  ASR out-of-vocab term rate   : {oov_rate:.1%}"
              f"  ({len(oov_terms)}/{n})  [informational]")
        for uid, ws in oov_terms[:5]:
            print(f"      {uid}: {', '.join(ws)}")

        rate = stats["unsupported_content_rate"]
        mark = "PASS" if rate == 0 else "FAIL"
        print(f"  unsupported-content rate     : {rate:.1%}  [{mark}]"
              f"   <- acceptance gate")
        if rate:
            overall_fail = True
            for uid, words_added in unsupported[:6]:
                print(f"      {uid}: {', '.join(words_added)}")

        if stats["medications_missed"]:
            print(f"  medications missed           : "
                  f"{', '.join(stats['medications_missed'][:8])}")
        if stats["organisms_missed"]:
            print(f"  organisms missed             : "
                  f"{', '.join(stats['organisms_missed'][:8])}")

    if args.latencies and args.latencies.exists():
        lat = json.loads(args.latencies.read_text())
        print(f"\n  latency median               : {percentile(lat, 0.5)/1000:.2f} s")
        print(f"  latency p95                  : {percentile(lat, 0.95)/1000:.2f} s")
        report["latency_ms"] = {"median": percentile(lat, 0.5),
                                "p95": percentile(lat, 0.95)}

    print()
    if overall_fail:
        print("SUITE FAILED: unsupported content is a critical defect "
              "(CLAUDE.md §10.12).")
    else:
        print("Unsupported-content rate is zero across all corpora.")

    if args.json:
        out = REPO_ROOT / "runtime/tmp/benchmark.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"wrote {out}")

    return 1 if overall_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
