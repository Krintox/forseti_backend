"""
Anchor dataset helper.

FORSETI compares its synthetic generator against two public real-world
transaction datasets. Neither is redistributed with this repository: both are
published under terms that require you to accept them on the source platform,
so they must be fetched manually with your own account.

    python scripts/download_anchors.py          # show status + instructions
    python scripts/download_anchors.py --check  # exit non-zero if missing

Until a dataset is present the fidelity harness reports
"NOT RUN / DATASET UNAVAILABLE" and makes no realism claim. That is intended
behaviour, not a failure.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ANCHOR_DIR = os.environ.get("DATASET_DIR", os.path.join(REPO_ROOT, "data", "anchors"))

ANCHORS = {
    "paysim.csv": {
        "name": "PaySim mobile-money simulation",
        "citation": "Lopez-Rojas, Elmir & Axelsson (2016)",
        "source": "https://www.kaggle.com/datasets/ealaxi/paysim1",
        "used_for": "Mobile-money / UPI-like amount and transaction-type distributions.",
        "note": "Download paysim1.zip, unzip, rename the CSV to paysim.csv.",
    },
    "creditcard.csv": {
        "name": "ULB credit-card fraud dataset",
        "citation": "Dal Pozzolo et al., Universite Libre de Bruxelles",
        "source": "https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud",
        "used_for": "Card-rail amount distribution and fraud prevalence.",
        "note": "Features V1-V28 are PCA components; only Amount/Class/Time map "
                "onto the canonical schema. Unmappable fields are excluded from "
                "comparison rather than invented.",
    },
}


def status() -> dict:
    return {fn: os.path.exists(os.path.join(ANCHOR_DIR, fn)) for fn in ANCHORS}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check for FORSETI anchor datasets")
    parser.add_argument("--check", action="store_true", help="exit 1 if any anchor is missing")
    args = parser.parse_args()

    os.makedirs(ANCHOR_DIR, exist_ok=True)
    present = status()

    print("FORSETI anchor datasets")
    print(f"  directory: {ANCHOR_DIR}\n")

    for filename, meta in ANCHORS.items():
        mark = "PRESENT" if present[filename] else "MISSING"
        print(f"  [{mark:>7}] {filename}  -  {meta['name']}")
        print(f"            citation : {meta['citation']}")
        print(f"            source   : {meta['source']}")
        print(f"            used for : {meta['used_for']}")
        print(f"            note     : {meta['note']}\n")

    missing = [f for f, ok in present.items() if not ok]
    if missing:
        print("These datasets are NOT redistributed with this repository.")
        print("Download them from the sources above, accept the platform terms, and")
        print(f"place the CSVs in: {ANCHOR_DIR}")
        print("\nThe fidelity harness will report NOT RUN / DATASET UNAVAILABLE until then,")
        print("and will never substitute fabricated numbers.")
    else:
        print("All anchors present. Run: python -m app.fidelity.report")

    if args.check and missing:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
