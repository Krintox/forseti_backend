# LEARN_04 — The DTL Core

> **Prerequisites:** [LEARN_01](LEARN_01_WHAT_AND_WHY.md), [LEARN_03](LEARN_03_MAP_OF_THE_CODEBASE.md)  
> **You will be able to:**
> - Explain the data architecture of the `DTLGlobalAuthorityState` and the four-bucket exposure accounting system.
> - Understand the exact evaluation order of the six invariants and why that order is non-negotiable.
> - Perform the step-by-step arithmetic for every invariant check with real financial numbers.
> - Inspect the structure and contents of a machine-checkable `SemanticDriftProof`.
> - Articulate the philosophy of the Adversarial Cost Governor and how proportionate containment prevents denial-of-service lockouts.  
> **Files this chapter is about:** `backend/app/models/state.py`, `backend/app/models/proofs.py`, `backend/app/dtl/ledger.py`, `backend/app/dtl/invariant_engine.py`, `backend/app/dtl/cost_governor.py`

---

## 1. The Delegation-Trust Ledger (DTL) Invention

🧒 **Like you're five**  
Think of the DTL as a magic notebook on Mum's kitchen table. Whenever the robot helper goes out to buy things, it must write its plans in the notebook *before* it pays. The notebook has six golden rules. If the robot wants to buy something after bedtime (Rule 1), or uses the wrong shop lane (Rule 2), or tries to buy a ₹5,000 toy all at once (Rule 3), or goes to a toy store instead of a grocery store (Rule 4), or hides a game card inside a bag of apples (Rule 5), or tries to spend more than Mum's ₹10,000 total (Rule 6), the notebook catches it instantly!

🏪 **In real life**  
In autonomous commerce, an AI agent is issued payment credentials across multiple payment networks. Payment rails only validate transactions locally. The **Delegation-Trust Ledger (DTL)** is the centralized, cross-rail authority kernel (`backend/app/dtl/ledger.py:7`). It tracks the global state of the user's grant and evaluates all transactions against deterministic mathematical constraints before local rail capture.

🎓 **Properly**  
The DTL decouples delegated authority verification from rail-specific settlement networks. It maintains a canonical authority state `DTLGlobalAuthorityState` (`backend/app/models/state.py:45`) and applies six deterministic predicates implemented in `DTLInvariantEngine` (`backend/app/dtl/invariant_engine.py:38`). When a predicate fails, it emits a structured `SemanticDriftProof` (`backend/app/models/proofs.py:10`), and routes the transaction to `AdversarialCostGovernor` (`backend/app/dtl/cost_governor.py:30`) for graduated containment.

> **A 7th dimension was added after this chapter was first written.** The Agentic Payment Security Runtime expansion added `BENEFICIARY` — *who the money settles to*, independent of amount, rail, or merchant category — alongside the six covered here, with its own invariant `INV_07_UNAUTHORIZED_BENEFICIARY` in the SAME registry this chapter describes. Nothing below about the original six changed to make room for it. Full treatment in [LEARN_16 — The Agent Intent Firewall](LEARN_16_INTENT_FIREWALL.md).

---

## 2. The Authority State Model & Two-Phase Exposure Accounting

```
┌────────────────────────────────────────────────────────────────────────┐
│                   DTL GLOBAL AUTHORITY STATE                           │
│  `backend/app/models/state.py:45`                                      │
├────────────────────────────────────────────────────────────────────────┤
│  authority_id: "auth_household_grocery_2026"                           │
│  principal: "user_shashank_primary"  │ agent_id: "agent_household_butler"│
│  global_budget_ceiling: ₹10,000.00                                     │
├────────────────────────────────────────────────────────────────────────┤
│                     TWO-PHASE EXPOSURE BREAKDOWN                       │
│                                                                        │
│  [1. Settled]     cumulative_spent_settled    = ₹0.00 (Finalized)      │
│  [2. Authorized]  cumulative_spent_authorized = ₹0.00 (Captured hold)  │
│  [3. Pending]     pending_spend_global        = ₹0.00 (In validation)  │
│  [4. Reserved]    reserved_spend_global       = ₹0.00 (Sub-delegated)  │
│  ────────────────────────────────────────────────────────────────────  │
│  TOTAL EXPOSURE = [1] + [2] + [3] + [4]       = ₹0.00                  │
│  HEADROOM       = ₹10,000.00 - TOTAL EXPOSURE = ₹10,000.00             │
└────────────────────────────────────────────────────────────────────────┘
```

