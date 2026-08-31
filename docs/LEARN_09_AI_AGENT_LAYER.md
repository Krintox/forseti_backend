# LEARN_09: The AI Advisory Agent Layer

> **Prerequisites:** [LEARN_01](LEARN_01_WHAT_AND_WHY.md), [LEARN_04](LEARN_04_THE_DTL_CORE.md), [LEARN_07](LEARN_07_ARENA_AND_EVENTS.md)  
> **You will be able to:**
> - Explain the architecture of the 10-provider fallback LLM client and its rate-limiting design.
> - Understand the standardized JSON envelope and the three non-negotiable rules governing all AI agents.
> - Detail the purpose, REST route, schema validator, and fallback mechanism for all 12 AI agents.
> - Trace the counterfactual simulation workflow where LLM hypotheses are verified through real simulator execution.
> - Articulate the strict architectural boundary between the deterministic core and advisory AI agents.  
> **Files this chapter is about:** `backend/app/ai/llm_client.py`, `backend/app/ai/agents.py`, `backend/app/ai/routes.py`

---

## 1. The Multi-Provider Fallback Client (`llm_client.py`)

🧒 **Like you're five**  
When the computer wants to ask a big smart brain (an LLM) to write a note or explain an event, it has a phonebook of 10 different assistant services. First, it calls the free assistant. If that assistant is busy or has no signal, it calls the backup assistant. If none of the assistants answer the phone, it uses a tidy pre-written letter template from its desk drawer!

🏪 **In real life**  
External LLM APIs (OpenAI, Anthropic, Gemini, Groq, Mistral, OpenRouter) frequently experience rate limits (HTTP 429), latency spikes, or temporary outages. `LLMClient` (`backend/app/ai/llm_client.py:95`) implements a resilient **tier-ordered fallback chain** using Python standard library `urllib.request` (avoiding heavy third-party SDK dependencies).

```
┌────────────────────────────────────────────────────────────────────────┐
│                   10-PROVIDER LLM FALLBACK PIPELINE                    │
├──────────────┬─────────────────────────────────────────────────────────┤
│ Tier 1 (Free)│ Groq ──► Cerebras ──► Mistral ──► OpenRouter (Free)     │
├──────────────┼─────────────────────────────────────────────────────────┤
│ Tier 2 (Std) │ Google Gemini ──► Together AI ──► Cohere ──► DeepSeek   │
├──────────────┼─────────────────────────────────────────────────────────┤
│ Tier 3 (High)│ OpenAI (GPT-4o) ──► Anthropic (Claude 3.5 Sonnet)       │
├──────────────┼─────────────────────────────────────────────────────────┤
│ Tier 4 (None)│ Deterministic Template / Rule-Based Code Fallback       │
└──────────────┴─────────────────────────────────────────────────────────┘
```

### Key Rotation and Secrets Policy

- **Key Rotation:** For each provider, the client probes up to 9 environment variable names (`<PROVIDER>_API_KEY`, `<PROVIDER>_API_KEY_1` … `_8`, `ai/llm_client.py:54`).
- **Secrets Policy:** The `.env` file is strictly ignored by git (`.gitignore:2`). The client reports provider availability and token latency, but **never logs, prints, or exposes key values in API responses** (`ai/llm_client.py:270`).

---

## 2. The Standard Agent Envelope & The Three Rules

Every one of the 12 AI agents wraps its output in a standardized envelope (`backend/app/ai/agents.py:52`):

```json
{
  "agent": "intent_compiler",
  "status": "OK",
  "generated_at": "2026-08-19T14:32:01.120Z",
  "result": { "...": "..." },
  "llm": {
    "provider": "groq",
    "model": "llama-3.3-70b-versatile",
    "latency_ms": 342.5,
    "cached": false
  },
  "enforcement": "advisory only - the deterministic engine decides"
}
```

### The Three Inviolable Agent Rules (`ai/agents.py:7`)

