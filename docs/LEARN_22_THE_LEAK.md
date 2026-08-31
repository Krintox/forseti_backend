# LEARN_22: The Leak: how we got our own headline wrong

<!--historical-record-->
> **This chapter quotes retired numbers on purpose.** It is the post-mortem of a data
> leak and of the over-reading that followed it, so figures like `0.000 recall`,
> `0.9054` and `PR-AUC 0.8882` appear here as the things that were *wrong*, always
> next to what replaced them. Current figures live in
> [`MEASURED_NUMBERS.md`](MEASURED_NUMBERS.md), generated from the artifacts that ship.


> **Prerequisites:** [LEARN_06](LEARN_06_THE_ML_MODEL.md), [LEARN_19](LEARN_19_GRAPH_SENTINEL.md)
> **You will be able to:**
> - Explain what categorical label leakage is, and why it is invisible in headline metrics.
> - Trace the exact mechanism that made FORSETI's flagship claim wrong.
> - Recognise the three warning signs that were present and unread.
> - Explain why the corrected result is a *better* argument than the wrong one.
> **Files this chapter is about:** `backend/app/detector/dataset_builder.py`, `backend/app/detector/leakage_audit.py`, `scripts/check_claims.py`

---

## 1. What we claimed

FORSETI's headline was:

> Every learned model scores **0.000 recall** on held-out cross-rail splitting, while the
> deterministic DTL invariant scores 0.877. *It is not detectable per-transaction, no matter how
> good the model is.*

It was wrong. Not the direction, the mechanism. And the refutation was sitting in our own
`artifacts/evaluation/baselines.json` the entire time, which reported that the same hybrid model
scored **0.9054** on cross-rail recall once the family was included in training. A model cannot be
structurally incapable of detecting something it detects at 90% recall.

---

## 2. The actual mechanism

🧒 **Like you're five**
We were teaching a robot to spot naughty shopping trips. Without meaning to, we sent every naughty
trip to shops that good shoppers *never* visited. So the robot never learned what naughty
behaviour looks like. It just learned the list of naughty shops. When we tested it on a new kind
of naughtiness at one of those shops, it got a perfect score and we thought it was clever.

🏪 **In real life**
Our generator assigned merchant category codes per attack family:

| Traffic | MCC | In the delegation's permitted set? |
|---|---|---|
| Legitimate | 5411 / 5812 / 5311 | ✅ always |
| `CROSS_RAIL_SPLIT` | 5311 | ✅ |
| `INTENT_LAUNDERING` | 5411 | ✅ |
| `REVOCATION_FLOOD` | 5734 | ❌ **never appears in legitimate traffic** |
| `VELOCITY_BURST` | 5499 | ❌ **never** |
| `SCOPE_CREEP` / `BASELINE_POISONING` | 5045 | ❌ **never** |

🎓 **Properly**
Four of six families were perfectly separable by one categorical value. That fact propagated
deterministically into the feature vector: `feature_schema.py` adds `+0.65` to `semantic_drift`
when the MCC is out of scope, and `cart_intent_consistency_score = 1.0 - semantic_drift`. So
`cart_intent_consistency_score` became a near-perfect label proxy, and it was the model's **#1
SHAP feature by a factor of two.**

The model never had to learn `exposure_after_tx_ratio` (the actual cross-rail aggregate) because
a cheaper shortcut existed. `CROSS_RAIL_SPLIT` was the *one* family with no MCC shortcut. Hold it
out, and the model had nothing left, hence 0.000. That number was not a fact about machine
learning. It was the shape of our own generator.

A second, independent leak sat beside it: every family also had its own dedicated `merchant_id`,
so `graph_merchant_pagerank` fingerprinted "which merchant node is this" and became the **#2**
feature. A previous fix had rewritten `merchant_id` for 22% of rows and never touched
`merchant_mcc` at all. It moved the leak one hop and made it harder to see, which is the worst
of the three available outcomes.

---

## 3. Three warning signs we had, and did not read

1. **A perfect score.** Held-out `REVOCATION_FLOOD` scored PR-AUC **1.000**, recall 1.000, and
   mean predicted probability **exactly 1.0** on a family the model had never seen. A perfectly
   confident perfect score on unseen data is not a triumph; it is a leak alarm.
