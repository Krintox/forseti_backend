# Post-Quantum Cryptographic Audit Architecture

## NIST FIPS 204 ML-DSA-44 Implementation
ML-DSA (Module-Lattice Digital Signature Algorithm) is the standardized successor to CRYSTALS-Dilithium published in NIST FIPS 204.

FORSETI integrates **ML-DSA-44 (Parameter Set 2, 128-bit quantum security category)** to create tamper-evident cryptographic audit logs for all DTL authority transitions.

## Strategic Value Hierarchy
```
        FORSETI
           │
           ▼
Delegation Authority Invariant
           │
           ▼
Cross-Rail Detection
           │
           ▼
Hybrid ML Detection
           │
     ┌─────┴─────┐
     ↓           ↓
Explainability  Containment
     │
     ▼
PQC Audit Integrity
```

PQC functions strictly as the **tamper-evident audit layer**, protecting historical authority delegations against future Harvest-Now-Decrypt-Later (HNDL) quantum attacks, while the DTL Invariant Engine and Hybrid ML detector provide real-time fraud prevention.

## Mathematical Verification & Tamper Resistance
- Deterministic RFC 8785 canonical JSON serialization ensures consistent byte representations.
- Cryptographic verification asserts that:
  1. Valid signature on an untampered message returns `True`.
  2. Modifying even a single amount or field in the payload yields `False`.
  3. Flipped or modified bytes in the signature payload yield `False`.
