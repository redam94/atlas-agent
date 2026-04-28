"""Errors raised by the graph layer."""
from __future__ import annotations


class GraphUnavailableError(RuntimeError):
    """Raised when Neo4j is unreachable after exhausting retries.

    Surfaces at the router as a 502 (lifespan-time) or as a `failed` ingestion
    job (request-time, via IngestionService's existing exception handler).
    """