2. **An ablation variant that beat "everything".** Removing the semantic feature group *improved*
   PR-AUC (0.9884 vs 0.9598). We wrote that up as an honest regularisation finding. It was a
   leakage diagnosis: the semantic group carried the MCC proxy, which was perfect for four
   families and useless for the fifth.
3. **A lift that vanished at the threshold.** The reported +0.0723 DTL feature lift existed only
   in threshold-free ranking. At the deployed 0.5 threshold, variants A and B had *identical*
   confusion matrices and identical rupees saved, to the paisa. A feature group that changes no
   decision is not doing the work its headline claims.

Each of these was visible in an artifact we had already generated. None of them was checked,
because nothing in the pipeline was responsible for checking them.

---

## 4. The fix

**Generator** (`dataset_builder.py`): one shared `MERCHANT_POPULATION`. No merchant and no MCC
belongs to a family. Attacks *concentrate* at high-risk merchants (weights 1.0 / 2.5 / 4.0 by risk
tier) without being *exclusive* to them, and ~8% of legitimate traffic deliberately lands on
out-of-scope MCCs. Because a real household does occasionally shop outside its delegation, and
that is what stops "out of scope" from meaning "fraud". Families constrain MCC only where their
*definition* requires it (laundering must sit at a compliant merchant; scope creep must not).

**Gate** (`leakage_audit.py`): runs before every training run and writes its verdict into
`metrics.json`. It measures, per categorical field, whether any single value determines the label,
and reports the strongest single-categorical shortcut per attack family. It is base-rate aware,
at a 7% base rate a naive purity metric flags every ordinary value as "98% pure" and tells you
nothing.

**Claim gate** (`scripts/check_claims.py`): regenerates `docs/MEASURED_NUMBERS.md` from artifacts
and fails when prose anywhere in the repo quotes a superseded figure. Reproducibility guaranteed
the artifacts were stable; nothing made the *writing* follow them, which is how one quantity ended
up with four different values across the repository.

---

## 5. The corrected result: and why it is a better argument

| | Leaked revision | Corrected |
|---|---|---|
| #1 SHAP feature | `cart_intent_consistency_score` (MCC proxy) | **`exposure_after_tx_ratio`** (the aggregate) |
| Held-out `REVOCATION_FLOOD` | PR-AUC 1.000, mean prob 1.0 🚩 | 0.793 / recall 0.889 |
| Held-out `CROSS_RAIL_SPLIT` | PR-AUC 0.572 | **0.841** |
| Hybrid cross-rail recall, held out | 0.000 | **0.828** |
| DTL feature lift | +0.0723, inert at threshold | **+0.2302**, recall 0.886 vs 0.532 |
| Ablation anomaly (C > A) | present | gone |

The old claim was "ML fundamentally cannot do this." The true claim is sharper and survives
cross-examination:

> A model that sees one transaction at a time scores 0.172 held out and only ~0.5 even when
> trained on the attack, the information is not in the transaction. Give it the aggregate and it
> reaches 0.828 on an unseen family. The deterministic invariant reaches 0.844 having seen
> nothing, and cannot silently degrade on a novel family. **The aggregate view is the thing that
> matters; nobody currently holds it.**

That argument does not need ML to fail. It needs the aggregate to be missing. Which it is, in
every real deployment today. It is a better argument because it is true, and because it survives
the obvious follow-up question that ended the previous one.

---

---

## 6. The sequel: we fixed the number, then over-read it

The corrected table in §5 is the honest one. The **sentence** written underneath
it was not, and it took a second pass to notice.

It said hybrid ML reached 0.828 held out, "within 0.016 of its own seen-family
score, which is what generalisation actually looks like."

The cross-rail slice is **64 transactions**.

```
95% Wilson interval, recall ≈ 0.83, n = 64   →   about ±0.09
the difference being read as meaningful      →          0.016
```

The sentence was resolving a difference roughly five times smaller than the
measurement error, in the headline, on our own data, in a repository whose
stated differentiator is claim discipline. Same shape as the leak. Different
mechanism.

### Why this one is worth more than the first

The leak has an easy moral: audit your generator. This one is harder and more
useful, because **nothing was wrong with the number.** 0.828 is correct. 0.844 is
correct. The experiment was sound, the artifact was accurate, the pipeline was
reproducible, and every one of those safeguards passed while the conclusion drawn
from them was still unsupported.

