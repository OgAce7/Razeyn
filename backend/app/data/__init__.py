"""
Data layer: synthetic payment data + processing helpers.

Implemented:
- schema.py    Field names, enums, and value pools shared by the
               generator and downstream consumers.
- generate.py  Seeded generator that produces transactions.csv +
               incidents.json (ground truth) under synthetic/. Run with
               `python -m app.data.generate` to (re)build the dataset.
- loader.py    load_transactions() / load_incidents() / load_incidents_list()
               — the stable read interface for downstream code.
- synthetic/   Generated output (gitignored; regenerate, don't hand-edit).

Not implemented yet (later steps):
- Incident *detection* logic (this module only generates data with known
  ground truth; detecting incidents from it is a separate component).
"""