### Why Four Exposure Buckets?

A naive ledger only counts **settled money**. However, payment settlement takes hours or days (T+1). If an autonomous agent dispatches three simultaneous ₹4,000 requests across three rails in parallel:
- At millisecond 0: Settled spend = ₹0.
- Leg 1 checks: `0 + 4,000 <= 10,000` -> PASS.
- Leg 2 checks: `0 + 4,000 <= 10,000` -> PASS (Leg 1 has not settled!).
- Leg 3 checks: `0 + 4,000 <= 10,000` -> PASS (Legs 1 & 2 have not settled!).

This creates a severe **time-of-check to time-of-use (TOCTOU) race condition**. 

FORSETI eliminates this by tracking four distinct buckets (`backend/app/models/state.py:56-59`):
1. `cumulative_spent_settled`: Transactions whose interbank funds transfer is complete.
2. `cumulative_spent_authorized`: Transactions approved by the issuer awaiting clearing.
3. `pending_spend_global`: In-flight transactions currently undergoing invariant verification.
4. `reserved_spend_global`: Earmarked allocations or sub-delegation pools.

Total global exposure is computed as (`backend/app/models/state.py:89`):

```python
# backend/app/models/state.py:89
@property
def total_exposure_global(self) -> float:
    return (
        self.cumulative_spent_settled +
        self.cumulative_spent_authorized +
        self.pending_spend_global +
        self.reserved_spend_global
    )

@property
def authority_headroom(self) -> float:
    return max(0.0, self.global_budget_ceiling - self.total_exposure_global)
```

---

## 3. The Six Invariants & Order of Execution

> **These six are where the design started, not where it ended.** The live engine
> evaluates **seven** authority invariants — BENEFICIARY (`INV_07`) is added in
> [LEARN_16](LEARN_16_INTENT_FIREWALL.md) — preceded by `INV_08_MANDATE_SUSPENDED`,
> which is a *policy state* rather than a dimension of the grant and is covered in
> [LEARN_20](LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md). This chapter builds the six that
> make the argument; the other two are additive and neither changes the order below.


In `DTLInvariantEngine.evaluate_all()` (`backend/app/dtl/invariant_engine.py:117`), the checks are executed in a strict, deliberate order (`invariant_engine.py:124`):

```python
# backend/app/dtl/invariant_engine.py:124
checks = (
    self._check_time,               # 1. TIME: Is the grant still valid?
    self._check_rail,               # 2. RAIL: Is this payment rail allowed?
    self._check_per_tx_cap,         # 3. PER_TX: Is this single transaction too large?
    self._check_mcc,                # 4. MERCHANT: Is the merchant category allowed?
    self._check_semantic_purpose,   # 5. PURPOSE: Is the basket free of excluded items?
    self._check_global_budget,      # 6. AMOUNT: Does total exposure exceed the ceiling?
)
```

### Why This Order Matters:
1. **Expiry First (`INV_06`):** An expired grant authorizes *nothing*. Evaluating budget or basket contents on an expired mandate is meaningless.
2. **Channel Validity Second (`INV_04`):** If a rail is not permitted (e.g. agent attempted a card payment on a UPI-only grant), the rail is rejected immediately without checking budget headroom.
3. **Per-Transaction Cap Third (`INV_05`):** Catches single large spikes requiring human step-up confirmation before touching aggregate totals.
4. **Merchant Scope Fourth (`INV_03`):** Validates merchant MCC before inspecting basket SKUs.
5. **Semantic Purpose Fifth (`INV_02`):** Inspects cart items for prohibited stored-value instruments.
6. **Global Budget Sixth (`INV_01`):** Computes cross-rail aggregate exposure across all prior settled, authorized, and in-flight transactions.

---

## 4. Worked Arithmetic for All Six Invariants

