# LEARN_13: Comprehensive Domain Glossary

> **Prerequisites:** None. Reference dictionary for the entire FORSETI curriculum.  
> **You will be able to:**
> - Look up any term used across the payment security, machine learning, and cryptographic modules.
> - Understand each concept via the standard 3-layer definition (5-year-old, formal, and code citation).
> - Use standard domain vocabulary with precision during demos and technical defenses.  
> **Files this chapter is about:** Reference guide for all repository files.

---

### <a id="ablation-study"></a>Ablation Study
- 🧒 **Like you're five:** Taking away one superpower at a time to see which one helped you win the game.
- 🎓 **Formal Definition:** An experimental procedure that systematically removes specific feature groups or components to quantify their marginal contribution to overall system performance.
- 📍 **Codebase Location:** `backend/app/detector/ablation.py:22`

---

### <a id="acquirer"></a>Acquirer (Acquiring Bank)
- 🧒 **Like you're five:** The shopkeeper's bank that catches the money when you pay.
- 🎓 **Formal Definition:** A financial institution that processes credit and debit card payments on behalf of a merchant, collecting funds from the issuer through the card network.
- 📍 **Codebase Location:** `docs/RESPONSIBLE_RESEARCH.md`

---

### <a id="adaptive-immune-system"></a>Adaptive Immune System
- 🧒 **Like you're five:** A parent who gets sterner each time the same trick is tried. Warning, then high shelf, then locked kitchen.
- 🎓 **Formal Definition:** The closed Red/Blue adaptive loop where Blue's containment response escalates with repeated occurrences of the SAME invariant this session (soft response → capability quarantine → mandate suspension), on top of the pre-existing Red-side strategy adaptation.
- 📍 **Codebase Location:** `backend/app/feedback/policy_adapter.py`; see [LEARN_20](LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md).

---

### <a id="adversarial-cost-governor"></a>Adversarial Cost Governor
- 🧒 **Like you're five:** A fair shopkeeper who removes only the forbidden item from your basket instead of banning you from the shop.
- 🎓 **Formal Definition:** A containment kernel that translates verified invariant violations into proportionate economic actions (e.g. partial authorization, headroom capping) to prevent denial-of-service lockouts.
- 📍 **Codebase Location:** `backend/app/dtl/cost_governor.py:30`

---

### <a id="agentic-mandate"></a>Agentic Mandate
- 🧒 **Like you're five:** A signed permission slip giving an autonomous robot permission to buy things on the web.
- 🎓 **Formal Definition:** A cryptographically bound spending authorization (inspired by Google AP2 and W3C Web Payments) tying an agent's intent to a machine-generated cart manifest.
- 📍 **Codebase Location:** `backend/app/simulator/adapters/agentic_adapter.py:13`

---

### <a id="anti-circularity"></a>Anti-Circularity
- 🧒 **Like you're five:** Making sure the practice test isn't so easy that you just memorize the answers.
- 🎓 **Formal Definition:** A dataset generation design principle that injects statistical noise and overlap between legitimate and fraudulent samples (e.g. ~9% legitimate gift cards) so the classifier learns behavioral patterns rather than synthetic generator artifacts.
- 📍 **Codebase Location:** `backend/app/detector/dataset_builder.py:273`

---

### <a id="attack-family-holdout"></a>Attack-Family Holdout
- 🧒 **Like you're five:** Testing whether you can solve a completely new type of puzzle you've never practiced before.
- 🎓 **Formal Definition:** An evaluation methodology where an entire attack family (e.g. `CROSS_RAIL_SPLIT`) is completely excluded from training data to measure out-of-distribution zero-shot detection.
- 📍 **Codebase Location:** `backend/app/detector/baselines.py:34`

---

