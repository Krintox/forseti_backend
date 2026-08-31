# LEARN_21 — Tokenization & Settlement Reconciliation

> **Prerequisites:** [LEARN_04](LEARN_04_THE_DTL_CORE.md), [LEARN_18](LEARN_18_KILL_CHAIN.md)  
> **You will be able to:**
> - Explain what `TokenizedPaymentCredential` is a scoped VIEW onto, and why it is never a second source of authority.
> - Trace the two enforcement points a token use passes through — its own static scope, and the LIVE DTL authority — and give an example where the second one is the only thing that refuses a transaction.
> - Explain why Settlement Conflict and Reconciliation Drift are neither DTL invariants nor Deception Lab detections, and which Kill Chain stages they close.
> - State, precisely, what FORSETI's tokenization model is NOT (a real network token vault) and why that wording matters.  
> **Files this chapter is about:** `backend/app/tokenization/`, `backend/app/settlement/reconciliation.py`, `backend/app/redteam/vectors/settlement_conflict.py`, `backend/app/redteam/vectors/reconciliation_drift.py`

---

## 1. A Synthetic Scoped-Token Model

🧒 **Like you're five**  
Mum gives you a wristband at a fair that says "you can go on 3 rides, nothing above the height limit, until 6pm." The wristband isn't magic money — it only means something because Mum's own rule still applies behind it. If Mum calls the fair operator at 5pm and says "actually, no more rides for them," your wristband stops working even though nobody wrote on it that this could happen.

🏪 **In real life**  
An agent doesn't have to carry the whole delegation around to make one purchase — it can be handed a smaller, scoped credential: this rail, this merchant category, this ceiling, until this time. That's a *token*. The property that matters is the same one the rest of FORSETI is built on: the token cannot mean more than the delegation behind it, and if that delegation is tightened or revoked after the token was issued, the token stops working too — not because the token itself changed, but because it was never an independent source of authority in the first place.

🎓 **Properly**  
`TokenizedPaymentCredential` (`backend/app/tokenization/models.py`) is a synthetic scoped-token model **inspired by token lifecycle and scoped-authorization concepts** in real payment tokenization schemes (EMVCo Payment Tokenisation, network token vaults) — it is explicitly **not** an implementation of Mastercard MDES, Visa Token Service, or any other real network's provisioning flow. `token_id` is an opaque synthetic string, not a network-issued DPAN reference, and no cryptographic provisioning handshake is modelled.

What *is* modelled deterministically is the part relevant to FORSETI's central thesis:

```
TOKEN SCOPE  →  DTL AUTHORITY  →  PAYMENT ACTION
```

Two enforcement points, not one:

1. **At issuance** (`issue_token`, `backend/app/tokenization/lifecycle.py`): a token's `amount_ceiling`, `allowed_rails`, `per_transaction_limit` and `expires_at` are all **clamped** to the live `DTLGlobalAuthorityState` at mint time. A caller cannot request a wider ceiling, an unauthorised rail, or a longer validity window than the delegation actually grants — the code does not trust the request, it computes `min(requested, authority.headroom)` and equivalent clamps for every field.
2. **At use** (`use_token`): every attempted spend is independently re-checked against the token's own static scope **and** the LIVE authority state, not a cached copy of it. This is the property judges specifically probe for — see §3.

---

## 2. The Lifecycle

| State | Meaning | Entered by |
|---|---|---|
| `ISSUED` | Minted, not yet usable | `issue_token()` |
| `ACTIVE` | Usable, spendable up to remaining ceiling | `activate()`, or automatically after a partial use |
| `SCOPED` | Momentarily bound to one in-flight scope check | `use_token()`, for the duration of that one check |
| `USED` | Ceiling exhausted; terminal | `use_token()`, when `cumulative_used` reaches `amount_ceiling` |
| `REVOKED` | Withdrawn before exhaustion; terminal | `revoke(token, reason)` |
| `EXPIRED` | Validity window elapsed; terminal | `check_and_expire()`, called at the top of every `use_token()` |

`TERMINAL_STATUSES = (USED, REVOKED, EXPIRED)` — once a token reaches one of these, `check_and_expire` is a no-op on it (a revoked token that later also passes its `expires_at` stays `REVOKED`, not silently overwritten to `EXPIRED` — the FIRST terminal reason is the one that gets recorded, and `tests/test_tokenization.py::TestExpiry::test_check_and_expire_is_idempotent_on_terminal_statuses` pins exactly that).

