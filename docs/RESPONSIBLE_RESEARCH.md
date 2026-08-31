# Responsible Research & Ethical Disclosure Statement

## Project FORSETI (CHIMERA)
**Mastercard Innovation Challenge @ Global Fintech Fest (GFF 2026)**

---

### 1. Synthetic Identities and Data Containment
All consumer identities, agent identifiers, merchant IDs, PANs, VPAs, and bank account numbers utilized within FORSETI are **100% synthetically generated** in accordance with ISO/IEC 27001 data isolation guidelines. No production user data, real-world cardholder records, live credentials, or personally identifiable information (PII) was collected, processed, or stored at any phase of this research.

### 2. Sandbox Simulation Constraints
The adversarial Red Team primitives implemented in FORSETI operate exclusively inside an isolated in-memory multi-rail state machine simulator. None of the attack payloads make live network calls to production card schemes (Mastercard, Visa), payment switches (NPCI, UPI), or real banking gateways.

### 3. Purpose of Dual-Team Formulation
The Red/Blue adversarial framework is designed to discover systemic authorization blind spots created by the interaction of disparate payment rails and autonomous agent delegation protocols *before* malicious actors can exploit them in production. By systematically cataloging 55 attack vectors and verifying defensive invariants mathematically, this research aims to harden next-generation multi-agent financial infrastructure.

### 4. Post-Quantum Integrity
All cryptographic audit features conform to **NIST FIPS 204 ML-DSA-44** parameters, ensuring that tamper-evident audit trails remain secure against future Harvest-Now-Decrypt-Later (HNDL) quantum threats.