1. **THE LLM NEVER ENFORCES:** The LLM explains, translates, and proposes. It *never* decides an authorization outcome. Every proposal is schema-validated and checked by the deterministic DTL engine.
2. **EVERY AGENT DEGRADES HONESTLY:** If an LLM is unavailable, has no API key, or returns invalid JSON, the envelope returns `status: "FALLBACK"` or `"LLM_UNAVAILABLE"` and provides a deterministic template response.
3. **NOTHING IS HARDCODED:** Prompts are dynamically assembled from live DTL state, real event logs, and generated artifacts.

---

## 3. Catalog of the 12 AI Agents

```
┌────────────────────────────────────────────────────────────────────────┐
│                        THE 12 AI AGENTS CATALOG                        │
├────┬────────────────────────┬──────────────────────┬───────────────────┤
│ #  │ Agent Name             │ REST Route           │ Primary Function  │
├────┼────────────────────────┼──────────────────────┼───────────────────┤
│ 1  │ Intent Compiler        │ POST /api/ai/intent/ │ Natural language  │
│    │                        │ compile              │ -> DTL Authority  │
│ 2  │ Semantic Cart Auditor  │ POST /api/ai/cart/   │ SKU analysis &    │
│    │                        │ audit                │ gift card split   │
│ 3  │ Event Explainer        │ POST /api/ai/event/  │ What/How/Why event│
│    │                        │ explain              │ diagnostics       │
│ 4  │ Adversarial Strategist │ POST /api/ai/red/    │ Proposes novel Red│
│    │                        │ propose              │ attack parameters │
│ 5  │ Incident Report Writer │ POST /api/ai/incident│ Regulator-ready   │
│    │                        │ /report              │ incident reporting│
│ 6  │ Policy Advisor         │ POST /api/ai/policy/ │ Minimal defensive │
│    │                        │ advise               │ policy tightening │
│ 7  │ Customer Notice Writer │ POST /api/ai/customer│ Transparent SMS / │
│    │                        │ /notice              │ Email notice copy │
│ 8  │ Regulatory Mapper      │ POST /api/ai/        │ Maps to RBI/NPCI/ │
│    │                        │ regulatory/map       │ PCI regulations   │
│ 9  │ Merchant Risk Profiler │ POST /api/ai/merchant│ Detects MCC vs    │
│    │                        │ /profile             │ inventory mismatch│
│ 10 │ Counterfactual Analyst │ POST /api/ai/        │ Real re-simulation│
│    │                        │ counterfactual       │ of what-if limits │
│ 11 │ Log Copilot            │ POST /api/ai/log/    │ Natural language  │
│    │                        │ query                │ -> log query filter│
│ 12 │ Model Card Generator   │ GET /api/ai/model-   │ Honest ML model   │
│    │                        │ card                 │ card documentation│
└────┴────────────────────────┴──────────────────────┴───────────────────┘
```

---

### Agent Specifications

#### 1. Intent Compiler (`intent_compiler`, `ai/agents.py:64`)
- **Problem:** Human intent is rich (*"₹10,000 for groceries this week, but ask me before any single item over ₹3,000"*), but banking cards only take a single limit.
- **Solution:** Compiles natural language into a machine-checkable `DTLGlobalAuthorityState` vector.
- **Validator:** `_validate_compiled_intent()` (`ai/agents.py:112`) ensures all generated MCCs exist in `KNOWN_MCCS` (`ai/agents.py:28`) and that monetary numbers are positive floats.
- **Fallback:** Rule-based regex parser.

#### 2. Semantic Cart Auditor (`cart_auditor`, `ai/agents.py:175`)
- **Problem:** Merchant codes are coarse; a supermarket selling electronics or gift cards looks compliant to a card network.
- **Solution:** Evaluates the economic substance of individual basket SKUs against authorized purpose.
- **Validator:** Re-checks item calculations against `semantic_exclusions`.

#### 3. Event Explainer (`event_explainer`, `ai/agents.py:280`)
- **Problem:** Security dashboards show raw log entries that analysts must manually decipher.
- **Solution:** Generates structured four-part explanations: **What** happened, **How** it occurred, **Why** it was flagged, and **Why it matters**.
- **Fallback:** Deterministic template generator (`ai/agents.py:330`).