Let us trace each invariant with concrete numbers, timestamps, and line citations.

### Invariant 1: `INV_06_AUTHORITY_EXPIRED` (Dimension: TIME)
- **Rule:** Mandate is valid only within `delegation_created_at + validity_window_hours` (`invariant_engine.py:202`).
- **Default Grant:** Created at $T_0$, validity window $= 168.0$ hours (7 days). Expiry $= T_0 + 168\text{h}$.
- **Attack Scenario (`LapsedMandateVector`):** Transaction timestamp $T_{\text{tx}} = T_0 + 200\text{h}$.
- **Arithmetic Evaluation:**
  $$T_{\text{tx}} - T_0 = 200.0\text{ hours} > 168.0\text{ hours}$$
  $$\text{Result: } \mathbf{200.0 > 168.0} \implies \text{VIOLATION (Severity: HIGH)}$$
- **Code Reference:** `backend/app/dtl/invariant_engine.py:214`

---

### Invariant 2: `INV_04_UNAUTHORIZED_RAIL` (Dimension: RAIL)
- **Rule:** Transaction rail must be in `permitted_rails` (`invariant_engine.py:228`).
- **Restricted Grant:** `permitted_rails = [PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2]`.
- **Attack Scenario (`RailScopeViolationVector`):** Agent attempts a payment of ₹2,500 on `PaymentRailType.CARD_TOKEN`.
- **Set Membership Evaluation:**
  $$\text{CARD\_TOKEN} \notin \{\text{UPI\_CIRCLE}, \text{AGENTIC\_AP2}\}$$
  $$\text{Result: } \mathbf{\text{CARD\_TOKEN is NOT PERMITTED}} \implies \text{VIOLATION (Severity: HIGH)}$$
- **Code Reference:** `backend/app/dtl/invariant_engine.py:236`

---

### Invariant 3: `INV_05_PER_TX_CAP_EXCEEDED` (Dimension: PER_TX)
- **Rule:** `tx.amount <= auth.per_transaction_cap` (`invariant_engine.py:252`).
- **Constrained Grant:** Global ceiling $= ₹10,000.00$, but `per_transaction_cap = ₹3,000.00`.
- **Attack Scenario (`PerTransactionBreachVector`):** Agent attempts a single purchase of ₹6,500.00.
- **Arithmetic Evaluation:**
  $$\text{Global Headroom Check: } 0 + ₹6,500 \le ₹10,000 \quad (\text{Headroom OK})$$
  $$\text{Per-Transaction Cap Check: } ₹6,500.00 > ₹3,000.00$$
  $$\text{Result: } \mathbf{6,500.00 > 3,000.00} \implies \text{VIOLATION (Severity: MEDIUM)}$$
- **Code Reference:** `backend/app/dtl/invariant_engine.py:260`

---

### Invariant 4: `INV_03_UNAUTHORIZED_MCC` (Dimension: MERCHANT)
- **Rule:** `tx.merchant_mcc in auth.permitted_mccs` (`invariant_engine.py:277`).
- **Default Grant:** `permitted_mccs = ["5411", "5499", "4900"]` (Grocery, Food, Utilities).
- **Attack Scenario (`ScopeCreepVector`):** Agent attempts a ₹3,200 purchase at an electronics store (`MCC 5732`).
- **Set Membership Evaluation:**
  $$\text{"5732"} \notin \{\text{"5411"}, \text{"5499"}, \text{"4900"}\}$$
  $$\text{Result: } \mathbf{\text{"5732" is UNAUTHORIZED}} \implies \text{VIOLATION (Severity: HIGH)}$$
- **Code Reference:** `backend/app/dtl/invariant_engine.py:284`

---

### Invariant 5: `INV_02_SEMANTIC_INTENT_DRIFT` (Dimension: PURPOSE)
- **Rule:** Cart items must not match `semantic_exclusions` (`invariant_engine.py:293`).
- **Default Grant:** `semantic_exclusions = ["STORED_VALUE", "GIFT_CARD", "CRYPTO_TOKEN", "RE_LIQUEFIABLE"]`.
- **Attack Scenario (`IntentLaunderingVector`):** Agent purchases a ₹9,500 basket under grocery MCC 5411 containing:
  - SKU 1: Organic Fresh Milk (`category="GROCERY"`, ₹1,000.00)
  - SKU 2: Digital Stored Value Voucher (`category="GIFT_CARD"`, `is_stored_value=True`, ₹8,500.00)