Reproducibility guarantees you get the same number twice. It says nothing about
whether the number can carry the sentence you attached to it.

### What the intervals actually resolve

| Comparison | Difference | Intervals | Verdict |
|---|---|---|---|
| With aggregate feature (0.828) vs without (0.172) | 0.656 | [0.718, 0.901] vs [0.099, 0.282] | **Real.** Nowhere near overlapping. |
| Hybrid held-out (0.828) vs seen (0.844) | 0.016 | [0.718, 0.901] vs [0.736, 0.913] | **Not resolvable.** No claim made. |
| Invariant held-out (0.8438) vs seen (0.8438) | 0.000 |, | **Identity.** Not a measurement at all. |

That third row is the whole argument, and it is the only one immune to sample
size. The invariant's two columns are equal *because arithmetic over the grant
has no fitted parameter for training data to move*. Row 2's two numbers are two
separate measurements that happened to land close on one run with one seed.
Presenting them as the same kind of evidence was the error.

### The fix, and the part that makes it stick

`wilson_interval()` and `recall_with_ci()` in `detector/baselines.py`. Wilson
rather than the normal approximation, because at n=64 near the boundaries the
normal interval cheerfully returns bounds above 1.0. Every published recall now
ships with its interval, its `n`, and the raw `caught/n` count, in the artifact,
in the README, and on the Detection Lab page.

`headline_finding.claim` used to be a hardcoded sentence that survived the leak
fix unchanged and therefore ended up contradicting the numbers beside it. It is
now **generated from the measured recalls**, including the clause about what n
cannot resolve. A claim computed from the measurement cannot outlive it.

And `tests/test_statistical_honesty.py` pins **both directions**:

```python
def test_the_aggregate_feature_separation_is_real(self, headline):
    assert not _overlap(with_dtl, without_dtl)     # the claim we DO make

def test_the_generalisation_gap_is_NOT_resolvable(self, headline):
    assert _overlap(held, seen)                    # the claim we DECLINED
```

The second one is the unusual half. It asserts that the comparison we backed away
from genuinely cannot be resolved, so the retraction is warranted rather than
performative. If a future run with more data makes it separable, that test fails
and tells us the careful hedge has become an *under*claim, which needs correcting
just as much.

### Both lessons, in one line

The leak was invisible because no leakage audit existed. The over-reading was
invisible because no confidence interval existed. Neither was caught by being
more careful; both were caught by **computing the missing measurement**.


## Check yourself

1. **Why is a perfectly confident perfect score on a held-out family a warning rather than a win?**
2. **Why did `CROSS_RAIL_SPLIT` specifically score 0.000 when the other families scored so well?**
3. **Why does ~8% of legitimate traffic now deliberately land on out-of-scope MCCs?**
4. **Why is a base-rate-aware purity metric necessary at a 7% fraud rate?**
5. **What does it mean that variants A and B had identical confusion matrices?**

<details>
<summary>Answers</summary>

1. Because generalisation to unseen data is hard and produces uncertainty. Certainty on data the
   model has never seen means it is reading something that was written into the input, a
   shortcut, not a learned pattern.
2. Because it was the only family whose MCC also appeared in legitimate traffic. The other
   families gave the model a free categorical shortcut that transferred between them; cross-rail
   split had none, so holding it out removed the only examples that ever exercised
   `exposure_after_tx_ratio`.
3. So that "MCC outside the permitted set" stops being a deterministic fraud label. It also makes
   INV_03's false-positive rate real rather than zero, a policy violation and fraud are different
   questions, and conflating them is what created the leak.
4. Because with 7% fraud, an ordinary value is ~93% legit by construction. A naive
   max(P(fraud|v), P(legit|v)) purity flags essentially every value and detects nothing. Leakage
   is movement toward certainty *beyond* what the base rate already provides.
5. That the DTL feature group changed no decision at the operating threshold, every transaction
   was classified identically with and without it. The reported lift existed only in ranking, so
   the feature group was not doing the work the headline attributed to it.
</details>

---

## Where to go next
→ [LEARN_06. The ML Model](LEARN_06_THE_ML_MODEL.md) · [`docs/MEASURED_NUMBERS.md`](MEASURED_NUMBERS.md)