#### 4. Adversarial Strategist (`red_strategist`, `ai/agents.py:390`)
- **Problem:** Security systems are often tested only against attacks their authors imagined.
- **Solution:** Analyzes the active defense policy and proposes novel attack vector parameters.
- **Safety Boundary:** The proposed attack is dispatched to the **deterministic simulator engine**, which executes and judges it objectively.

#### 5. Incident Report Writer (`incident_report`, `ai/agents.py:490`)
- **Problem:** Writing compliance reports following an intercepted breach is labor-intensive.
- **Solution:** Generates an audit-ready incident report from the actual event log timeline, explicitly marking missing facts as "Not Established".
- **`deterministic_appendix` (Agentic Security Runtime expansion):** Every response now carries a small block of cross-module facts, kill-chain stage/score (LEARN_18), Intent Firewall hard-drift count (LEARN_16), Deception Lab detection count (LEARN_17), attached **unconditionally**, computed directly from the round result, never from the model. It is present even when `status: "LLM_UNAVAILABLE"`, because an incident report's FACTS must not depend on whether a language model answered; only its narrative prose should. See §5 below for a real UI bug this property exposed.

#### 6. Policy Advisor (`policy_advisor`, `ai/agents.py:590`)
- **Problem:** After an incident, human operators reflexively over-tighten limits, hurting legitimate conversion.
- **Solution:** Suggests the minimal sufficient policy adjustment and calculates its estimated false-positive impact.

#### 7. Customer Notice Writer (`customer_notice`, `ai/agents.py:685`)
- **Problem:** A contained transaction reaches the customer as an unexplained decline, destroying trust.
- **Solution:** Drafts transparent customer communications (SMS, push notification, email) explaining what was held, what cleared, and how to approve.

#### 8. Regulatory Mapper (`regulatory_mapper`, `ai/agents.py:770`)
- **Problem:** Banks cannot adopt security controls without mapping them to compliance mandates.
- **Solution:** Maps FORSETI invariants to RBI Master Directions on Digital Payment Security, NPCI OC 201-B, and PCI-DSS requirements.

#### 9. Merchant Risk Profiler (`merchant_profiler`, `ai/agents.py:850`)
- **Problem:** Merchants declare their own MCCs during onboarding and rarely get re-audited.
- **Solution:** Compares merchant trading names and item catalogs against declared MCCs to flag category laundering.

#### 10. Counterfactual Analyst (`counterfactual_analyst`, `ai/agents.py:930`)
- **Problem:** "Would a ₹2,500 per-transaction cap have stopped this attack?" is usually answered with guesswork.
- **Solution:** The LLM proposes counterfactual limits; **the system re-runs the real simulator with those limits** and reports the actual outcome (`ai/routes.py`).
- **RAIL and PURPOSE dimensions (Agentic Security Runtime expansion):** Originally AMOUNT-only ("what ceiling would have stopped this"), the agent can now also propose "what if the card rail had been disabled" (RAIL) or "what if gift cards had been permitted" (PURPOSE), replayed the same way, in an isolated `orchestrator.sandbox()`, with the mutation read back from the ACTUAL replayed grant (`permitted_rails_tested`, `semantic_exclusions_tested`) rather than merely echoed from the request. **Dimension-gated for honesty:** a round whose strategy runs against a FIXED authority profile (`RAIL_SCOPE_VIOLATION`, `PER_TX_BREACH`, `LAPSED_MANDATE`, `BENEFICIARY_DRIFT`, `CONSTRAINT_EROSION`) only ever offers AMOUNT, proposing RAIL/PURPOSE there would be silently overwritten by that vector's own fixed profile at replay time and misrepresent what was actually tested.

#### 11. Log Copilot (`log_copilot`, `ai/agents.py:1000`)
- **Problem:** Querying JSONL logs during live incidents requires syntax knowledge.
- **Solution:** Translates natural language questions (*"Show all UPI attempts that failed"*) into structured filters executed over the in-memory log.

