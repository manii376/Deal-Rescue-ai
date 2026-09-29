"""M1 Hindsight integration spike.

Runs real calls against a running Hindsight server through the app's
integration module (app/services/hindsight_memory.py) and reports every check
as PASSED, FAILED, SKIPPED or UNVERIFIED. A skipped check is not a pass.

All data is synthetic and uses unique per-run identifiers. By default the spike
writes to a dedicated bank ("<HINDSIGHT_BANK_ID>-spike") so the demo bank stays
clean; pass --cleanup to delete that bank afterwards.

Usage (from backend/):
    uv run python scripts/hindsight_spike.py [--cleanup] [--latency-samples N]
        [--observation-wait SECONDS] [--report PATH]

Exit code: 0 if nothing FAILED, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.services.hindsight_memory import (  # noqa: E402
    HindsightMemory,
    HindsightMemoryError,
    MemoryRecord,
    MemoryUnavailableError,
    _redact,
    customer_tag,
    deal_tag,
    from_settings,
    kind_tag,
)

PASSED, FAILED, SKIPPED, UNVERIFIED = "PASSED", "FAILED", "SKIPPED", "UNVERIFIED"


@dataclass
class Check:
    id: str
    name: str
    status: str = SKIPPED
    detail: str = ""
    needs_llm_credentials: bool = False
    data: dict[str, Any] = field(default_factory=dict)


class Spike:
    def __init__(self, settings: Settings, args: argparse.Namespace) -> None:
        self.settings = settings
        self.args = args
        self.run_id = uuid.uuid4().hex[:8]
        suffix = "-spike-chunks" if args.extraction_mode == "chunks" else "-spike"
        self.bank_id = args.bank or f"{settings.hindsight_bank_id}{suffix}"
        self.checks: dict[str, Check] = {}
        self.latencies: dict[str, list[float]] = {"retain": [], "recall": [], "reflect": []}
        self.secrets = [
            s.get_secret_value()
            for s in (settings.anthropic_api_key, settings.hindsight_api_key, settings.hindsight_self_hosted_api_key)
            if s is not None
        ]
        self.cust_a = f"spike-a-{self.run_id}"
        self.cust_b = f"spike-b-{self.run_id}"
        self.doc_a = f"interaction:{self.cust_a}:1"
        self.doc_b = f"interaction:{self.cust_b}:1"

    # -- helpers -------------------------------------------------------------

    def check(self, cid: str, name: str, needs_llm: bool = False) -> Check:
        c = Check(cid, name, needs_llm_credentials=needs_llm)
        self.checks[cid] = c
        return c

    def safe(self, value: Any) -> Any:
        return json.loads(_redact(json.dumps(value, default=str), self.secrets))

    def owner(self, rec: MemoryRecord) -> str:
        """Which synthetic customer a memory belongs to, from tags or document id."""
        if customer_tag(self.cust_a) in rec.tags or (rec.document_id or "").startswith(
            f"interaction:{self.cust_a}"
        ):
            return "A"
        if customer_tag(self.cust_b) in rec.tags or (rec.document_id or "").startswith(
            f"interaction:{self.cust_b}"
        ):
            return "B"
        return "unattributed"

    def sample(self, recs: list[MemoryRecord], n: int = 3) -> list[dict[str, Any]]:
        return [self.safe(asdict(r)) for r in recs[:n]]

    # -- checks --------------------------------------------------------------

    async def run(self) -> None:
        self.check_invalid_config()
        await self.check_unreachable()
        mem = HindsightMemory(
            self.settings.hindsight_effective_base_url,
            self.bank_id,
            api_key=self.settings.hindsight_client_key(),
            timeout_seconds=self.settings.hindsight_timeout_seconds,
        )
        try:
            if not await self.check_health(mem):
                self.skip_rest("Hindsight service is not reachable/healthy")
                return
            if not await self.check_bank(mem):
                self.skip_rest("bank could not be created")
                return
            retained = await self.check_retains(mem)
            if not retained:
                self.skip_rest("retain did not succeed (see C03/C10c)", start="C04")
                return
            if self.args.observation_wait > 0:
                await asyncio.sleep(self.args.observation_wait)
            await self.check_recall(mem)
            await self.check_isolation(mem)
            await self.check_reflect(mem)
            await self.check_list_and_history(mem)
            await self.check_latency_samples(mem)
        finally:
            if self.args.cleanup:
                try:
                    await mem.delete_bank()
                    self.cleanup_note = f"deleted bank {self.bank_id}"
                except HindsightMemoryError as exc:
                    self.cleanup_note = f"cleanup failed: {exc}"
            await mem.aclose()

    def skip_rest(self, reason: str, start: str = "C02") -> None:
        for cid, name, needs_llm in PLANNED:
            if cid >= start and cid not in self.checks:
                c = self.check(cid, name, needs_llm)
                c.status, c.detail = SKIPPED, f"skipped: {reason}"

    def check_invalid_config(self) -> None:
        c = self.check("C10a", "Invalid configuration is rejected before any call")
        problems = []
        for bad in ({"hindsight_base_url": "localhost:8888"}, {"hindsight_bank_id": "bad bank/id"}):
            try:
                Settings(_env_file=None, **bad)
                problems.append(f"accepted {bad}")
            except ValidationError:
                pass
        c.status = FAILED if problems else PASSED
        c.detail = "; ".join(problems) or "bad URL and bad bank id raise ValidationError"

    async def check_unreachable(self) -> None:
        c = self.check("C10b", "Unavailable service yields a safe, typed error")
        fake_key = "sk-ant-spike-fake-key-000"
        mem = HindsightMemory("http://127.0.0.1:9", "unreachable", api_key=fake_key, timeout_seconds=5)
        try:
            health = await mem.health()
            try:
                await mem.recall("x", tags=[customer_tag("x")])
                c.status, c.detail = FAILED, "recall against a closed port did not raise"
            except MemoryUnavailableError as exc:
                leaked = fake_key in str(exc)
                ok = not health.reachable and not leaked
                c.status = PASSED if ok else FAILED
                c.detail = f"health.reachable={health.reachable}; error='{exc}'; key leaked={leaked}"
        finally:
            await mem.aclose()

    async def check_health(self, mem: HindsightMemory) -> bool:
        c = self.check("C01", "Hindsight health and connectivity")
        started = time.perf_counter()
        status = await mem.health()
        c.data = {
            "reachable": status.reachable,
            "healthy": status.healthy,
            "api_version": status.api_version,
            "health_payload": self.safe(status.detail),
            "latency_s": round(time.perf_counter() - started, 3),
        }
        c.status = PASSED if status.reachable and status.healthy else FAILED
        c.detail = f"reachable={status.reachable} healthy={status.healthy} version={status.api_version}"
        return c.status == PASSED

    async def check_bank(self, mem: HindsightMemory) -> bool:
        c = self.check("C02", "Create or reuse the memory bank")
        try:
            await mem.ensure_bank(
                name="Deal Rescue AI spike (synthetic)",
                retain_mission=(
                    "Synthetic sales CRM notes. Keep customer requirements, budgets, "
                    "stakeholders, commitments and dates."
                ),
                reflect_mission="I am a sales assistant that answers only from recorded memories.",
                retain_extraction_mode=self.args.extraction_mode,
                # chunks mode exists to test without an LLM, so also turn off
                # observation consolidation (which calls the LLM in the background).
                enable_observations=False if self.args.extraction_mode == "chunks" else None,
            )
            mode = self.args.extraction_mode or "server default"
            c.status, c.detail = PASSED, f"bank '{self.bank_id}' created/updated (extraction mode: {mode})"
        except HindsightMemoryError as exc:
            c.status, c.detail = FAILED, str(exc)
        return c.status == PASSED

    async def _retain(self, mem: HindsightMemory, cust: str, doc: str, content: str, when: datetime):
        return await mem.retain(
            content,
            document_id=doc,
            tags=[customer_tag(cust), deal_tag(f"{cust}-deal"), kind_tag("interaction")],
            timestamp=when,
            context="sales call notes",
            metadata={"customer_id": cust, "interaction_id": doc, "source": "rep_note", "synthetic": "true"},
        )

    async def check_retains(self, mem: HindsightMemory) -> bool:
        now = datetime.now(UTC)
        content_a = (
            f"[SYNTHETIC DEMO DATA, run {self.run_id}] Discovery call with Aurora Logistics. "
            "CFO Priya Raman said the annual budget is capped at 42,000 USD and that a SOC 2 "
            "Type II report is mandatory before signing. They want a pilot in the Pune warehouse."
        )
        content_b = (
            f"[SYNTHETIC DEMO DATA, run {self.run_id}] Call with Borealis Foods. "
            "Head of IT Marcus Lee insisted on an on-premises deployment and mentioned a budget "
            "of 310,000 USD. Procurement needs a signed data processing agreement."
        )
        ok = True
        for cid, name, cust, doc, content in (
            ("C03", "Retain synthetic interaction for customer A", self.cust_a, self.doc_a, content_a),
            ("C04", "Retain synthetic interaction for customer B", self.cust_b, self.doc_b, content_b),
        ):
            c = self.check(cid, name, needs_llm=True)
            try:
                out = await self._retain(mem, cust, doc, content, now - timedelta(days=2))
                self.latencies["retain"].append(out.latency_seconds)
                c.data = self.safe(asdict(out))
                c.status = PASSED if out.success else FAILED
                c.detail = f"success={out.success} items={out.items_count} latency={out.latency_seconds:.2f}s"
            except HindsightMemoryError as exc:
                c.status, c.detail = FAILED, str(exc)
                ok = False
                if cid == "C03":
                    note = self.check("C10c", "Missing/invalid LLM credentials surface as a clean error")
                    creds = "missing" if self.settings.anthropic_api_key is None else "set"
                    message = str(exc)
                    is_auth = "401" in message or "authentication" in message.lower()
                    leaked = any(secret and secret in message for secret in self.secrets)
                    note.status = PASSED if is_auth and not leaked else UNVERIFIED
                    note.detail = (
                        f"retain raised {type(exc).__name__} (auth error={is_auth}, "
                        f"secret leaked={leaked}). ANTHROPIC_API_KEY in backend .env is {creds}."
                    )
                    break
            ok = ok and c.status == PASSED
        return ok

    async def _recall(self, mem: HindsightMemory, query: str, tags: list[str], match: str = "all_strict"):
        started = time.perf_counter()
        recs = await mem.recall(query, tags=tags, tags_match=match)
        self.latencies["recall"].append(time.perf_counter() - started)
        return recs

    async def check_recall(self, mem: HindsightMemory) -> None:
        c = self.check("C05", "Recall expected memory with customer tag filter", needs_llm=True)
        try:
            recs = await self._recall(mem, "What is Aurora Logistics' budget?", [customer_tag(self.cust_a)])
            owners = [self.owner(r) for r in recs]
            mentions_budget = any("42,000" in r.text or "42000" in r.text for r in recs)
            c.data = {"count": len(recs), "owners": owners, "sample": self.sample(recs)}
            if recs and all(o == "A" for o in owners) and mentions_budget:
                c.status, c.detail = PASSED, f"{len(recs)} results, all customer A, budget fact present"
            elif recs and all(o == "A" for o in owners):
                c.status = UNVERIFIED
                c.detail = f"{len(recs)} customer-A results but no result text contains the 42,000 budget"
            else:
                c.status, c.detail = FAILED, f"results={len(recs)} owners={owners}"
        except HindsightMemoryError as exc:
            c.status, c.detail = FAILED, str(exc)

    async def check_isolation(self, mem: HindsightMemory) -> None:
        c = self.check("C06", "Customer-scoped recall never returns the other customer", needs_llm=True)
        # Adversarial queries: ask each scope about the *other* customer's facts.
        probes = [
            ("A-scope asks about B", [customer_tag(self.cust_a)], "B",
             "Borealis Foods on-premises deployment Marcus Lee 310,000 budget"),
            ("B-scope asks about A", [customer_tag(self.cust_b)], "A",
             "Aurora Logistics SOC 2 Priya Raman 42,000 budget Pune pilot"),
            ("A-scope own query", [customer_tag(self.cust_a)], "B", "What does the customer require?"),
            ("B-scope own query", [customer_tag(self.cust_b)], "A", "What does the customer require?"),
        ]
        results, leaks, unattributed = [], 0, 0
        try:
            for label, tags, forbidden, query in probes:
                recs = await self._recall(mem, query, tags)
                owners = [self.owner(r) for r in recs]
                leak = owners.count(forbidden)
                leaks += leak
                unattributed += owners.count("unattributed")
                results.append({"probe": label, "count": len(recs), "owners": owners,
                                "types": [r.type for r in recs]})
            # Negative control: without a tag filter both customers should be visible,
            # proving the filter (not the query) is what isolates.
            control = await self._recall(
                mem, "Aurora Logistics and Borealis Foods budgets", [], match="any"
            )
            control_owners = {self.owner(r) for r in control}
            results.append({"probe": "unscoped control", "count": len(control),
                            "owners": sorted(control_owners)})
            c.data = {"probes": results}
            if leaks == 0 and unattributed == 0 and {"A", "B"} <= control_owners:
                c.status = PASSED
                c.detail = "0 cross-customer results in 4 scoped probes; unscoped control sees both"
            elif leaks == 0 and unattributed == 0:
                c.status = UNVERIFIED
                c.detail = f"no leaks, but unscoped control saw only {sorted(control_owners)}"
            else:
                c.status = FAILED
                c.detail = f"cross-customer results={leaks}, unattributed results={unattributed}"
        except HindsightMemoryError as exc:
            c.status, c.detail = FAILED, str(exc)

    async def check_reflect(self, mem: HindsightMemory) -> None:
        c = self.check("C07", "Reflect with include_facts returns evidence IDs", needs_llm=True)
        try:
            started = time.perf_counter()
            out = await mem.reflect(
                "What budget constraint and compliance requirement has this customer stated?",
                tags=[customer_tag(self.cust_a)],
            )
            self.latencies["reflect"].append(time.perf_counter() - started)
            owners = [self.owner(e) for e in out.evidence]
            c.data = {
                "text_excerpt": _redact(out.text[:400], self.secrets),
                "evidence_count": len(out.evidence),
                "evidence_ids": [e.id for e in out.evidence],
                "evidence_owners": owners,
                "evidence_sample": self.sample(out.evidence),
                "mental_model_ids": out.mental_model_ids,
                "directive_ids": out.directive_ids,
                "usage": out.usage,
            }
            if out.evidence and "B" not in owners:
                c.status = PASSED
                c.detail = f"{len(out.evidence)} evidence facts with ids; none from customer B"
            elif not out.evidence:
                c.status, c.detail = FAILED, "based_on.memories was empty"
            else:
                c.status, c.detail = FAILED, f"evidence included customer B facts: {owners}"
        except HindsightMemoryError as exc:
            c.status, c.detail = FAILED, str(exc)

    async def check_list_and_history(self, mem: HindsightMemory) -> None:
        c = self.check("C08a", "List memories by document and by tag")
        try:
            by_doc = await mem.list_memories(document_id=self.doc_a)
            by_tag = await mem.list_memories(tags=[customer_tag(self.cust_b)], tags_match="all_strict")
            observations = await mem.list_memories(memory_type="observation")
            doc_ok = bool(by_doc) and all(r.document_id == self.doc_a for r in by_doc)
            tag_ok = bool(by_tag) and all(self.owner(r) == "B" for r in by_tag)
            c.data = {
                "by_document_count": len(by_doc),
                "by_document_sample": self.sample(by_doc, 2),
                "by_tag_count": len(by_tag),
                "by_tag_owners": [self.owner(r) for r in by_tag],
                "observation_count": len(observations),
                "observation_sample": self.sample(observations, 2),
            }
            c.status = PASSED if doc_ok and tag_ok else FAILED
            c.detail = f"document filter ok={doc_ok}, tag filter ok={tag_ok}, observations={len(observations)}"
        except HindsightMemoryError as exc:
            c.status, c.detail = FAILED, str(exc)
            return

        h = self.check("C08b", "Observation history endpoint")
        if not observations:
            h.status = UNVERIFIED
            h.detail = (
                "no observations existed yet (consolidation is asynchronous); "
                "endpoint exists in the client but was not exercised"
            )
            return
        try:
            history = await mem.observation_history(observations[0].id)
            h.data = {"memory_id": observations[0].id, "history": self.safe(history)}
            h.status = PASSED if history is not None else UNVERIFIED
            h.detail = "history returned" if history is not None else "404 for this observation"
        except HindsightMemoryError as exc:
            h.status, h.detail = FAILED, str(exc)

    async def check_latency_samples(self, mem: HindsightMemory) -> None:
        c = self.check("C09", "Retain latency measurement", needs_llm=True)
        for i in range(self.args.latency_samples):
            try:
                out = await self._retain(
                    mem, self.cust_a, f"interaction:{self.cust_a}:lat{i}",
                    f"[SYNTHETIC DEMO DATA, run {self.run_id}] Follow-up email {i}: Aurora Logistics "
                    f"asked for a revised quote by day {i + 3} and confirmed Priya Raman is the signer.",
                    datetime.now(UTC),
                )
                self.latencies["retain"].append(out.latency_seconds)
            except HindsightMemoryError as exc:
                c.status, c.detail = FAILED, str(exc)
                return
        vals = sorted(self.latencies["retain"])
        if not vals:
            c.status, c.detail = SKIPPED, "no successful retains"
            return
        c.data = {k: [round(v, 2) for v in vs] for k, vs in self.latencies.items()}
        c.status = PASSED
        c.detail = (
            f"retain n={len(vals)} min={vals[0]:.2f}s median={vals[len(vals) // 2]:.2f}s "
            f"max={vals[-1]:.2f}s ("
            + (
                "chunks mode: NO LLM extraction, not representative of production)"
                if self.args.extraction_mode == "chunks"
                else "synchronous retain, includes LLM extraction)"
            )
        )


PLANNED = [
    ("C01", "Hindsight health and connectivity", False),
    ("C02", "Create or reuse the memory bank", False),
    ("C03", "Retain synthetic interaction for customer A", True),
    ("C04", "Retain synthetic interaction for customer B", True),
    ("C05", "Recall expected memory with customer tag filter", True),
    ("C06", "Customer-scoped recall never returns the other customer", True),
    ("C07", "Reflect with include_facts returns evidence IDs", True),
    ("C08a", "List memories by document and by tag", True),
    ("C08b", "Observation history endpoint", True),
    ("C09", "Retain latency measurement", True),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bank", help="bank id to use (default: <HINDSIGHT_BANK_ID>-spike)")
    p.add_argument("--cleanup", action="store_true", help="delete the spike bank afterwards")
    p.add_argument("--latency-samples", type=int, default=3, help="extra retains for latency (default 3)")
    p.add_argument("--observation-wait", type=float, default=10.0,
                   help="seconds to wait after retain so observations can consolidate (default 10)")
    p.add_argument("--report", type=Path, help="write a JSON report to this path")
    p.add_argument(
        "--extraction-mode",
        choices=["chunks", "concise", "verbose", "verbatim"],
        help="bank retain_extraction_mode. 'chunks' skips the LLM, so tag isolation, recall and "
        "listing can be tested without credentials (default: server default, LLM extraction)",
    )
    return p.parse_args(argv)


async def run_spike(argv: list[str] | None = None) -> tuple[Spike, int]:
    args = parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
        print(f"Invalid configuration in environment/.env: {fields}", file=sys.stderr)
        raise SystemExit(2) from None
    spike = Spike(settings, args)
    spike.cleanup_note = "bank kept (pass --cleanup to delete)"
    await spike.run()

    order = [cid for cid, _, _ in PLANNED] + ["C10a", "C10b", "C10c"]
    checks = sorted(spike.checks.values(), key=lambda c: order.index(c.id) if c.id in order else 99)
    print(f"\nHindsight spike run {spike.run_id} against {settings.hindsight_effective_base_url} "
          f"(bank '{spike.bank_id}')")
    print(f"Config: {settings.describe()}")
    print(f"Extraction mode: {args.extraction_mode or 'server default (LLM extraction)'}\n")
    for c in checks:
        if args.extraction_mode == "chunks" and c.id not in {"C07", "C08b"}:
            creds = " [chunks mode: no LLM used]" if c.needs_llm_credentials else ""
        else:
            creds = " [needs LLM credentials]" if c.needs_llm_credentials else ""
        print(f"{c.status:<10} {c.id:<5} {c.name}{creds}\n           {c.detail}")
    counts = {s: sum(c.status == s for c in checks) for s in (PASSED, FAILED, SKIPPED, UNVERIFIED)}
    print(f"\nSummary: {counts}. {spike.cleanup_note}.")

    if args.report:
        report = {
            "run_id": spike.run_id,
            "ran_at": datetime.now(UTC).isoformat(),
            "hindsight_base_url": settings.hindsight_effective_base_url,
            "bank_id": spike.bank_id,
            "config": settings.describe(),
            "extraction_mode": args.extraction_mode or "server default",
            "summary": counts,
            "checks": [asdict(c) for c in checks],
            "cleanup": spike.cleanup_note,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(_redact(json.dumps(report, indent=2, default=str), spike.secrets))
        print(f"Report written to {args.report}")
    return spike, (1 if counts[FAILED] else 0)


def main() -> None:
    _, code = asyncio.run(run_spike())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
