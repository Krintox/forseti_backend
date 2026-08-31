"""
Claim reconciliation gate.

FORSETI's stated differentiator is claim discipline: "every number was produced
by a pipeline in this repository and read back from artifacts/." An adversarial
review found that was not true of the prose - the README headline table cited
`baselines.json` by name while contradicting it, and one quantity (the DTL
feature lift) appeared in the repository with FOUR different values.

Reproducibility guarantees that re-running produces the same artifact. It does
not guarantee that the prose describes the artifact. Nothing checked that, so
nothing caught it.

This script is that check. It:

  1. reads the DEPLOYED artifacts (backend/artifacts/) and derives the
     authoritative numbers - the copy a judge's browser actually reaches;
  2. writes docs/MEASURED_NUMBERS.md, the single place any doc should quote;
  3. scans every tracked .md for numbers that are KNOWN-STALE - values that
     were correct for a previous run and are now contradicted - and fails.

    python scripts/check_claims.py            # report
    python scripts/check_claims.py --check    # exit 1 if anything is stale

Run it before anyone reads a number on a stage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS = os.path.join(REPO_ROOT, "docs")

# Read the artifacts that actually SHIP.
#
# This gate used to read the monorepo-root `artifacts/`, which is not the tree a
# deployed instance serves. The two silently diverged by six days: the prose was
# reconciled against numbers that existed only on a developer's disk, while the
# public API reported a retracted headline and a pre-leak-fix model. A gate that
# checks a copy nobody serves proves nothing about what a judge sees.
ARTIFACTS = os.path.join(REPO_ROOT, "backend", "artifacts")
if not os.path.isdir(os.path.join(ARTIFACTS, "evaluation")):
    ARTIFACTS = os.path.join(REPO_ROOT, "artifacts")

# Docs that are explicitly historical records of a past state. They are allowed
# to contain superseded numbers, because that is their entire purpose - but they
# must SAY so at the top, which `scan_historical_banners()` enforces. Without
# that, "historical" is just an exemption list, and a reader who lands on one of
# these pages has no way to know the numbers are retired. That is exactly how a
# +0.1378 lift figure survived a full remediation pass.
# Put this on (or immediately above) a line that deliberately quotes a
# superseded number - e.g. a post-mortem explaining what the old figure was.
CLAIMS_OK_MARKER = "<!--claims-ok-->"

#: Goes at the top of a HISTORICAL_DOCS file, right under its H1.
HISTORICAL_BANNER_MARKER = "<!--historical-record-->"

HISTORICAL_DOCS = {
    "AUTHORITY_MODEL_AND_ARCHITECTURE_REVIEW.md",
    "ROADMAP_AGENTIC_SECURITY_EXPANSION.md",
    "SESSION_HANDOVER.md",
    "roast.txt",
    "LEARN_22_THE_LEAK.md",       # the post-mortem: quoting the old numbers is the point
    "Forseti_3.0_progress.md",    # remediation log: records what each number WAS
}


def _load(rel: str) -> Dict[str, Any]:
    path = os.path.join(ARTIFACTS, rel)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def authoritative_numbers() -> Dict[str, Any]:
    """The current truth, read from artifacts. Nothing here is typed by hand."""
    metrics = _load("evaluation/metrics.json")
    baselines = _load("evaluation/baselines.json")
    ablation = _load("evaluation/ablation_results.json")
    latency = _load("benchmark/latency.json")
    fidelity = _load("fidelity/fidelity_report.json")

    tm = metrics.get("test_metrics", {})
    head = baselines.get("headline_finding", {})
    dtl_lift = ablation.get("measured_dtl_feature_lift", {})
    graph_lift = ablation.get("measured_graph_feature_lift", {})

    return {
        "detection": {
            "experiment_id": metrics.get("experiment_id"),
            "pr_auc": tm.get("pr_auc"),
            "roc_auc": tm.get("roc_auc"),
            "f1": tm.get("f1_score"),
            "precision": tm.get("precision"),
            "recall": tm.get("recall"),
            "recall_at_0_5pct_fpr": tm.get("recall_at_0.5pct_fpr"),
            "feature_count": metrics.get("dataset", {}).get("feature_count"),
            "fraud_prevalence": metrics.get("dataset", {}).get("fraud_prevalence"),
            "ece_before": metrics.get("calibration", {}).get("ece_before_calibration"),
            "ece_after": metrics.get("calibration", {}).get("ece_after_calibration"),
            "leakage_audit_passed": metrics.get("leakage_audit", {}).get("passed"),
        },
        "cross_rail_recall": {
            "held_out": head.get("cross_rail_split_recall_when_family_held_out", {}),
            "seen": head.get("cross_rail_split_recall_when_family_seen", {}),
            # Published because a point estimate on a 64-transaction slice invited a
            # comparison the data could not support, and did, for a whole revision.
            "ci95": head.get("cross_rail_split_recall_ci95", {}),
        },
        "false_positive_rate": {
            arch: (baselines.get("condition_attack_family_holdout", {})
                   .get("baselines", {}).get(arch, {}).get("false_positive_rate"))
            for arch in ("rules_only", "per_rail_ml", "ml_without_dtl",
                         "hybrid_dtl_ml", "dtl_invariant_only")
        },
        "lift": {
            "dtl_pr_auc": dtl_lift.get("lift"),
            "dtl_relative_pct": dtl_lift.get("relative_lift_pct"),
            "graph_pr_auc": graph_lift.get("lift"),
            "graph_relative_pct": graph_lift.get("relative_lift_pct"),
            # A THIRD lift figure exists and is legitimate: the baseline harness
            # measures hybrid-vs-no-DTL across separately trained models, which is
            # not the same experiment as the ablation's feature-group removal.
            # Publishing only one while two are computed is how "one quantity with
            # four different values" happened the first time.
            "baseline_harness_pr_auc": (baselines.get("measured_dtl_lift", {}) or {}).get("pr_auc_lift"),
            "baseline_harness_definition": (baselines.get("measured_dtl_lift", {}) or {}).get("definition"),
            "baseline_harness_vs_silo": (baselines.get("measured_dtl_lift", {}) or {}).get("pr_auc_lift_vs_per_rail_silo"),
        },
        "latency": {
            "p99_ms": (latency.get("breakdown", {}).get("full_end_to_end_pipeline", {}) or {}).get("p99_ms"),
            "verdict": latency.get("metadata", {}).get("sla_verdict"),
        },
        "fidelity": {
            "status": fidelity.get("metadata", {}).get("overall_status"),
        },
    }


# Values that were true for an earlier run and are now contradicted. Each entry
# is (stale_literal, what_it_used_to_mean, where_the_truth_lives_now).
STALE_VALUES: List[Tuple[str, str, str]] = [
    ("0.8882", "old test PR-AUC", "metrics.json -> test_metrics.pr_auc"),
    ("0.9541", "post-graph pre-leak-fix PR-AUC", "metrics.json -> test_metrics.pr_auc"),
    ("0.9825", "old test ROC-AUC", "metrics.json -> test_metrics.roc_auc"),
    ("0.9956", "post-graph pre-leak-fix ROC-AUC", "metrics.json -> test_metrics.roc_auc"),
    ("0.877", "old DTL invariant cross-rail recall", "baselines.json -> headline_finding"),
    ("0.9054", "pre-leak-fix cross-rail recall", "baselines.json -> headline_finding"),
    # Same number wearing a percent sign. It appeared that way in three pitch
    # scripts, which is the worst possible place for a retracted figure.
    ("90.54%", "pre-leak-fix cross-rail recall, as a percentage",
     "baselines.json -> headline_finding (0.8438)"),
    ("87.70%", "old DTL invariant cross-rail recall, as a percentage",
     "baselines.json -> headline_finding (0.8438)"),
    ("0.2568", "pre-leak-fix rules-only cross-rail recall", "baselines.json -> headline_finding"),
    ("0.8926", "pre-leak-fix hybrid PR-AUC", "baselines.json / ablation_results.json"),
    ("0.1378", "DTL lift, revision 1 of 4", "ablation_results.json -> measured_dtl_feature_lift"),
    ("0.0723", "DTL lift, revision 2 of 4", "ablation_results.json -> measured_dtl_feature_lift"),
    ("0.0682", "DTL lift, revision 3 of 4", "ablation_results.json -> measured_dtl_feature_lift"),
    ("0.0325", "DTL lift, revision 4 of 4", "ablation_results.json -> measured_dtl_feature_lift"),
    ("0.0246", "pre-leak-fix graph lift", "ablation_results.json -> measured_graph_feature_lift"),
    ("18.26%", "old DTL relative lift", "ablation_results.json -> relative_lift_pct"),
    ("8.15%", "pre-leak-fix DTL relative lift", "ablation_results.json -> relative_lift_pct"),
    ("2.55%", "pre-leak-fix graph relative lift", "ablation_results.json -> relative_lift_pct"),
    ("1.238 ms", "old p99 latency", "benchmark/latency.json"),
    ("9.045%", "pre-leak-fix invariant false-positive rate",
     "baselines.json -> dtl_invariant_only.false_positive_rate (0.15761)"),
    ("0.09045", "pre-leak-fix invariant false-positive rate",
     "baselines.json -> dtl_invariant_only.false_positive_rate (0.15761)"),
]


# The leak produced a whole CLASS of claim, not just numbers: "every learned
# model scores zero on cross-rail splitting". Zeros are too common a literal to
# put in STALE_VALUES, so this matches the assertion instead.
_NOT_SENTENCE_END = r"[^.\n]"
_ZERO_RECALL_CLAIM = re.compile(
    r"(?:models?|ml|learned|classifier)" + _NOT_SENTENCE_END + r"{0,80}?"
    r"(?:score[sd]?|achiev\w*|reach\w*|measur\w*|of)" + _NOT_SENTENCE_END + r"{0,30}?"
    r"0(?:\.0+)?" + _NOT_SENTENCE_END + r"{0,30}?recall",
    re.I,
)
_ZERO_RECALL_ALT = re.compile(
    r"0(?:\.0+)?\s+recall" + _NOT_SENTENCE_END + r"{0,60}?(?:models?|ml|learned|classifier)",
    re.I,
)


def scan_retracted_claims() -> List[Dict[str, Any]]:
    """
    Flags the retracted "learned models score 0.0 recall" claim wherever it
    reappears unmarked.

    It was true of a broken experiment, it was the headline for a while, and it
    survived the first correction pass in four documents because the gate only
    compared numbers. A post-mortem may still quote it - that is what the
    `<!--claims-ok-->` marker is for.
    """
    findings: List[Dict[str, Any]] = []
    for path in _tracked_docs():
        if os.path.basename(path) in HISTORICAL_DOCS:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            prev = lines[lineno - 2] if lineno >= 2 else ""
            prev2 = lines[lineno - 3] if lineno >= 3 else ""
            if any(CLAIMS_OK_MARKER in t for t in (line, prev, prev2)):
                continue
            if _ZERO_RECALL_CLAIM.search(line) or _ZERO_RECALL_ALT.search(line):
                findings.append({
                    "file": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"),
                    "line": lineno,
                    "stale_value": "0.0 recall for learned models",
                    "was": "the MCC-leak headline, retracted",
                    "truth_now": "baselines.json -> 0.1719 without DTL features, 0.8281 with",
                    "text": line.strip()[:120],
                })
    return findings



# ---------------------------------------------------------------------------
# Test counts are claims too.
#
# The suite grew 217 -> 341 and four documents went on saying 217, including the
# chapter whose entire subject is the test suite. Nothing caught it, because the
# gate only knew about metrics. A number quoted in prose is a claim regardless of
# whether it came out of a model.
#
# Collected counts (not static `def test_` counts) are the honest figure, since
# parametrised tests expand: test_suspension_is_enforced.py has 8 definitions
# and 32 cases. So the count comes from pytest itself, cached to an artifact so
# the everyday `--check` run stays fast.
# ---------------------------------------------------------------------------

TEST_INVENTORY_PATH = os.path.join(ARTIFACTS, "tests", "test_inventory.json")


def collect_test_inventory() -> Dict[str, Any]:
    """Runs pytest --collect-only and writes the per-file counts to artifacts/."""
    import subprocess
    from collections import Counter
    from datetime import datetime, timezone

    backend = os.path.join(REPO_ROOT, "backend")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:warnings"],
        cwd=backend, capture_output=True, text=True,
    )
    counts = Counter()
    for line in proc.stdout.splitlines():
        if "::" in line and line.strip().startswith("tests/"):
            counts[line.split("::", 1)[0].strip()] += 1
    if not counts:
        raise SystemExit(
            "pytest collected nothing. Output was:\n" + (proc.stdout or proc.stderr)[-2000:]
        )
    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": sum(counts.values()),
        "files": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    os.makedirs(os.path.dirname(TEST_INVENTORY_PATH), exist_ok=True)
    with open(TEST_INVENTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)
    return inventory


def load_test_inventory() -> Optional[Dict[str, Any]]:
    if not os.path.exists(TEST_INVENTORY_PATH):
        return None
    try:
        with open(TEST_INVENTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# Prose forms that assert a backend test count. Deliberately narrow: a bare
# number near the word "test" is far too common to flag.
_TEST_COUNT_PATTERNS = [
    re.compile(r"\b(\d{2,4})[ -](?:automated |backend |passing |pytest )*tests?\b", re.I),
    re.compile(r"\b(\d{2,4})-test\b", re.I),
    re.compile(r"\b(\d{2,4}) (?:tests? )?passed\b", re.I),
    re.compile(r"\btests?/?\s*(?:->|:)\s*(\d{2,4}) passed\b", re.I),
]



def scan_historical_banners() -> List[Dict[str, Any]]:
    """
    Every exempted doc must announce that it is exempt.

    HISTORICAL_DOCS suppresses stale-value findings for a whole file. That is
    right - a post-mortem quoting the old number is the point - but it means a
    reader arriving at one of those pages sees superseded figures with nothing
    marking them. The exemption has to come with an obligation.
    """
    findings: List[Dict[str, Any]] = []
    for name in sorted(HISTORICAL_DOCS):
        if not name.endswith(".md"):
            continue  # roast.txt and friends are inputs, not published prose
        path = os.path.join(DOCS, name)
        if not os.path.exists(path):
            path = os.path.join(REPO_ROOT, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                head = f.read(4000)
        except (OSError, UnicodeDecodeError):
            continue
        if HISTORICAL_BANNER_MARKER in head:
            continue
        findings.append({
            "file": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"),
            "line": 1,
            "stale_value": "no historical-record banner",
            "was": "exempt from the stale-value scan",
            "truth_now": f"add {HISTORICAL_BANNER_MARKER} under the H1, pointing at "
                         "docs/MEASURED_NUMBERS.md",
            "text": "this file is exempted from claim checking but does not say so",
        })
    return findings


def scan_test_counts(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Flags any doc asserting a backend test count that is not currently true.

    A per-FILE count is a legitimate thing for a doc to state ("test_forseti.py
    | 51"), so those are accepted as well as the total. That does weaken the
    gate slightly - a stale number that happens to coincide with some file's
    current count slips through - but flagging every table row in the test
    chapter would make the gate noise, and noise gets switched off.
    """
    true_total = inventory["total"]
    allowed = {true_total} | set(inventory["files"].values())
    findings: List[Dict[str, Any]] = []
    for path in _tracked_docs():
        if os.path.basename(path) in HISTORICAL_DOCS:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            prev = lines[lineno - 2] if lineno >= 2 else ""
            if CLAIMS_OK_MARKER in line or CLAIMS_OK_MARKER in prev:
                continue
            for pattern in _TEST_COUNT_PATTERNS:
                for match in pattern.finditer(line):
                    claimed = int(match.group(1))
                    if claimed in allowed:
                        continue
                    findings.append({
                        "file": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"),
                        "line": lineno,
                        "stale_value": str(claimed),
                        "was": "a superseded backend test count",
                        "truth_now": f"artifacts/tests/test_inventory.json -> total ({true_total})",
                        "text": line.strip()[:120],
                    })
    return findings