- **Evaluation:**
  $$\text{Violated SKUs} = \{\text{SKU 2 (category="GIFT\_CARD")}\}$$
  $$\text{Result: } \mathbf{\text{"GIFT\_CARD" matches semantic\_exclusions}} \implies \text{VIOLATION (Severity: CRITICAL)}$$
- **Code Reference:** `backend/app/dtl/invariant_engine.py:299`

---

### Invariant 6: `INV_01_GLOBAL_BUDGET_EXCEEDED` (Dimension: AMOUNT)
- **Rule:** `total_exposure_global + tx.amount <= global_budget_ceiling` (`invariant_engine.py:316`).
- **Default Grant:** `global_budget_ceiling = ₹10,000.00`.
- **Attack Scenario (`CrossRailSplitVector`):** Three ₹4,000 transactions across three rails:
  - **Leg 1 (Card):**
    $$0.00 + ₹4,000.00 = ₹4,000.00 \le ₹10,000.00 \quad \mathbf{\checkmark\text{ (Approved)}}$$
    Ledger books ₹4,000 to `cumulative_spent_authorized` (`ledger.py:40`). Remaining headroom $= ₹6,000.00$.
  - **Leg 2 (UPI):**
    $$₹4,000.00 + ₹4,000.00 = ₹8,000.00 \le ₹10,000.00 \quad \mathbf{\checkmark\text{ (Approved)}}$$
    Ledger books ₹4,000 to `cumulative_spent_authorized`. Remaining headroom $= ₹2,000.00$.
  - **Leg 3 (Agentic Mandate):**
    $$₹8,000.00 + ₹4,000.00 = \mathbf{₹12,000.00 > ₹10,000.00} \quad \mathbf{\times\text{ (VIOLATION)}}$$
    Breach amount $= ₹12,000.00 - ₹10,000.00 = \mathbf{₹2,000.00}$.
- **Code Reference:** `backend/app/dtl/invariant_engine.py:321`

---

## 5. The Structure of a `SemanticDriftProof`

When an invariant check fails, it returns a strongly-typed cryptographic proof object (`backend/app/models/proofs.py:10`):

```python
# backend/app/models/proofs.py:10
class SemanticDriftProof(BaseModel):
    proof_id: str                          # Unique identifier (e.g. "proof_budget_718293")
    timestamp: datetime                    # UTC timestamp of invariant failure
    invariant_code: str                    # E.g. "INV_01_GLOBAL_BUDGET_EXCEEDED"
    authority_dimension: str               # "AMOUNT" | "PER_TX" | "RAIL" | "MERCHANT" | "PURPOSE" | "TIME"
    severity: str                          # "MEDIUM" | "HIGH" | "CRITICAL"
    authorized_state_predicate: str        # E.g. "Total aggregate spend <= ₹10,000.00"
    actual_drift_observation: str          # E.g. "Cross-rail split pushing total exposure to ₹12,000.00"
    drift_score: float                     # Normalized confidence score (0.0 to 1.0)
    remediation_suggestion: str            # Recommended containment policy
    formal_expression: str                 # ASSERT predicate expression
    proof_explanation: str                 # Human-readable explanation with exact arithmetic
    violated_skus: Optional[List[str]]     # List of offending SKU IDs (for purpose drift)
    pqc_signature_bytes_hex: str           # PQC signature placeholder/envelope field
    pqc_verified: bool                     # Signature status
```

---

## 6. The Adversarial Cost Governor

🧒 **Like you're five**  
If you accidentally put a toy in the grocery cart with the vegetables, Mum doesn't cancel the entire grocery trip and lock you out of the house. She simply takes the toy out of the cart, pays for the vegetables, and continues home! The Cost Governor does the exact same thing for the computer.