A single token supports **multiple uses up to its ceiling**, not one-shot spend — `cumulative_used` and `use_count` accumulate across calls to `use_token`, and the token only becomes `USED` once `remaining_ceiling <= 0`.

---

## 3. Why the Second Enforcement Point Is the Interesting One

A token whose own fields would technically allow a transaction can still be refused, because `use_token` re-checks the live `DTLGlobalAuthorityState` independently:

```python
# backend/app/tokenization/lifecycle.py
if not auth.allows_rail(tx.rail):
    ...
    return False, _violation(..., code="TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY", ...)
if tx.amount > auth.authority_headroom:
    ...
    return False, _violation(..., code="TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY", ...)
```

`tests/test_tokenization.py::TestTokenCannotOutliveTheLiveDelegation` proves this directly: a token is issued while UPI is a permitted rail, the delegation is then narrowed to CARD-only *after* issuance, and a UPI use — which the token's own `allowed_rails` field still lists — is refused with `TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY`, not because the token was edited, but because the authority behind it no longer agrees.

The other five violation codes (`TOKEN_RAIL_OUT_OF_SCOPE`, `TOKEN_MERCHANT_OUT_OF_SCOPE`, `TOKEN_PER_TX_LIMIT_EXCEEDED`, `TOKEN_AMOUNT_CEILING_EXCEEDED`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`) all come from the FIRST enforcement point — the token's own static scope — and exist independently of whatever the live authority currently says.

### Demo surface

`POST /api/tokens/issue`, `GET /api/tokens`, `POST /api/tokens/{id}/revoke`, and `POST /api/tokens/{id}/use` (`backend/app/main.py`) expose this lifecycle live. The `/tokens` frontend page lets you issue a token against the current arena delegation, then attempt a transaction against it and see either `ALLOWED` or the exact violation code and explanation — the same "attempt an action, see the deterministic verdict" pattern the arena uses for attack vectors, applied to token scope instead.

---

## 4. Settlement Conflict & Reconciliation Drift

🧒 **Like you're five**  
Two shops both agree to sell you the same toy for the same allowance money. One shop says "sold, you have it." The other shop says "refunded, here's your money back." Both of those things can't be true about the SAME toy at the SAME time — even though each shop, on its own, did something perfectly ordinary.

🏪 **In real life**  
Everything earlier in this course — the seven DTL invariants, the Intent Firewall, Deception Lab — evaluates a transaction **before or at** authorization. Settlement Conflict and Reconciliation Drift are different: they are **post-authorization lifecycle** failures. Every authority-dimension check can pass cleanly on both legs, and the books can still disagree afterward.

🎓 **Properly**  
`backend/app/settlement/reconciliation.py` is a THIRD parallel concern, alongside DTL invariants (authority dimensions) and Deception Lab (agent reasoning integrity):

```
DTL invariants   → was this transaction inside the grant at auth time?
Deception Lab    → was the agent's own reasoning fed a false premise?
Reconciliation   → do the post-authorization books agree with each other?
```

Two `SyntheticTransaction` fields carry this: `obligation_id` (links two legs as the same underlying economic obligation) and `settlement_action` (`CAPTURE` | `REFUND` | `DUPLICATE_CAPTURE`).

| Check | Fires when | Kill Chain stage | Vector |
|---|---|---|---|
| `detect_settlement_conflict` (`RECON_01`) | One leg of an obligation is `CAPTURE`d on one rail while another leg of the SAME obligation is `REFUND`ed on a **different** rail | 10 — `SETTLEMENT_CONFLICT` | `redteam/vectors/settlement_conflict.py` |
| `detect_reconciliation_drift` (`RECON_02`) | The SAME obligation is captured more than once on the **same** rail (a duplicated/replayed settlement message) | 11 — `RECONCILIATION_DRIFT` | `redteam/vectors/reconciliation_drift.py` |

Both vectors carry their own `authority_profile` (a ₹12,000 ceiling) precisely so their two ₹5,000 legs never accidentally trip `INV_01_GLOBAL_BUDGET_EXCEEDED` — the whole teaching point is that **nothing about the seven authority dimensions catches this**; `tests/test_settlement_reconciliation.py::TestOrchestratorEndToEnd::test_no_authority_dimension_invariant_fires_for_*` pins that every `step_results[i]["proof"]` is `None` for both rounds.

### Containment

`apply_settlement_containment` mirrors `dtl/cost_governor.py`'s proportionality principle without reusing its code path (a settlement conflict is not an authority-dimension violation, so routing it through the cost governor's `invariant_code` dispatch would misrepresent what actually happened): `SETTLEMENT_HOLD` freezes the conflicting leg pending manual reconciliation without disturbing the original capture; `DUPLICATE_SETTLEMENT_REVERSED` removes the excess (second) capture and restores the reconciled total to the single authorised amount.

### This closes the Kill Chain

Before this module, `kill_chain/stages.py` documented an honest gap: two of the eleven lifecycle stages had no implemented vector. `STRATEGY_TO_STAGE` now maps `SETTLEMENT_CONFLICT` and `RECONCILIATION_DRIFT` directly onto their stages — all 11 stages now have an implemented vector behind them. Run rounds 16 and 17 in the arena (or select **Settlement Conflict** / **Reconciliation Drift** in the vector picker) and `kill_chain.stage.code` will read `SETTLEMENT_CONFLICT` / `RECONCILIATION_DRIFT` on the resulting round, contributing to session-level `coverage()` like every other vector.

---

## 5. What This Is Not

Consistent with the wording discipline the rest of this course uses:

- **Not** "FORSETI implements Mastercard Agentic Tokens" or "a local MDES implementation" — say *"a synthetic scoped-token model that demonstrates how tokenized payment credentials can inherit and enforce delegated authority."*
- **Not** a persistent credential store — `TokenStore` (`backend/app/tokenization/store.py`) is in-memory, process-lifetime, exactly like `DTLLedger`. Restarting the backend clears every issued token.
- **Not** a real settlement/clearing engine — `app/settlement/reconciliation.py` demonstrates the CONCEPT of cross-system settlement inconsistency with two synthetic obligations; it does not model actual banking clearing/settlement infrastructure, message formats, or timing.

---

## Check yourself

1. **A token was issued when the live delegation permitted all three rails. The delegation is later narrowed to UPI-only. Does the token's own `allowed_rails` field change? Does a card-rail use still succeed?**
2. **Why is `TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY` a different violation code from `TOKEN_RAIL_OUT_OF_SCOPE`, even though both can refuse the same rail?**
3. **Why do Settlement Conflict and Reconciliation Drift use a NEW display-only dimension label (`SETTLEMENT_INTEGRITY`) instead of one of the seven `AuthorityDimension` values?**
4. **What is the one structural difference between the two vectors' `settlement_action` patterns that makes one a "conflict" and the other "drift"?**
5. **Name the two things FORSETI's tokenization model explicitly is NOT.**

<details>
<summary>Answers</summary>

1. No — the token's own `allowed_rails` field is fixed at issuance and never mutated by later delegation changes. But a card-rail use still fails, because `use_token` independently re-checks `auth.allows_rail(tx.rail)` against the LIVE authority every time, not just the token's own static scope.
2. `TOKEN_RAIL_OUT_OF_SCOPE` means the token's OWN static `allowed_rails` field excludes this rail. `TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY` means the token's own scope would have allowed it, but the live delegation behind the token no longer does — the second check exists specifically so a token cannot outlive the delegation it was scoped from.
3. Because — exactly like `AGENT_INTEGRITY` for the Deception Lab vectors — both vectors satisfy every one of the seven authority dimensions at authorization time. The failure is entirely post-authorization, so labelling it as one of the seven dimensions would misrepresent what actually went wrong.
4. Settlement Conflict pairs a `CAPTURE` with a `REFUND` on a **different** rail (a cross-rail disagreement about the obligation's final state). Reconciliation Drift pairs two `CAPTURE`-type legs on the **same** rail (a same-rail duplicate/replayed settlement application).
5. A real network token vault (e.g. Mastercard MDES/local MDES implementation), and a persistent credential store (tokens live only in-memory, process-lifetime).
</details>

---

## Where to go next
→ Return to [LEARN_00 — Start Here](LEARN_00_START_HERE.md), or see `docs/FINAL_IMPLEMENTATION_AUDIT.md` for the ground-truth status of every module in this course.