def _tracked_docs() -> List[str]:
    """Every .md/.txt at the repo root and in docs/ - the prose a judge reads."""
    targets: List[str] = []
    for base in (REPO_ROOT, DOCS):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.endswith((".md", ".txt")) and os.path.isfile(os.path.join(base, name)):
                targets.append(os.path.join(base, name))
    return targets


def scan_docs() -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for path in _tracked_docs():
        name = os.path.basename(path)
        if name in HISTORICAL_DOCS:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            # Deliberate historical citation - "an earlier revision reported X,
            # here is why it was wrong". Quoting a superseded number IS the
            # point in a post-mortem, so allow it when explicitly marked.
            prev = lines[lineno - 2] if lineno >= 2 else ""
            if CLAIMS_OK_MARKER in line or CLAIMS_OK_MARKER in prev:
                continue
            for literal, meaning, truth in STALE_VALUES:
                # Boundary-matched: "0.877" must not match inside the CURRENT
                # value "0.8772". Substring matching produced exactly that
                # false positive against the generated numbers file.
                # `\%` in LaTeX-ish prose ("$+18.26\%$") hid a stale value from
                # this gate for a whole revision, so normalise it away first.
                haystack = line.replace("\\%", "%")
                if re.search(rf"(?<![\d.]){re.escape(literal)}(?![\d])", haystack):
                    findings.append({
                        "file": os.path.relpath(path, REPO_ROOT).replace(os.sep, "/"),
                        "line": lineno,
                        "stale_value": literal,
                        "was": meaning,
                        "truth_now": truth,
                        "text": line.strip()[:120],
                    })
    return findings


