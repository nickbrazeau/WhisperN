#!/usr/bin/env python3
"""Build medical dictionaries from downloaded authoritative sources.

Sources (both public domain, both fetched at setup time -- never at runtime):

  * FDA National Drug Code Directory -- generic drug substance names.
    https://www.accessdata.fda.gov/cder/ndctext.zip
  * NCBI Taxonomy -- organism binomials.
    https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip

Design note on organisms: NCBI carries ~2.5M names. Shipping all of them would
be actively harmful -- an ASR/validation vocabulary containing every
environmental bacterium invites false matches on terms no clinician will ever
dictate. Instead, species are drawn only from a curated whitelist of genera
that occur in clinical practice, so spellings are authoritative while the
vocabulary stays clinically meaningful.

Usage:
  scripts/build_dictionaries.py --ndc <product.txt> --taxonomy <dir> --out dictionaries/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Genera encountered in clinical infectious diseases: bacteria, fungi, and
# parasites. Not exhaustive; extend deliberately, and see CLAUDE.md section 8
# for the process when adding terms.
CLINICAL_GENERA = {
    # Gram-positive cocci and bacilli
    "Staphylococcus", "Streptococcus", "Enterococcus", "Listeria",
    "Corynebacterium", "Bacillus", "Clostridium", "Clostridioides",
    "Cutibacterium", "Propionibacterium", "Peptostreptococcus", "Rothia",
    "Lactobacillus", "Gemella", "Abiotrophia", "Granulicatella", "Nocardia",
    "Actinomyces", "Erysipelothrix", "Micrococcus", "Aerococcus",
    # Gram-negative
    "Escherichia", "Klebsiella", "Enterobacter", "Serratia", "Proteus",
    "Providencia", "Morganella", "Citrobacter", "Salmonella", "Shigella",
    "Yersinia", "Vibrio", "Aeromonas", "Plesiomonas", "Pseudomonas",
    "Acinetobacter", "Stenotrophomonas", "Burkholderia", "Achromobacter",
    "Haemophilus", "Neisseria", "Moraxella", "Kingella", "Eikenella",
    "Cardiobacterium", "Aggregatibacter", "Pasteurella", "Bartonella",
    "Brucella", "Francisella", "Legionella", "Bordetella", "Campylobacter",
    "Helicobacter", "Bacteroides", "Prevotella", "Porphyromonas",
    "Fusobacterium", "Capnocytophaga", "Elizabethkingia", "Chryseobacterium",
    "Ralstonia", "Cupriavidus", "Sphingomonas", "Pantoea", "Raoultella",
    "Hafnia", "Edwardsiella", "Cronobacter", "Leclercia",
    # Mycobacteria and relatives
    "Mycobacterium", "Mycobacteroides", "Mycolicibacterium", "Nocardiopsis",
    # Spirochetes, atypicals, intracellular
    "Treponema", "Borrelia", "Borreliella", "Leptospira", "Mycoplasma",
    "Mycoplasmoides", "Ureaplasma", "Chlamydia", "Chlamydophila",
    "Rickettsia", "Orientia", "Ehrlichia", "Anaplasma", "Coxiella",
    "Tropheryma",
    # Fungi
    "Candida", "Candidozyma", "Nakaseomyces", "Pichia", "Clavispora",
    "Cryptococcus", "Aspergillus", "Fusarium", "Scedosporium",
    "Lomentospora", "Rhizopus", "Mucor", "Lichtheimia", "Cunninghamella",
    "Rhizomucor", "Histoplasma", "Blastomyces", "Coccidioides",
    "Paracoccidioides", "Talaromyces", "Sporothrix", "Pneumocystis",
    "Trichosporon", "Malassezia", "Exophiala", "Alternaria", "Curvularia",
    "Trichophyton", "Microsporum", "Epidermophyton", "Saccharomyces",
    "Geotrichum", "Rhodotorula", "Emergomyces",
    # Parasites
    "Plasmodium", "Toxoplasma", "Leishmania", "Trypanosoma", "Giardia",
    "Entamoeba", "Cryptosporidium", "Cyclospora", "Cystoisospora",
    "Babesia", "Naegleria", "Acanthamoeba", "Balamuthia", "Strongyloides",
    "Schistosoma", "Echinococcus", "Taenia", "Ascaris", "Trichuris",
    "Necator", "Ancylostoma", "Enterobius", "Wuchereria", "Onchocerca",
    "Loa", "Brugia", "Trichinella", "Fasciola", "Clonorchis", "Opisthorchis",
    "Paragonimus", "Dirofilaria", "Angiostrongylus", "Anisakis",
}

# Substance entries that are not useful dictation vocabulary.
_DRUG_REJECT = re.compile(
    r"""(
        ^\s*$                 # empty
      | \d                    # any digit: salts, isotopes, codes
      | ^\W                   # leading punctuation
      | \b(POLLEN|EXTRACT|VENOM|DANDER|EPITHELIUM|HAIR|WHOLE)\b
    )""",
    re.VERBOSE | re.IGNORECASE,
)

_BINOMIAL = re.compile(r"^([A-Z][a-z]+) ([a-z][a-z\-]+)$")


def build_medications(ndc_product: Path) -> list[str]:
    """Extract generic drug names from the FDA NDC product file."""
    names: set[str] = set()
    # The FDA file is not UTF-8; it is Windows-1252 in practice.
    with ndc_product.open(encoding="cp1252", errors="replace") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        try:
            i_sub = header.index("SUBSTANCENAME")
            i_gen = header.index("NONPROPRIETARYNAME")
        except ValueError:
            sys.exit("error: unexpected NDC header; columns not found")

        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) <= max(i_sub, i_gen):
                continue
            raw = []
            # SUBSTANCENAME lists active ingredients, semicolon separated.
            raw.extend(cols[i_sub].split(";"))
            # NONPROPRIETARYNAME is the generic product name; it often carries
            # combinations joined by " and ".
            raw.extend(re.split(r"\s+and\s+|,", cols[i_gen]))

            for item in raw:
                name = " ".join(item.split()).strip(" .,-")
                if not name or len(name) < 4 or len(name) > 60:
                    continue
                if _DRUG_REJECT.search(name):
                    continue
                # Store lowercase: these are generic names, not proper nouns.
                names.add(name.lower())
    return sorted(names)


def build_organisms(taxonomy_dir: Path) -> list[str]:
    """Extract species binomials for clinically significant genera."""
    nodes = taxonomy_dir / "nodes.dmp"
    names_dmp = taxonomy_dir / "names.dmp"
    for p in (nodes, names_dmp):
        if not p.exists():
            sys.exit(f"error: missing {p}")

    # taxid -> rank, so subspecies and strain entries can be excluded.
    species_ids: set[int] = set()
    with nodes.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split("\t|\t")
            if len(parts) < 3:
                continue
            if parts[2].strip() == "species":
                try:
                    species_ids.add(int(parts[0]))
                except ValueError:
                    continue

    found: set[str] = set()
    with names_dmp.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split("\t|\t")
            if len(parts) < 4:
                continue
            name_class = parts[3].replace("\t|", "").strip()
            if name_class != "scientific name":
                continue
            try:
                taxid = int(parts[0])
            except ValueError:
                continue
            if taxid not in species_ids:
                continue
            name = parts[1].strip()
            m = _BINOMIAL.match(name)
            if not m:
                continue
            if m.group(1) not in CLINICAL_GENERA:
                continue
            found.add(name)
    return sorted(found)


def write_dictionary(path: Path, header: str, terms: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(header.rstrip() + "\n")
        for t in terms:
            fh.write(t + "\n")
    print(f"{path}: {len(terms)} terms")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ndc", type=Path, help="FDA NDC product.txt")
    ap.add_argument("--taxonomy", type=Path, help="dir with names.dmp/nodes.dmp")
    ap.add_argument("--out", type=Path, default=Path("dictionaries"))
    args = ap.parse_args()

    if not args.ndc and not args.taxonomy:
        ap.error("give --ndc and/or --taxonomy")

    if args.ndc:
        meds = build_medications(args.ndc)
        write_dictionary(
            args.out / "medications.txt",
            "# Generic drug substance names.\n"
            "# Source: FDA National Drug Code Directory (public domain).\n"
            "# https://www.accessdata.fda.gov/cder/ndctext.zip\n"
            "# Generated by scripts/build_dictionaries.py -- do not edit by hand.\n"
            "# Contains no patient information.",
            meds,
        )

    if args.taxonomy:
        orgs = build_organisms(args.taxonomy)
        write_dictionary(
            args.out / "microorganisms.txt",
            "# Species binomials for clinically significant genera.\n"
            "# Source: NCBI Taxonomy (public domain).\n"
            "# https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip\n"
            "# Restricted to a curated genus whitelist -- see\n"
            "# CLINICAL_GENERA in scripts/build_dictionaries.py.\n"
            "# Generated by scripts/build_dictionaries.py -- do not edit by hand.\n"
            "# Contains no patient information.",
            orgs,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
