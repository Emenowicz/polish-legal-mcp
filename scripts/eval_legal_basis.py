"""Measure find_legal_basis on the evaluation set: lexical only vs with the production act index.

Developer tool, not part of the package; needs network (api.sejm.gov.pl). The index is built or
refreshed at --index with the production refresh_act_index (first run ~5 min, later runs only
changed statutes), so the numbers are those of the shipped code path.

    uv run python scripts/eval_legal_basis.py --index /tmp/act_index.sqlite

Recall@10: a query counts when any of its expected acts is among the first 10 candidates.
Queries and how they were collected: tests/fixtures/legal_basis_eval.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.services.act_index import refresh_act_index
from polish_legal_mcp.services.legal import LegalService, warsaw_today

EVALSET = Path(__file__).parent.parent / "tests/fixtures/legal_basis_eval.json"


def rank_of(elis: list[str], expected: list[str]) -> int | None:
    return next((i for i, e in enumerate(elis, 1) if e in expected), None)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(EVALSET.read_text())["queries"]
    eli = EliClient(Settings())
    legal = LegalService(eli)
    rows, times = [], []
    try:
        started = time.perf_counter()
        index = await refresh_act_index(eli, args.index, warsaw_today())
        print(f"index: {len(index.acts)} acts, refresh {time.perf_counter() - started:.0f} s")
        for case in cases:
            legal.act_index = None
            lexical = [c.act.eli for c in (await legal.find_legal_basis(case["q"])).candidates]
            legal.act_index = index
            t0 = time.perf_counter()
            result = await legal.find_legal_basis(case["q"])
            times.append((time.perf_counter() - t0) * 1000)
            hybrid = [c.act.eli for c in result.candidates]
            rows.append(
                (case, rank_of(lexical, case["expected"]), rank_of(hybrid, case["expected"]))
            )
    finally:
        await eli.aclose()

    fmt = lambda v: "-" if v is None else str(v)  # noqa: E731
    print(f"\n{'query':64} {'kind':8} {'lex':>4} {'index':>5}")
    for case, lex, hyb in rows:
        print(f"{case['q'][:64]:64} {case['kind']:8} {fmt(lex):>4} {fmt(hyb):>5}")
    for kind in (None, *sorted({c["kind"] for c, _, _ in rows})):
        sel = [(lex, hyb) for c, lex, hyb in rows if kind is None or c["kind"] == kind]
        lex_hits = sum(1 for lex, _ in sel if lex is not None)
        hyb_hits = sum(1 for _, hyb in sel if hyb is not None)
        print(
            f"recall@10 {kind or 'all':9} lexical {lex_hits}/{len(sel)}  index {hyb_hits}/{len(sel)}"
        )
    lost = [c["q"][:60] for c, lex, hyb in rows if lex is not None and hyb is None]
    print(
        f"regressions: {lost or 'none'}; query with index median {statistics.median(times):.0f} ms"
    )


if __name__ == "__main__":
    asyncio.run(main())
