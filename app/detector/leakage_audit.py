"""
Categorical-leakage audit for the synthetic generator.

This module exists because of a specific, measured failure. The previous
generator gave four of six attack families an MCC that never occurred in
legitimate traffic, and gave every family its own dedicated merchant node.
Both facts were invisible in the headline metrics and both were doing the
model's work for it:

  * held-out REVOCATION_FLOOD scored PR-AUC 1.000 with mean predicted
    probability exactly 1.0 - a leak readout reported as generalisation;
  * graph_merchant_pagerank became the model's #2 SHAP feature by encoding
    "which merchant node is this" rather than any fraud-ring structure.

The lesson is that anti-leakage work has to be MEASURED, not asserted. The
generator already reasoned carefully about stored-value overlap and amount
overlap in prose - and shipped a perfect categorical fingerprint anyway,
because nothing checked.

`audit_categorical_leakage` is that check. For every categorical field it
computes, per value, how purely that value predicts the label, and reports
the worst case. A value that appears in >= `min_support` rows and is >=
`purity_threshold` pure is a shortcut the model will find before it learns
anything behavioural.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

# Fields whose values a model could memorise as a label lookup.
DEFAULT_CATEGORICAL_FIELDS = ["merchant_mcc", "merchant_id", "device_id", "rail"]

# A value must appear at least this many times before its purity is
# meaningful - otherwise a single-row value is trivially "100% pure".
DEFAULT_MIN_SUPPORT = 15

# Above this purity, a single categorical value is effectively the label.
DEFAULT_PURITY_THRESHOLD = 0.95


def audit_categorical_leakage(
    df: pd.DataFrame,
    label_col: str = "is_fraud",
    fields: Optional[List[str]] = None,
    min_support: int = DEFAULT_MIN_SUPPORT,
    purity_threshold: float = DEFAULT_PURITY_THRESHOLD,
) -> Dict[str, Any]:
    """
    Returns a per-field leakage report plus an overall pass/fail.

    The metric is BASE-RATE AWARE, and that matters. A naive
    max(P(fraud|v), P(legit|v)) purity flags every value in an imbalanced
    dataset: at a 7% base fraud rate, a perfectly ordinary merchant sits at
    ~98% legit and looks "98% pure" while carrying almost no information.

    What actually constitutes leakage is a value that moves you close to
    CERTAINTY beyond what the base rate already gives you:

      * positive leak - P(fraud|v) >= purity_threshold. The value means fraud.
        This is the shape the old generator had: merchant_mcc=5734 occurred
        in attack rows and nowhere else.
      * negative leak - the value near-perfectly EXCLUDES fraud while the
        base rate does not, i.e. fraud_rate <= base_rate/20 with real support.

    Values that are merely cleaner or dirtier than average are signal, not
    leakage: some merchants genuinely are riskier, and a model is supposed to
    learn that.
    """
    fields = [f for f in (fields or DEFAULT_CATEGORICAL_FIELDS) if f in df.columns]
    base_rate = float(df[label_col].mean())
    report: Dict[str, Any] = {}
    worst_overall = 0.0
    leaking_fields: List[str] = []

    for field in fields:
        grouped = df.groupby(field)[label_col].agg(["count", "mean"])
        grouped = grouped[grouped["count"] >= min_support]
        if grouped.empty:
            report[field] = {"values_checked": 0, "max_fraud_precision": None, "leaking_values": []}
            continue

        leaking: List[Dict[str, Any]] = []
        for idx, r in grouped.iterrows():
            fraud_rate = float(r["mean"])
            support = int(r["count"])
            positive_leak = fraud_rate >= purity_threshold
            negative_leak = base_rate > 0 and fraud_rate <= base_rate / 20.0
            if positive_leak or negative_leak:
                leaking.append({
                    "value": str(idx),
                    "support": support,
                    "fraud_rate": round(fraud_rate, 4),
                    "lift_over_base": round(fraud_rate / base_rate, 3) if base_rate else None,
                    "direction": "MEANS_FRAUD" if positive_leak else "EXCLUDES_FRAUD",
                })

        max_fraud_precision = float(grouped["mean"].max())
        worst_overall = max(worst_overall, max_fraud_precision)
        if leaking:
            leaking_fields.append(field)

        report[field] = {
            "values_checked": int(len(grouped)),
            "max_fraud_precision": round(max_fraud_precision, 4),
            "max_lift_over_base": round(max_fraud_precision / base_rate, 3) if base_rate else None,
            "leaking_values": sorted(leaking, key=lambda r: -r["fraud_rate"]),
        }

    return {
        "passed": not leaking_fields,
        "base_fraud_rate": round(base_rate, 4),
        "purity_threshold": purity_threshold,
        "min_support": min_support,
        "worst_fraud_precision_observed": round(worst_overall, 4),
        "leaking_fields": leaking_fields,
        "per_field": report,
        "interpretation": (
            "A field leaks when one of its VALUES determines the label. Positive leak: "
            "P(fraud|value) >= threshold, i.e. the value means fraud. Negative leak: the value "
            "excludes fraud far beyond the base rate. Values that are merely riskier or safer "
            "than average are legitimate signal and are not flagged - a fraud model is supposed "
            "to learn those."
        ),
    }


def family_separability(
    df: pd.DataFrame,
    family_col: str = "attack_family",
    fields: Optional[List[str]] = None,
    min_support: int = DEFAULT_MIN_SUPPORT,
) -> Dict[str, Any]:
    """
    For each attack family, the strongest single categorical value that
    identifies it. This is the check that would have caught the original
    MCC leak: REVOCATION_FLOOD was 100% identifiable by merchant_mcc=5734.

    A family that is highly separable by ONE categorical value cannot be used
    to make a claim about generalisation, because holding it out removes
    nothing the model needs - the shortcut transfers from its siblings.
    """
    fields = [f for f in (fields or DEFAULT_CATEGORICAL_FIELDS) if f in df.columns]
    families = [f for f in df[family_col].dropna().unique() if f != "NONE"]
    out: Dict[str, Any] = {}

    for family in families:
        in_family = df[family_col] == family
        best: Dict[str, Any] = {"field": None, "value": None, "precision": 0.0, "recall": 0.0}
        for field in fields:
            for value, sub in df.groupby(field):
                if len(sub) < min_support:
                    continue
                tp = int((sub[family_col] == family).sum())
                if tp == 0:
                    continue
                precision = tp / len(sub)
                recall = tp / max(1, int(in_family.sum()))
                # A shortcut needs BOTH: the value must mostly mean this
                # family, and must cover most of it.
                if min(precision, recall) > min(best["precision"], best["recall"]):
                    best = {
                        "field": field,
                        "value": str(value),
                        "precision": round(precision, 4),
                        "recall": round(recall, 4),
                    }
        out[str(family)] = best

    return out