MEASURED_NUMBERS_HEADER = """# MEASURED NUMBERS — single source of truth

<!-- GENERATED by scripts/check_claims.py. Do not hand-edit. -->

Every number below is read directly out of `artifacts/` by
`scripts/check_claims.py`. If a document anywhere in this repository quotes a
measured figure, it must match this file — and `python scripts/check_claims.py
--check` fails the build when it does not.

This file exists because prose drifted from artifacts once already: the README
headline cited `baselines.json` by name while contradicting it, and the DTL
feature lift appeared with four different values across the repo. Reproducibility
made the artifacts stable; nothing made the writing follow them.

"""


def write_measured_numbers(nums: Dict[str, Any]) -> str:
    d, lift, xr = nums["detection"], nums["lift"], nums["cross_rail_recall"]
    out = [MEASURED_NUMBERS_HEADER]
    out.append(f"**Experiment:** `{d['experiment_id']}` · seed 42\n")

    out.append("\n## Detection (`artifacts/evaluation/metrics.json`)\n")
    out.append("| Metric | Value |\n|---|---|\n")
    for label, key in [
        ("PR-AUC (temporal test)", "pr_auc"), ("ROC-AUC", "roc_auc"), ("F1", "f1"),
        ("Precision", "precision"), ("Recall", "recall"),
        ("Recall @ 0.5% FPR", "recall_at_0_5pct_fpr"),
        ("Feature count", "feature_count"), ("Fraud prevalence", "fraud_prevalence"),
        ("Calibration ECE before", "ece_before"), ("Calibration ECE after", "ece_after"),
        ("Categorical leakage audit passed", "leakage_audit_passed"),
    ]:
        out.append(f"| {label} | {d.get(key)} |\n")

    out.append("\n## Cross-rail recall (`artifacts/evaluation/baselines.json`)\n")
    held_ci = (xr.get("ci95") or {}).get("held_out", {})
    fpr = nums.get("false_positive_rate", {})
    out.append("| Architecture | Family held out | 95% CI | Family seen | FPR |\n|---|---|---|---|---|\n")
    for arch in [k for k in xr["held_out"] if k != "note"]:
        band = held_ci.get(arch)
        ci_s = f"[{band['ci95'][0]}, {band['ci95'][1]}]" if band else "—"
        out.append(f"| {arch} | {xr['held_out'].get(arch)} | {ci_s} | "
                   f"{xr['seen'].get(arch)} | {fpr.get(arch)} |\n")
    n = next((b["n"] for b in held_ci.values()), None)
    if n:
        out.append(
            f"\nWilson score intervals, 95%, n={n} held-out cross-rail transactions. "
            "The with-feature vs without-feature separation is wider than these intervals. "
            "The classifier's held-out vs seen difference is NOT — no generalisation claim "
            "is made from it. The invariant's two columns are equal by construction.\n"
        )

    out.append("\n## Feature-group lift (`artifacts/evaluation/ablation_results.json`)\n")
    out.append("| Quantity | Value |\n|---|---|\n")
    out.append(f"| DTL feature lift (PR-AUC) | +{lift['dtl_pr_auc']} ({lift['dtl_relative_pct']}% relative) |\n")
    out.append(f"| Graph feature lift (PR-AUC) | +{lift['graph_pr_auc']} ({lift['graph_relative_pct']}% relative) |\n")
    out.append(f"| Baseline-harness DTL lift (PR-AUC) | +{lift['baseline_harness_pr_auc']} |\n")
    out.append(f"| Baseline-harness DTL lift vs per-rail silo | +{lift['baseline_harness_vs_silo']} |\n")
    out.append(
        "\n**These are the ONLY DTL lift figures, and they are NOT interchangeable.** "
        "Any other value in any document is stale.\n\n"
        f"- **Feature-group lift, +{lift['dtl_pr_auc']}** — removes the DTL feature GROUPS from "
        "one model and retrains it. `ablation_results.json`, variant A minus variant B.\n"
        f"- **Baseline-harness lift, +{lift['baseline_harness_pr_auc']}** — compares two "
        "SEPARATELY TRAINED architectures on the all-families-seen condition. "
        "`baselines.json` → `measured_dtl_lift`.\n"
        f"- **Lift vs per-rail silo, +{lift['baseline_harness_vs_silo']}** — the hybrid against "
        "siloed per-rail models.\n\n"
        "They differ because they are different experiments, not because one is wrong. Quote the "
        "one whose definition matches the sentence you are writing, and name which it is — "
        "publishing a single unqualified \"DTL lift\" is how this quantity ended up in the "
        "repository with four different values.\n"
    )

    out.append("\n## Latency (`artifacts/benchmark/latency.json`)\n")
    out.append(f"- Full inline pipeline p99: **{nums['latency']['p99_ms']} ms**\n")
    out.append(f"- Verdict: `{nums['latency']['verdict']}`\n")

    out.append("\n## Public anchor fidelity (`artifacts/fidelity/fidelity_report.json`)\n")
    out.append(f"- Status: **{nums['fidelity']['status']}**\n")

    inventory = load_test_inventory()
    out.append("\n## Test inventory (`artifacts/tests/test_inventory.json`)\n")
    if inventory:
        out.append(f"- Backend tests collected: **{inventory['total']}** "
                   f"across {len(inventory['files'])} files\n")
        out.append("- Refresh with `python scripts/check_claims.py --collect-tests`\n")
    else:
        out.append("- **NOT COLLECTED** - run `python scripts/check_claims.py --collect-tests`\n")

    text = "".join(out)
    with open(os.path.join(DOCS, "MEASURED_NUMBERS.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile documented claims against artifacts")
    parser.add_argument("--check", action="store_true", help="exit 1 if any stale claim is found")
    parser.add_argument(
        "--collect-tests", action="store_true",
        help="re-run pytest --collect-only and refresh artifacts/tests/test_inventory.json",
    )
    args = parser.parse_args()

    if args.collect_tests:
        inv = collect_test_inventory()
        print(f"Collected {inv['total']} tests across {len(inv['files'])} files "
              f"-> artifacts/tests/test_inventory.json")

    nums = authoritative_numbers()
    if not nums["detection"]["pr_auc"]:
        print("No artifacts found. Run `python tasks.py all` first.")
        return 1

    write_measured_numbers(nums)
    print("Wrote docs/MEASURED_NUMBERS.md from current artifacts.")
    print(f"  PR-AUC={nums['detection']['pr_auc']}  "
          f"DTL lift=+{nums['lift']['dtl_pr_auc']}  "
          f"leakage_audit_passed={nums['detection']['leakage_audit_passed']}")

    findings = scan_docs()

    inventory = load_test_inventory()
    if inventory:
        print(f"  backend tests={inventory['total']} "
              f"(across {len(inventory['files'])} files)")
        findings += scan_test_counts(inventory)
    else:
        print("  backend test count NOT COLLECTED - run with --collect-tests to gate it")

    findings += scan_retracted_claims()
    findings += scan_historical_banners()

    # Prose matching the artifacts is only half the guarantee. The public API
    # serves backend/artifacts/, which is a SEPARATE tracked copy - and it sat
    # six days behind, still reporting a retracted headline and still shipping
    # the pre-leak-fix model. A claim gate that cannot see what is deployed is
    # checking the wrong thing.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sync_deployable_artifacts import diff as _deploy_diff

        missing, differing, _extra = _deploy_diff()
        if missing or differing:
            count = len(missing) + len(differing)
            print(f"\n  DEPLOY SKEW: {count} artifact(s) in backend/artifacts/ "
                  "do not match the generated ones.")
            for rel in (missing + differing)[:12]:
                findings.append({
                    "file": f"backend/artifacts/{rel}",
                    "line": 0,
                    "stale_value": "the deployed copy of this artifact",
                    "was": "missing" if rel in missing else "an older run",
                    "truth_now": f"artifacts/{rel} — fix with "
                                 "`python scripts/sync_deployable_artifacts.py`",
                    "text": "the public API serves this copy, not artifacts/",
                })
        else:
            print("  deployable artifacts in sync with artifacts/")
    except Exception as exc:  # never let the deploy check mask a prose finding
        print(f"  (deploy-skew check unavailable: {exc})")

    if not findings:
        print("\nNo stale numeric claims found in tracked prose.")
        return 0

    print(f"\n{len(findings)} STALE CLAIM(S) — prose contradicts artifacts:\n")
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for f in findings:
        by_file.setdefault(f["file"], []).append(f)
    for path, items in sorted(by_file.items()):
        print(f"  {path}")
        for it in items:
            print(f"    :{it['line']}  {it['stale_value']}  ({it['was']}) -> {it['truth_now']}")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
