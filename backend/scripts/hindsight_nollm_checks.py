"""M1 follow-up checks that need NO LLM calls (banks use "chunks" extraction).

Subcommands:
  persist-write  --state FILE   write uniquely identified memories and record their ids
  persist-verify --state FILE   after a container restart/recreate, verify they are unchanged
  bank-layout                   compare evidence mapping in one shared bank vs per-customer
                                banks + a shared outcomes bank (banks deleted afterwards)

Every check is PASSED / FAILED / UNVERIFIED. These checks say nothing about LLM
extraction, reflect, or observation consolidation.

Usage (from backend/):
  uv run python scripts/hindsight_nollm_checks.py persist-write --state spike-reports/persist.json
  docker compose restart hindsight
  uv run python scripts/hindsight_nollm_checks.py persist-verify --state spike-reports/persist.json
  uv run python scripts/hindsight_nollm_checks.py bank-layout --report spike-reports/layout.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services.hindsight_memory import (  # noqa: E402
    HindsightMemory,
    HindsightMemoryError,
    customer_tag,
    deal_tag,
    kind_tag,
)

PASSED, FAILED, UNVERIFIED = "PASSED", "FAILED", "UNVERIFIED"
results: list[dict[str, Any]] = []


def record(cid: str, name: str, ok: bool | None, detail: str, data: Any = None) -> None:
    status = UNVERIFIED if ok is None else (PASSED if ok else FAILED)
    results.append({"id": cid, "name": name, "status": status, "detail": detail, "data": data})
    print(f"{status:<10} {cid:<4} {name}\n           {detail}")


def bank(bank_id: str) -> HindsightMemory:
    s = get_settings()
    return HindsightMemory(s.hindsight_effective_base_url, bank_id, api_key=s.hindsight_client_key(),
                           timeout_seconds=s.hindsight_timeout_seconds)


async def make_chunks_bank(mem: HindsightMemory, label: str) -> None:
    await mem.ensure_bank(
        name=f"M1 no-LLM check: {label} (synthetic)",
        retain_mission="Synthetic test data.",
        reflect_mission="Answer only from recorded memories.",
        retain_extraction_mode="chunks",
        enable_observations=False,
    )


async def retain(mem: HindsightMemory, text: str, doc: str, cust: str, kind: str = "interaction") -> None:
    await mem.retain(
        text,
        document_id=doc,
        tags=[customer_tag(cust), deal_tag(f"{cust}-deal"), kind_tag(kind)],
        timestamp=datetime.now(UTC),
        context="sales call notes" if kind == "interaction" else "recorded outcome",
        metadata={"customer_id": cust, "source_record": doc, "synthetic": "true"},
    )


# -- persistence -------------------------------------------------------------


async def persist_write(state_path: Path) -> int:
    run = uuid.uuid4().hex[:8]
    bank_id = f"deal-rescue-persist-{run}"
    cust = f"persist-{run}"
    mem = bank(bank_id)
    try:
        await make_chunks_bank(mem, "persistence")
        docs = {}
        for i in (1, 2):
            doc = f"interaction:{cust}:{i}"
            await retain(mem, f"[SYNTHETIC DEMO DATA, run {run}] Persistence probe {i}: "
                              f"customer {cust} asked for quote revision {i}.", doc, cust)
            recs = await mem.list_memories(document_id=doc)
            docs[doc] = [{"id": r.id, "text": r.text, "tags": sorted(r.tags)} for r in recs]
        ok = all(len(v) == 1 for v in docs.values())
        record("P1", "Write uniquely identified memories", ok,
               f"bank {bank_id}: {sum(len(v) for v in docs.values())} memories in {len(docs)} documents",
               docs)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"run": run, "bank_id": bank_id, "customer": cust,
                                          "written_at": datetime.now(UTC).isoformat(),
                                          "documents": docs}, indent=2))
        return 0 if ok else 1
    except HindsightMemoryError as exc:
        record("P1", "Write uniquely identified memories", False, str(exc))
        return 1
    finally:
        await mem.aclose()


async def persist_verify(state_path: Path, label: str, cleanup: bool) -> int:
    state = json.loads(state_path.read_text())
    mem = bank(state["bank_id"])
    code = 0
    try:
        mismatches = []
        for doc, expected in state["documents"].items():
            got = [{"id": r.id, "text": r.text, "tags": sorted(r.tags)}
                   for r in await mem.list_memories(document_id=doc)]
            if got != expected:
                mismatches.append({"document": doc, "expected": expected, "got": got})
        record("P2", f"Memories unchanged after {label}", not mismatches,
               "all memory ids, texts and tags identical" if not mismatches
               else f"{len(mismatches)} document(s) differ", mismatches or None)
        recs = await mem.recall("quote revision", tags=[customer_tag(state["customer"])])
        expected_ids = {m["id"] for v in state["documents"].values() for m in v}
        found = {r.id for r in recs} & expected_ids
        record("P3", f"Recall finds persisted memories after {label}", found == expected_ids,
               f"{len(found)}/{len(expected_ids)} persisted memory ids returned by recall")
        code = 0 if all(r["status"] == PASSED for r in results) else 1
        if cleanup:
            await mem.delete_bank()
            print(f"deleted bank {state['bank_id']}")
    except HindsightMemoryError as exc:
        record("P2", f"Memories unchanged after {label}", False, str(exc))
        code = 1
    finally:
        await mem.aclose()
    return code


# -- bank layout ---------------------------------------------------------------


async def bank_layout(cleanup: bool) -> int:
    run = uuid.uuid4().hex[:8]
    a, b = f"layout-a-{run}", f"layout-b-{run}"
    shared = bank(f"deal-rescue-layout-shared-{run}")
    bank_a = bank(f"deal-rescue-cust-{a}")
    bank_b = bank(f"deal-rescue-cust-{b}")
    outcomes = bank(f"deal-rescue-outcomes-{run}")
    all_banks = [shared, bank_a, bank_b, outcomes]
    text_a = f"[SYNTHETIC DEMO DATA, run {run}] Aurora: budget capped at USD 42,000; SOC 2 required."
    text_b = f"[SYNTHETIC DEMO DATA, run {run}] Borealis: on-prem deployment required; budget USD 310,000."
    out_a = (f"[SYNTHETIC DEMO DATA, run {run}] Outcome for deal {a}-deal: LOST. "
             "Action taken: 20% discount offered late. Reason recorded: security review not started.")
    try:
        for m, label in zip(all_banks, ["shared", "customer A", "customer B", "outcomes"]):
            await make_chunks_bank(m, label)

        # Design 1: one shared bank + tags. Design 2: per-customer banks + outcomes bank.
        await retain(shared, text_a, f"interaction:{a}:1", a)
        await retain(shared, text_b, f"interaction:{b}:1", b)
        await retain(bank_a, text_a, f"interaction:{a}:1", a)
        await retain(bank_b, text_b, f"interaction:{b}:1", b)
        await retain(outcomes, out_a, f"outcome:{a}-deal", a, kind="outcome")

        # L1: evidence mapping — recall id -> get_memory -> source record (both designs).
        rec = (await shared.recall("budget", tags=[customer_tag(a)]))[0]
        full = await shared.get_memory(rec.id)
        ok = bool(full) and full.document_id == f"interaction:{a}:1" and \
            full.metadata.get("source_record") == f"interaction:{a}:1" and customer_tag(a) in full.tags
        record("L1", "Memory id maps back to source record via get_memory (shared bank)", ok,
               f"get_memory({rec.id}) -> document_id={full.document_id if full else None}, "
               f"metadata.source_record={full.metadata.get('source_record') if full else None}",
               {"recall_result_id": rec.id, "get_memory": full.__dict__ if full else None})

        rec_a = (await bank_a.recall("budget", tags=[customer_tag(a)]))[0]
        full_a = await bank_a.get_memory(rec_a.id)
        ok = bool(full_a) and full_a.document_id == f"interaction:{a}:1"
        record("L2", "Memory id maps back to source record (customer bank)", ok,
               f"get_memory -> document_id={full_a.document_id if full_a else None}")

        # L3: memory ids are bank-scoped — the wrong bank cannot resolve them.
        wrong = await bank_b.get_memory(rec_a.id)
        record("L3", "Memory ids are bank-scoped (lookup in another bank returns 404)", wrong is None,
               "customer B bank returned nothing for customer A's memory id" if wrong is None
               else f"customer B bank resolved customer A's memory: {wrong.text[:60]}")

        # L4: per-customer banks isolate without any tag filter.
        leak_b = [r for r in await bank_b.recall("Aurora budget SOC 2 42,000", tags=[], tags_match="any")
                  if "Aurora" in r.text]
        record("L4", "Customer B bank cannot return customer A memories even unfiltered", not leak_b,
               f"unfiltered recall in B's bank for A's facts returned {len(leak_b)} A memories")

        # L5: shared outcomes bank supports cross-deal lookup and maps to its deal.
        outs = await outcomes.recall("deals lost after late discount", tags=[kind_tag("outcome")],
                                     tags_match="all_strict")
        ok = bool(outs) and outs[0].document_id == f"outcome:{a}-deal" and "LOST" in outs[0].text
        record("L5", "Outcomes bank recall returns recorded outcome with its deal reference", ok,
               f"{len(outs)} result(s); first document_id={outs[0].document_id if outs else None}")

        # L6: memory ids are unique across banks for identical content.
        record("L6", "Same content in two banks gets distinct memory ids", rec.id != rec_a.id,
               f"shared={rec.id} customerA={rec_a.id}")
        code = 0 if all(r["status"] == PASSED for r in results) else 1
    except (HindsightMemoryError, IndexError) as exc:
        record("LX", "bank-layout run", False, f"{type(exc).__name__}: {exc}")
        code = 1
    finally:
        for m in all_banks:
            if cleanup:
                try:
                    await m.delete_bank()
                except HindsightMemoryError:
                    pass
            await m.aclose()
    return code


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("persist-write")
    w.add_argument("--state", type=Path, required=True)
    v = sub.add_parser("persist-verify")
    v.add_argument("--state", type=Path, required=True)
    v.add_argument("--label", default="container restart")
    v.add_argument("--cleanup", action="store_true")
    lay = sub.add_parser("bank-layout")
    lay.add_argument("--keep", action="store_true", help="keep the test banks")
    for sp in (w, v, lay):
        sp.add_argument("--report", type=Path)
    args = p.parse_args()

    if args.cmd == "persist-write":
        code = asyncio.run(persist_write(args.state))
    elif args.cmd == "persist-verify":
        code = asyncio.run(persist_verify(args.state, args.label, args.cleanup))
    else:
        code = asyncio.run(bank_layout(cleanup=not args.keep))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"command": args.cmd, "ran_at": datetime.now(UTC).isoformat(),
                                           "results": results}, indent=2, default=str))
    print(f"exit code {code}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