🏪 **In real life**  
In traditional banking systems, when a fraud alert triggers, the bank places an immediate blanket lock on the customer's entire debit/credit card. For autonomous agents, **a blanket block creates a catastrophic denial-of-service attack surface**: an attacker can flood an agent with small out-of-scope requests, triggering complete operational paralysis for the human principal.

The **Adversarial Cost Governor** (`backend/app/dtl/cost_governor.py:30`) solves this by applying the **smallest proportionate response** tailored to the violated dimension:

```
┌────────────────────────────────────────────────────────────────────────┐
│             ADVERSARIAL COST GOVERNOR CONTAINMENT MATRIX               │
├─────────────┬───────────────────────────────┬──────────────────────────┤
│ Dimension   │ Containment Action            │ Headroom Consumed?       │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ PURPOSE     │ PARTIAL_AUTH                  │ YES (legitimate SKUs only│
│             │ (Authorizes grocery items,    │ e.g. ₹1,000 booked;      │
│             │ quarantines ₹8,500 gift card) │ ₹8,500 quarantined)     │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ AMOUNT      │ HEADROOM_CAP                  │ YES (up to remaining     │
│             │ (Authorizes remaining ₹2,000; │ headroom; excess         │
│             │ quarantines ₹2,000 overflow)  │ quarantined)             │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ PER_TX      │ STEP_UP_REQUIRED              │ NO (held for biometric   │
│             │ (Holds single large tx for    │ human confirmation;      │
│             │ user confirmation; agent keeps│ headroom untouched)      │
│             │ normal small-tx grant)        │                          │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ RAIL        │ RAIL_SCOPE_BLOCK              │ NO (unauthorized rail is │
│             │ (Declines unauthorized rail;  │ refused; permitted rails │
│             │ allowed rails stay usable)    │ stay 100% operational)   │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ MERCHANT    │ SCOPE_QUARANTINE              │ NO (unauthorized MCC is  │
│             │ (Routes to shadow ledger;     │ isolated; valid stores   │
│             │ valid merchants clear)        │ continue clearing)       │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ TIME        │ RE_CONSENT_HOLD               │ NO (held pending fresh   │
│             │ (Holds lapsed mandate tx;     │ grant from principal)    │
│             │ user instruments untouched)   │                          │
├─────────────┼───────────────────────────────┼──────────────────────────┤
│ BENEFICIARY │ BENEFICIARY_SCOPE_BLOCK       │ NO (unauthorized         │
│ (7th dim,   │ (Refuses settlement to an     │ beneficiary is refused;  │
│ LEARN_16)   │ unlisted counterparty)        │ authorised ones stay usable)│
└─────────────┴───────────────────────────────┴──────────────────────────┘
```

---

## Check yourself

1. **Why does FORSETI track four exposure buckets instead of just settled spend?**
2. **In what exact order are the six invariants evaluated?**
3. **What happens during a `PARTIAL_AUTH` containment action on a basket with grocery items and a gift card?**
4. **Does a `RAIL_SCOPE_BLOCK` containment action consume authority headroom from the global budget?**
5. **What is the mathematical condition under which `INV_01` emits a violation?**

<details>
<summary>Answers</summary>

1. To prevent TOCTOU race conditions where an autonomous agent dispatches simultaneous transactions across multiple rails before any single transaction settles.
2. 1. TIME (`INV_06`), 2. RAIL (`INV_04`), 3. PER_TX (`INV_05`), 4. MERCHANT (`INV_03`), 5. PURPOSE (`INV_02`), 6. AMOUNT (`INV_01`).
3. The Cost Governor approves and books the legitimate grocery portion (e.g. ₹1,000) and isolates the prohibited gift card portion (e.g. ₹8,500) into a quarantined state (`backend/app/dtl/cost_governor.py:126`).
4. No. Scope violations consume zero headroom, preserving the user's remaining grant for valid transactions on permitted rails (`backend/app/dtl/cost_governor.py:58`).
5. When `total_exposure_global + tx.amount > global_budget_ceiling` (`backend/app/dtl/invariant_engine.py:318`).
</details>

---

## Where to go next
→ [LEARN_05 — Attacks and Simulator](LEARN_05_ATTACKS_AND_SIMULATOR.md)