### <a id="authority-dimensions"></a>Authority Dimensions
- 🧒 **Like you're five:** The seven rules Mum set: how much, how big, which road, which shop, what kind of items, by what time, and, added later, WHO the money actually goes to.
- 🎓 **Formal Definition:** The seven orthogonal constraints defining a valid delegated grant: `AMOUNT`, `PER_TX`, `RAIL`, `MERCHANT`, `PURPOSE`, `TIME`, and `BENEFICIARY` (the 7th, added by the Agentic Security Runtime expansion, see [Beneficiary Dimension](#beneficiary-dimension)).
- 📍 **Codebase Location:** `backend/app/models/state.py`

---

### <a id="authority-headroom"></a>Authority Headroom
- 🧒 **Like you're five:** How many rupees you have left before you reach Mum's budget limit.
- 🎓 **Formal Definition:** The uncommitted monetary balance remaining on a delegated grant, calculated as $\max(0, \text{global\_budget\_ceiling} - \text{total\_exposure\_global})$.
- 📍 **Codebase Location:** `backend/app/models/state.py:112`

---

### <a id="autonomous-agent"></a>Autonomous Agent
- 🧒 **Like you're five:** A smart software helper that decides what to buy and clicks "pay" all by itself.
- 🎓 **Formal Definition:** An artificial intelligence system endowed with delegated credentials and operational agency to execute multi-step commercial workflows without real-time human supervision.
- 📍 **Codebase Location:** `backend/app/models/state.py:52`

---

### <a id="baseline-model"></a>Baseline Model
- 🧒 **Like you're five:** A standard player you compare yourself against to prove you are actually better.
- 🎓 **Formal Definition:** Reference architectural implementations (rules-only, siloed per-rail ML, global ML without DTL features) evaluated under identical test conditions to quantify the value of DTL.
- 📍 **Codebase Location:** `backend/app/detector/baselines.py:110`

---

### <a id="beneficiary-dimension"></a>Beneficiary Dimension
- 🧒 **Like you're five:** Rule 7: not just how much, how, and where, but WHO the money actually ends up with.
- 🎓 **Formal Definition:** The 7th authority dimension, naming which settlement counterparties (VPAs) a delegation permits, independent of amount, rail, and merchant category. Guarded by `INV_07_UNAUTHORIZED_BENEFICIARY`.
- 📍 **Codebase Location:** `backend/app/models/state.py`; see [LEARN_16](LEARN_16_INTENT_FIREWALL.md).

---

### <a id="canonical-json-rfc-8785"></a>Canonical JSON (RFC 8785)
- 🧒 **Like you're five:** Arranging words in alphabetical order with no extra spaces so everyone gets the exact same letter.
- 🎓 **Formal Definition:** A deterministic serialization standard that sorts dictionary keys and standardizes number/whitespace formatting to ensure cryptographic signatures produce identical hashes across platforms.
- 📍 **Codebase Location:** `backend/app/crypto/canonicalization.py:27`

---

### <a id="card-tokenization"></a>Card Tokenization
- 🧒 **Like you're five:** Swapping your real bank card number for a special substitute game token.
- 🎓 **Formal Definition:** A security standard (EMV/MDES/VTS) replacing a 16-digit primary account number with a surrogate token restricted to a specific device, merchant, or channel.
- 📍 **Codebase Location:** `backend/app/simulator/adapters/card_adapter.py:12`

---

### <a id="constraint-erosion"></a>Constraint Erosion
- 🧒 **Like you're five:** Sneaking one tiny forbidden thing into the cart, then a bigger one next time, then almost the whole cart. Hoping nobody notices it happening slowly.
- 🎓 **Formal Definition:** An attack spreading purpose drift across a sequence of transactions of increasing severity rather than one obvious spike, demonstrating that `INV_02_SEMANTIC_INTENT_DRIFT` is a deterministic membership check that catches a small first slice exactly as reliably as a blatant last one.
- 📍 **Codebase Location:** `backend/app/redteam/vectors/constraint_erosion.py`; see [LEARN_20](LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md).

---

### <a id="cross-rail-split"></a>Cross-Rail Split
- 🧒 **Like you're five:** Spending ₹40 from wallet 1, ₹40 from wallet 2, and ₹40 from wallet 3 so no single wallet catches your ₹100 limit.
- 🎓 **Formal Definition:** An adversarial evasion technique where spend is partitioned across multiple distinct payment rails, staying beneath local rail limits while exceeding the global aggregate budget.
- 📍 **Codebase Location:** `backend/app/redteam/vectors/cross_rail_split.py:20`

---

### <a id="deception-lab"></a>Deception Lab
- 🧒 **Like you're five:** A lie-detector sitting next to the robot helper's ear, checking whether anyone just whispered a trick. Completely separately from whether the robot's purchase broke any money rule.
- 🎓 **Formal Definition:** Four deterministic detectors (prompt injection, tool-output poisoning, context/memory poisoning, self-approval) that re-derive ground truth from data no deception can touch, orthogonal to authority-dimension enforcement, a transaction can be authority-clean and still trip a detector here.
- 📍 **Codebase Location:** `backend/app/deception_lab/detectors.py`; see [LEARN_17](LEARN_17_DECEPTION_LAB.md).

---

### <a id="delegation"></a>Delegation
- 🧒 **Like you're five:** Giving someone your permission to spend your money under strict rules.
- 🎓 **Formal Definition:** The legal and technical granting of bounded financial authority by a human principal to an autonomous secondary agent.
- 📍 **Codebase Location:** `backend/app/models/state.py:45`

---

### <a id="delegation-trust-ledger-dtl"></a>Delegation-Trust Ledger (DTL)
- 🧒 **Like you're five:** The master notebook that watches all your wallets and stops you from overspending.
- 🎓 **Formal Definition:** The central multidimensional state ledger in FORSETI that tracks global exposure and evaluates seven deterministic invariants (the original six, plus `INV_07_UNAUTHORIZED_BENEFICIARY`) across all payment rails.
- 📍 **Codebase Location:** `backend/app/dtl/ledger.py:7`

---

### <a id="drift-vector"></a>Drift Vector
- 🧒 **Like you're five:** One scoreboard combining what all six-or-seven referees already whistled about, so you can see how far off the rules a play was, all at once.
- 🎓 **Formal Definition:** A per-dimension reshaping of the `SemanticDriftProof` objects an invariant evaluation already produced, into a single `overall_drift_score` and an `ALLOW`/`PARTIAL_DRIFT`/`HARD_DRIFT` verdict. Computed by the Intent Firewall; invents no new detection.
- 📍 **Codebase Location:** `backend/app/intent_firewall/drift_engine.py`; see [LEARN_16](LEARN_16_INTENT_FIREWALL.md).

---

### <a id="escalation-ladder"></a>Escalation Ladder
- 🧒 **Like you're five:** First a warning, then the cookie jar moves to a high shelf, then the whole kitchen gets locked, the SAME misbehaviour gets a sterner response each time.
- 🎓 **Formal Definition:** The Blue-side response-strength progression keyed off the count of PRIOR occurrences of the same invariant this session: soft response (1st) → `CAPABILITY_QUARANTINED` (2nd) → `AGENT_SUSPENDED` (3rd+, capped).
- 📍 **Codebase Location:** `backend/app/feedback/policy_adapter.py`; see [LEARN_20](LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md).

---

### <a id="expected-calibration-error-ece"></a>Expected Calibration Error (ECE)
- 🧒 **Like you're five:** Making sure when the weather robot says "80% chance of rain", it actually rains 8 times out of 10.
- 🎓 **Formal Definition:** A scalar metric measuring the discrepancy between predicted model risk probabilities and observed empirical fraud frequencies.
- 📍 **Codebase Location:** `backend/app/detector/calibration.py:75`

---

### <a id="gradient-boosted-decision-tree-gbdt"></a>Gradient-Boosted Decision Tree (GBDT)
- 🧒 **Like you're five:** A team of 300 detective trees where each tree learns to fix the mistakes made by the previous tree.
- 🎓 **Formal Definition:** An ensemble machine learning algorithm that iteratively builds decision trees to minimize a loss function on tabular data.
- 📍 **Codebase Location:** `backend/app/detector/model.py:54`

---

### <a id="graph-sentinel"></a>Graph Sentinel (Payment Graph Sentinel)
- 🧒 **Like you're five:** Watching whether ten different kids who've never met suddenly all start paying the same stranger, something no single kid's piggy bank could ever notice alone.
- 🎓 **Formal Definition:** A training-time, cross-authority entity graph (agent↔merchant, with device-sharing) producing 8 graph-derived ML features (degree, PageRank, betweenness, Louvain community, device sharing). Built once per dataset-generation run; not a live per-round graph.
- 📍 **Codebase Location:** `backend/app/graph_sentinel/graph_builder.py`; see [LEARN_19](LEARN_19_GRAPH_SENTINEL.md).

---

### <a id="hash-chain"></a>Hash Chain
- 🧒 **Like you're five:** A chain of paper clips where each clip is stamped with the exact number of the clip before it.
- 🎓 **Formal Definition:** A cryptographic data structure where each record includes the SHA-256 hash of the previous record, creating an append-only, tamper-evident timeline.
- 📍 **Codebase Location:** `backend/app/arena/events.py:145`

---

### <a id="intent-firewall"></a>Intent Firewall (Agent Intent Firewall)
- 🧒 **Like you're five:** The scoreboard operator standing between the human's wishes and the robot's actions, painting one red or yellow light for "how far off the rules" the robot's play was.
- 🎓 **Formal Definition:** The reshaping/verdict layer that turns invariant-engine proofs into a per-dimension [Drift Vector](#drift-vector) and an `ALLOW`/`PARTIAL_DRIFT`/`HARD_DRIFT` verdict, emitted on every transaction regardless of outcome.
- 📍 **Codebase Location:** `backend/app/intent_firewall/`; see [LEARN_16](LEARN_16_INTENT_FIREWALL.md).

---

### <a id="intent-laundering"></a>Intent Laundering
- 🧒 **Like you're five:** Buying a gift card at the supermarket so it looks like you bought apples.
- 🎓 **Formal Definition:** An attack where an agent converts authorized category spend into liquid stored-value instruments under a legitimate merchant MCC.
- 📍 **Codebase Location:** `backend/app/redteam/vectors/intent_laundering.py:12`

---

### <a id="invariant"></a>Invariant
- 🧒 **Like you're five:** A rule that must ALWAYS be true, with zero exceptions.
- 🎓 **Formal Definition:** A deterministic mathematical predicate evaluated over authority state and transaction attributes that must hold true for authorization to proceed.
- 📍 **Codebase Location:** `backend/app/dtl/invariant_engine.py:38`

---

### <a id="isotonic-regression"></a>Isotonic Regression
- 🧒 **Like you're five:** Straightening out a bent ruler so the measurements are always accurate.
- 🎓 **Formal Definition:** A non-parametric calibration algorithm that fits a monotonic step-wise function to map raw model scores to true calibrated probabilities.
- 📍 **Codebase Location:** `backend/app/detector/calibration.py:48`

---

### <a id="issuer-issuing-bank"></a>Issuer (Issuing Bank)
- 🧒 **Like you're five:** The bank where you keep your real money and which gave you your card.
- 🎓 **Formal Definition:** The financial institution that holds the customer's funds, issues payment cards, and provides authorization holds.
- 📍 **Codebase Location:** `backend/app/simulator/adapters/base.py:11`

---

### <a id="kill-chain"></a>Kill Chain (Agentic Payment Kill Chain)
- 🧒 **Like you're five:** An eleven-panel comic strip of a robber's whole plan, drawn out in advance, so you can point at exactly which panel any real attack belongs to.
- 🎓 **Formal Definition:** An 11-stage agentic-payment lifecycle taxonomy that every existing attack vector maps onto (one primary stage each), plus per-round scoring (detection latency, exposure prevented, blast radius, chain score) and session-level stage coverage. A mapping/scoring layer over existing vectors, not a new attack surface.
- 📍 **Codebase Location:** `backend/app/kill_chain/`; see [LEARN_18](LEARN_18_KILL_CHAIN.md).

---

### <a id="merchant-category-code-mcc"></a>Merchant Category Code (MCC)
- 🧒 **Like you're five:** A 4-digit badge on a store telling everyone what kind of shop it is (5411 = Supermarket).
- 🎓 **Formal Definition:** A 4-digit ISO 18245 code classifying a business by the types of goods or services it provides.
- 📍 **Codebase Location:** `backend/app/ai/agents.py:28`

---

### <a id="ml-dsa-44-fips-204"></a>ML-DSA-44 (FIPS 204)
- 🧒 **Like you're five:** A futuristic digital stamp that even quantum supercomputers cannot forge.
- 🎓 **Formal Definition:** The NIST FIPS 204 Module-Lattice Digital Signature Algorithm (Security Category 2), providing post-quantum unforgeable signatures.
- 📍 **Codebase Location:** `backend/app/crypto/pqc_provider.py:31`

---

### <a id="payment-rail"></a>Payment Rail
- 🧒 **Like you're five:** A highway that moves money from one place to another (Card highway, UPI highway).
- 🎓 **Formal Definition:** An infrastructure network and settlement switch through which financial value is transferred between participants.
- 📍 **Codebase Location:** `backend/app/models/state.py:6`

---

### <a id="post-quantum-cryptography-pqc"></a>Post-Quantum Cryptography (PQC)
- 🧒 **Like you're five:** Secret codes designed to be safe even when quantum supercomputers exist.
- 🎓 **Formal Definition:** Cryptographic algorithms based on lattice theory designed to resist cryptanalysis by both classical and quantum computers.
- 📍 **Codebase Location:** `backend/app/crypto/pqc_provider.py:2`

---

### <a id="precision-recall-auc-pr-auc"></a>Precision-Recall AUC (PR-AUC)
- 🧒 **Like you're five:** A single score measuring how good a detective is at catching real thieves without blaming innocent people.
- 🎓 **Formal Definition:** The area under the precision-recall curve, serving as the gold standard metric for evaluating binary classifiers on imbalanced datasets.
- 📍 **Codebase Location:** `artifacts/evaluation/metrics.json`

---

### <a id="principal"></a>Principal
- 🧒 **Like you're five:** The human boss who owns the bank account.
- 🎓 **Formal Definition:** The primary human or corporate entity delegating bounded authority to an autonomous agent.
- 📍 **Codebase Location:** `backend/app/models/state.py:51`

---

### <a id="reconciliation-drift"></a>Reconciliation Drift
- 🧒 **Like you're five:** A shop's receipt machine printing the same sale twice by mistake, so it looks like you bought two of something you only bought once.
- 🎓 **Formal Definition:** A post-authorization lifecycle failure (Kill Chain stage 11, `RECON_02`) where the same authorised obligation is captured more than once on the same rail, a duplicated/replayed settlement message inflating the reconciled total beyond what was actually authorised. Distinct from a DTL invariant: every authority dimension is satisfied at authorization time on both legs.
- 📍 **Codebase Location:** `backend/app/settlement/reconciliation.py` (`detect_reconciliation_drift`)

---

### <a id="semantic-drift"></a>Semantic Drift
- 🧒 **Like you're five:** When a robot helper starts buying things that don't match the job you hired it to do.
- 🎓 **Formal Definition:** The deviation between the authorized economic intent of a delegation and the actual basket contents or merchant category of a transaction.
- 📍 **Codebase Location:** `backend/app/models/proofs.py:10`

---

### <a id="semantic-drift-proof"></a>Semantic Drift Proof
- 🧒 **Like you're five:** A printed receipt showing the exact rule that was broken, with the math circled in red ink.
- 🎓 **Formal Definition:** A structured, machine-checkable evidence object containing the violated invariant code, dimension, formal expression, and violated SKUs.
- 📍 **Codebase Location:** `backend/app/models/proofs.py:10`

---

### <a id="settlement-conflict"></a>Settlement Conflict
- 🧒 **Like you're five:** One shop says "sold!" and a different shop says "refunded!" about the exact same toy, at the exact same time.
- 🎓 **Formal Definition:** A post-authorization lifecycle failure (Kill Chain stage 10, `RECON_01`) where one leg of an authorised obligation is CAPTURED on one rail while a different leg of the SAME obligation is REFUNDED on a different rail, two locally valid instructions producing an economically inconsistent final state that no single rail-local view can see.
- 📍 **Codebase Location:** `backend/app/settlement/reconciliation.py` (`detect_settlement_conflict`)

---

### <a id="shap-treeexplainer"></a>SHAP (TreeExplainer)
- 🧒 **Like you're five:** A mathematical judge that explains exactly how many points each clue contributed to the final score.
- 🎓 **Formal Definition:** Shapley Additive Explanations calculated via `shap.TreeExplainer`, providing game-theoretic feature attribution for tree models.
- 📍 **Codebase Location:** `backend/app/detector/explainability.py:43`

---

### <a id="temporal-split"></a>Temporal Split
- 🧒 **Like you're five:** Training on yesterday's games and testing on today's games without mixing the dates.
- 🎓 **Formal Definition:** Splitting a time-series dataset strictly by timestamp order to prevent future data from leaking into past training samples.
- 📍 **Codebase Location:** `backend/app/detector/train.py:126`

---

### <a id="tokenized-payment-credential"></a>Tokenized Payment Credential
- 🧒 **Like you're five:** A wristband at a fair that only works for 3 rides, under the height limit, until 6pm, and stops working the moment the fair operator says so, even though nobody wrote on the wristband itself.
- 🎓 **Formal Definition:** `TokenizedPaymentCredential`, a synthetic scoped-token model inspired by token lifecycle and scoped-authorization concepts in real payment tokenization schemes, **not** an implementation of any real network's token vault (e.g. Mastercard MDES). Its scope is clamped to the live DTL authority at issuance and independently re-checked against that authority's current state at every use, so it can never authorise more than the delegation currently allows.
- 📍 **Codebase Location:** `backend/app/tokenization/models.py`, `backend/app/tokenization/lifecycle.py`

---

### <a id="two-phase-exposure-accounting"></a>Two-Phase Exposure Accounting
- 🧒 **Like you're five:** Counting money you've promised to spend immediately so you don't accidentally spend it twice.
- 🎓 **Formal Definition:** A concurrency control mechanism that locks pending in-flight funds before authorization to eliminate multi-rail TOCTOU race conditions.
- 📍 **Codebase Location:** `backend/app/models/state.py:56`

---

### <a id="unified-risk-engine"></a>Unified Risk Engine
- 🧒 **Like you're five:** The office assistant who averages five teachers' grades onto one summary line for the principal, without re-grading anyone's work, and without hiding it if the math teacher already failed you.
- 🎓 **Formal Definition:** An equal-weighted composite of five signals other modules already computed (DTL invariant outcome, Intent Firewall drift, Deception Lab detection, ML probability, kill-chain score). `deterministic_override` makes explicit that the DTL invariant decided the outcome before this score was even computed. It is a synthesis, not a detector.
- 📍 **Codebase Location:** `backend/app/risk_engine/risk.py`; see [LEARN_20](LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md).

---

### <a id="upi-circle"></a>UPI-Circle
- 🧒 **Like you're five:** Letting your brother pay with your UPI account up to a monthly pocket money limit.
- 🎓 **Formal Definition:** An NPCI framework allowing primary UPI account holders to delegate secondary users or sub-wallets with dedicated monthly spend limits.
- 📍 **Codebase Location:** `backend/app/simulator/adapters/upi_adapter.py:11`

---

### <a id="zero-trainserve-skew"></a>Zero Train/Serve Skew
- 🧒 **Like you're five:** Using the exact same ruler in school practice and in the final exam.
- 🎓 **Formal Definition:** The guarantee that the exact same feature extraction code is executed during offline dataset generation and online real-time inference.
- 📍 **Codebase Location:** `backend/app/detector/feature_schema.py:56`

---

## Check yourself

1. **What is the difference between an Issuer and an Acquirer?**
2. **Define the term "Two-Phase Exposure Accounting".**
3. **What does ECE stand for, and what does it measure?**
4. **Why is PR-AUC preferred over ROC-AUC on imbalanced fraud datasets?**
5. **What is the difference between a Hash Chain and a Digital Signature?**

<details>
<summary>Answers</summary>

1. An Issuer is the customer's bank holding the funds and issuing payment tokens; an Acquirer is the merchant's bank processing transactions and collecting settlement funds.
2. An accounting mechanism that tracks settled, authorized, pending, and reserved funds simultaneously to prevent in-flight TOCTOU race conditions.
3. Expected Calibration Error; it measures the difference between predicted model probabilities and actual empirical fraud frequencies.
4. Because ROC-AUC can be artificially inflated by a large number of true negatives in heavily imbalanced datasets ($7.09\%$ fraud), while PR-AUC focuses directly on positive class precision and recall.
5. A hash chain proves sequence ordering but can be rewritten by anyone holding server access; a digital signature uses asymmetric cryptography to prove authenticity and prevent forgery.
</details>

---

## Where to go next
→ [LEARN_14. Teach It Back](LEARN_14_TEACH_IT_BACK.md)
