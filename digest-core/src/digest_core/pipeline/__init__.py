"""Pipeline internals extracted from ``run.py`` (ACTPULSE-23).

Leaf modules only: each imports from ``config`` / ``ingest`` / ``llm`` and
never from ``run``, so ``run`` can shrink without creating a cycle.
"""