#### 12. Model Card Generator (`model_card`, `ai/agents.py:1030`)
- **Problem:** Machine learning model cards are often omitted or whitewash model limitations.
- **Solution:** Generates a standardized model card directly from `metrics.json` and `baselines.json`, and **programmatically refuses to generate a card that omits known weaknesses** (`ai/agents.py:1034`).

---

## 4. System Hierarchy: Core vs. Intelligence Layer

```
┌────────────────────────────────────────────────────────────────────────┐
│                   FORSETI SYSTEM HIERARCHY                             │
│  `backend/app/ai/agents.py:1095` (`SYSTEM_HIERARCHY`)                  │
├────────────────────────────────────────────────────────────────────────┤
│ 1. THE CORE INVENTION (Deterministic & Authoritative)                  │
│    • Delegation-Trust Ledger (DTL)                                     │
│    • 7 Deterministic Invariants (`backend/app/dtl/invariant_engine.py`) │
│    • Adversarial Cost Governor (`backend/app/dtl/cost_governor.py`)   │
│    • Calibrated GBDT ML Detector & SHAP (`backend/app/detector/`)      │
│    • Post-Quantum Audit Signatures (`backend/app/crypto/`)             │
├────────────────────────────────────────────────────────────────────────┤
│ 2. THE ADVISORY INTELLIGENCE LAYER (Non-Authoritative)                 │
│    • 12 AI Agents (`backend/app/ai/agents.py`)                         │
│    • Explains events, compiles intent, drafts reports, maps compliance │
│    • Operates strictly outside the real-time authorization path        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 5. A Real Frontend Bug the `deterministic_appendix` Design Exposed

Wiring the incident report's `deterministic_appendix` into `frontend/app/ai/page.tsx` surfaced a genuine gap: the generic `AgentCard` component only rendered `ResultBody` (and therefore the appendix) when `result.result` was truthy, which is **exactly false** in the `LLM_UNAVAILABLE` case the appendix exists to cover. The one guarantee the backend had just built ("facts survive even without an LLM") was silently defeated by the frontend's own render guard. Fixed with a fallback render path in `AgentCard` itself (`frontend/app/ai/page.tsx`), and verified live in BOTH states, this session's environment turned out to have live LLM keys configured after all, so both the normal `ResultBody` path and the fallback path were exercised and confirmed correct through an actual browser session, not merely assumed from reading the two code paths.

---

## Check yourself

1. **How many LLM providers are configured in the fallback chain?**
2. **What are the three inviolable rules governing all 12 AI agents?**
3. **What does the Counterfactual Analyst agent do that proves it is not hallucinating outcomes?**
4. **Which agent compiles natural language instructions into a DTL authority vector?**
5. **Does any AI agent have the authority to approve or decline a payment transaction?**
6. **Why does the Counterfactual Analyst restrict a `RAIL_SCOPE_VIOLATION` round to AMOUNT-only proposals?**

<details>
<summary>Answers</summary>

1. 10 providers across 3 cost/performance tiers (`backend/app/ai/llm_client.py:60`).
2. (1) The LLM never enforces, (2) Every agent degrades honestly with fallbacks, and (3) Nothing is hardcoded (`backend/app/ai/agents.py:7-14`).
3. It dispatches proposed counterfactual constraints to the real Python simulator engine, which re-executes the attack and measures the actual outcome (`backend/app/ai/routes.py`).
4. The Intent Compiler agent (`intent_compiler`, `backend/app/ai/agents.py:64`).
5. No. The AI layer is strictly advisory. All transaction decisions are made deterministically by the DTL Invariant Engine and Cost Governor (`backend/app/ai/agents.py:7`).
6. Because that strategy runs against a FIXED authority profile of its own (`STRATEGY_AUTHORITY_PROFILE`); a RAIL or PURPOSE mutation proposed for it would be silently overwritten by that fixed profile when the round is replayed, making the reported result misrepresent what was actually tested.
</details>

---

## Where to go next
→ [LEARN_10. Frontend](LEARN_10_FRONTEND.md)
