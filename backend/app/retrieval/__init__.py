"""
Evidence Retrieval.

Responsible for: gathering structured evidence (transaction records,
account history, chargeback flags, velocity checks) and unstructured
evidence (support tickets, notes) relevant to a detected incident, and
returning a single evidence bundle for the AI Investigation Agent.

Not implemented yet. Planned contents:
- structured.py    Pull structured evidence from the DB/synthetic data
- unstructured.py  Pull/search unstructured evidence (optionally via Mistral)
- bundle.py        Merge both into a normalized EvidenceBundle object
"""
