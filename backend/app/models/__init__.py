"""
SQLAlchemy models.

Not implemented yet. Planned tables (to be added in a later step):
- Incident        (detected payment degradation events)
- EvidenceItem     (structured/unstructured evidence linked to an incident)
- Investigation    (AI agent's reasoning trace + diagnosis)
- RecoveryAction   (bounded action chosen + guardrail decision)
- Outcome          (measured recovered revenue)
- AuditLog         (immutable trail of every decision/action)

Each model module should be created here and imported in this file so
`Base.metadata.create_all` (see app/core/db.py) picks it up.
"""
