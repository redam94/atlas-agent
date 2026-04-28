"""CLI entrypoint: `atlas-graph backfill`."""
from __future__ import annotations

import argparse
import asyncio
import sys

from atlas_core.config import AtlasConfig
from atlas_core.db.session import (
    create_engine_from_config,
    create_session_factory,
    session_scope,
)
from neo4j import AsyncGraphDatabase

from atlas_graph.backfill import backfill_phase1
from atlas_graph.store import GraphStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="atlas-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill", help="Backfill Phase 1 chunks into Neo4j")
    args = parser.parse_args()
    if args.cmd == "backfill":
        sys.exit(asyncio.run(_run_backfill()))


async def _run_backfill() -> int:
    config = AtlasConfig()
    engine = create_engine_from_config(config)
    factory = create_session_factory(engine)
    driver = AsyncGraphDatabase.driver(
        str(config.graph.uri),
        auth=(config.graph.user, config.graph.password.get_secret_value()),
    )
    graph = GraphStore(driver)
    try:
        async with session_scope(factory) as db:
            result = await backfill_phase1(
                db=db,
                graph=graph,
                progress_cb=_print_progress,
            )
        print(
            f"\nBackfill complete: {result.documents} docs, {result.chunks} chunks, "
            f"{result.batches} batches in "
            f"{(result.finished_at - result.started_at).total_seconds():.1f}s"
        )
    finally:
        await graph.close()
        await engine.dispose()
    return 0


def _print_progress(batch: int, total: int) -> None:
    pct = (batch * 100 // total) if total else 100
    print(f"batch {batch}/{total} ({pct}%)", flush=True)


if __name__ == "__main__":
    main()
