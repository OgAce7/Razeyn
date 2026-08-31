"""
Audit logging.

Writes an immutable, traceable record of every incident's full chain:
detection -> evidence retrieval -> AI decision -> policy decision ->
action -> outcome -- so every automated decision can be reviewed and so
`app/evaluation/` has one place to compute metrics from.

Files:
- schema.py    `AuditRecord` and its nested (frozen) dataclasses
- builder.py   `build_audit_record(...)` -- glues already-produced
               pipeline objects into one AuditRecord (no new computation)
- store.py     `AuditStore` -- in-memory list + JSON save/load, same
               philosophy as `app/policies/ledger.py`

A durable SQLAlchemy-backed `AuditLog` table (see `app/models/`) is a
possible future replacement for `AuditStore`'s in-memory list; nothing
in `builder.py` or the evaluation layer depends on which storage backend
is used, only on the `AuditRecord` shape.
"""

from app.audit.builder import build_audit_record
from app.audit.schema import AuditRecord
from app.audit.store import AuditStore

__all__ = ["AuditRecord", "AuditStore", "build_audit_record"]
