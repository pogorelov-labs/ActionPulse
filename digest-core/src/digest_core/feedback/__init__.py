"""Feedback loop scaffolding (EP-15): close the reactions flywheel.

deliver (api mode, capture post_ids) → record the post_id↔evidence map
(``delivered_ledger``) → harvest reactions (``reactions``) → fold ✓/✗ onto
``evidence_id`` → (next) feed ``eval-gold`` / ``eval-calibrate`` to set a real
``recall_floor`` and flip the judge gate.

This package is the offline-buildable scaffold: the ledger + the harvest logic
(+ its read-client method). The actual harvest call and the gold/calibration
integration are corp-network steps (ADR-012).
"""
