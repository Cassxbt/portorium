"""Precedent Gate: no code without a living precedent.

A model proposes; Zetaris and a second model verify; code decides.
  1 retrieve   Nemotron Embed + Rerank over CBP rulings issued before `as_of`
  2 living     Zetaris, per request: ruling not revoked; code_in_schedule / parent_survives (HTS 2026 Rev 21)
  3 judge      Nemotron Ultra via Nightshift: is each precedent the same article; one question if they differ
  3 propose    Nemotron Ultra via Nightshift, in parallel: one code from the retrieved rulings, with a confidence
  4 decide     the proposed code must be cited by a standing ruling and be living in Rev 21
  5 critique   Nemotron Ultra checks the product against that code's Rev 21 text, every level and its
               alternatives, read from Zetaris: a code can survive a revision while what it covers moves

Broker decisions recalled from memory (Meterless H-MEM) are judged and offered to the Proposer like
rulings, so a remembered decision counts only for a product the model finds to be the same.
Anything the gate cannot settle goes to REVIEW with one question, or to a broker.
"""
import contextvars
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from . import trace
from .index import Precedent, PrecedentIndex
from .liveness import FAILED_RULE, LIVING, REVOKED, SPLIT, Liveness, precedent_rows, schedule_text
from .memory import BrokerMemory
from .nightshift import ULTRA, Answer, Nightshift
from .nvidia import Nvidia
from .rulings import RulingTexts
from .zetaris import Zetaris

CLASSIFIED, REVIEW, REFUSE = "CLASSIFIED", "REVIEW", "REFUSE"
MATCHES = ("full", "partial", "none")
RULING, MEMORY = "ruling", "memory"
HS2022 = "2022-01-01"

SYSTEM = """You are a US customs classification analyst. Compare an importer's product with prior decisions:
CBP rulings, and decisions a licensed broker signed off earlier (BROKER entries).
For each one decide whether it describes the same article, so that its tariff number applies unchanged:
  "full"    the same article: same identity (for chemicals and medicines the same compound or active
            ingredient; for machines and electronics the same kind of device with the same functions;
            for garments the same garment type, construction, predominant fibre and gender), and every fact
            the precedent's classification depends on is stated for the product and agrees
  "partial" the same kind of product, but its identity differs, or a fact the classification depends on
            differs or is not stated for the product
  "none"    a different kind of product
A different product of the same type or category is "partial", even when it would share the heading.
If you note any tariff-relevant difference or unknown, the match is not "full".
An entry without FACTS can be at most "partial".
If an entry cites several codes, give the code that applies to this product, else "none".
Decide only from the facts given. Do not use your own knowledge of tariff numbers.
If entries with "full" or "partial" disagree, write the single question to the importer whose answer
would separate them; otherwise "".
Reply with only a JSON object:
{"precedents": [{"ruling": "<id as given>", "match": "full|partial|none", "code": "<one of its codes or none>",
"fact": "<for full, the shared identity; otherwise the decisive difference or unknown, short>"}], "question": "<question or empty>"}"""


PROPOSE = ("You are a US customs classification analyst. Choose the code for the product from the prior CBP rulings "
           "listed, and state your confidence from 0 to 100 that it is correct at 10 digits. "
           'Reply with only a JSON object: {"code": "<one listed code, 10 digits>", "confidence": <0-100>}')

CRITIC = """You check a proposed US tariff classification against the words of the schedule, HTS 2026 Revision 21.
You get the importer's product, any answers the importer gave, and the proposed 10-digit line with every level
above it. Under each level you also get the alternatives: the other lines at that level. A line reading "Other"
covers only goods that none of its alternatives at that level cover.
Decide whether the product falls within every level of the proposed path rather than within an alternative.
Judge only from the product facts and the schedule text given. If placing the product needs a fact that is not
stated, and an alternative would apply depending on it, the product does not fit: ask for that fact.
Reply with only a JSON object:
{"fits": true or false, "line": "<the first level that does not fit, or empty>", "reason": "<short>",
"question": "<when a missing fact decides it, the one question to the importer; otherwise empty>"}"""


@dataclass
class JudgedPrecedent:
    ruling_number: str
    ruling_date: str
    subject: str
    codes: list[str]
    match: str
    code: str | None
    fact: str
    status: str | None = None
    failed_rule: str | None = None
    pre_hs2022: bool = False
    kind: str = RULING
    unresolved: bool = False
    successors: list[str] = field(default_factory=list)


@dataclass
class Proposal:
    code: str | None
    confidence: float
    answer: Answer

    def to_dict(self) -> dict:
        return {"code": self.code, "confidence": self.confidence, "routed_model": self.answer.routed_model,
                "receipt_id": self.answer.receipt.get("receipt_id")}


@dataclass
class Verdict:
    decision: str
    reason: str
    code: str | None
    precedents: list[JudgedPrecedent]
    question: str = ""
    receipts: list[dict] = field(default_factory=list)
    dq: dict = field(default_factory=dict)
    remembered: list[dict] = field(default_factory=list)
    critique: dict = field(default_factory=dict)
    proposal: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_code(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _key(value) -> str:
    return re.sub(r"[^A-Z0-9:]", "", str(value or "").upper())


class PrecedentGate:
    def __init__(self, index: PrecedentIndex, nvidia: Nvidia, nightshift: Nightshift, liveness: Liveness,
                 texts: RulingTexts | None = None, zetaris: Zetaris | None = None, model: str = ULTRA, top: int = 6,
                 critic: bool = True):
        """critic: a classification must also fit the code's current schedule text (needs Zetaris)."""
        self.index, self.nvidia, self.nightshift, self.liveness = index, nvidia, nightshift, liveness
        self.texts, self.zetaris, self.model, self.top, self.critic = texts, zetaris, model, top, critic

    def classify(self, product: str, as_of: str, answers: list[tuple[str, str]] = (),
                 exclude: set[str] = frozenset(), memory: BrokerMemory | None = None) -> Verdict:
        with trace.role("Retriever"):
            precedents = self.index.search(self.nvidia, product, as_of, exclude=exclude)[: self.top]
            facts = {p.ruling_number: f for p in precedents if self.texts and (f := self.texts.facts(p.ruling_number))}
            trace.event("retrieve", product, f"{len(precedents)} rulings issued before {as_of}, {len(facts)} with product facts: "
                        + ", ".join(p.ruling_number for p in precedents), target_agent="Adjudicator")
        remembered = []
        if memory:
            with trace.role("Memory"):
                stale = []
                for recall in memory.recall(product):
                    if self.liveness.status(recall.code) != LIVING:
                        stale.append(recall.memory_id)
                        continue
                    entry = Precedent(f"MEM:{recall.memory_id}", "", f"broker {recall.broker}", [recall.code], [])
                    precedents.append(entry)
                    facts[entry.ruling_number] = recall.content
                remembered = memory.recall_answers(product)
                broker = [p.ruling_number for p in precedents if p.ruling_number.startswith("MEM:")]
                trace.event("recall", product, f"{len(broker)} broker decision(s) passed on, {len(stale)} dropped as no longer "
                            f"living, {len(remembered)} earlier importer answer(s)", target_agent="Adjudicator")
        if not precedents:
            with trace.role("Gate"):
                trace.event("decide", product, "REFUSE NO_PRECEDENT_FOUND", status="refused", rule="NO_PRECEDENT_FOUND")
            return Verdict(REFUSE, "NO_PRECEDENT_FOUND", None, [], dq=self.liveness.counts)
        rows = {}
        if self.zetaris:
            with trace.role("Auditor"):
                rows = precedent_rows(self.zetaris, [p.ruling_number for p in precedents if not p.ruling_number.startswith("MEM:")])
                trace.event("check_liveness", ", ".join(rows), "; ".join(
                    f"{n}{' REVOKED' if r['revoked'] else ''} " + ", ".join(f"{c} {st}" for c, st in r["codes"].items())
                    for n, r in rows.items()), target_agent="Adjudicator")
        # The Adjudicator and the Proposer are independent calls; each thread carries this request's trace.
        with ThreadPoolExecutor(2) as pool:
            judging = pool.submit(contextvars.copy_context().run, self._judge_traced, product, precedents, answers, facts,
                                  rows, remembered)
            proposing = pool.submit(contextvars.copy_context().run, self._propose_traced, product, precedents, answers, facts,
                                    remembered)
            judged, question, answer = judging.result()
            proposal = proposing.result()
        with trace.role("Gate"):
            verdict = self.decide(judged, question, answer, proposal, self._statuses(proposal.code, judged, rows))
            verdict.remembered = [{"question": q, "answer": a, "memory_id": m} for q, a, m in remembered]
            trace.event("decide", f"proposed {proposal.code or '-'}; " + "; ".join(
                f"{j.ruling_number} {j.match} {j.code or '-'} {j.status or ''}".strip() for j in judged),
                        f"{verdict.decision} {verdict.reason} {verdict.code or ''}".strip(),
                        target_agent="Critic" if verdict.decision == CLASSIFIED and self.critic and self.zetaris
                        else "Importer" if verdict.question else "Broker" if verdict.decision == REVIEW else None,
                        status={CLASSIFIED: "success", REVIEW: "needs_review", REFUSE: "refused"}[verdict.decision],
                        rule=verdict.reason)
        if verdict.decision == CLASSIFIED and self.critic and self.zetaris:
            verdict = self.critique(product, answers, verdict)
        return verdict

    def critique(self, product: str, answers, verdict: Verdict) -> Verdict:
        """Check a classification against its code's Rev 21 text; a mismatch becomes REVIEW."""
        code = verdict.code
        with trace.role("Auditor"):
            rows = schedule_text(self.zetaris, code)
            path = [r for r in rows if r["kind"] == "path"]
            trace.event("schedule_text", code, " > ".join(r["description"] for r in path),
                        target_agent="Critic", alternatives=sum(r["kind"] == "alternative" for r in rows))
        lines = [f"PRODUCT: {product}"] + [f"IMPORTER ANSWERED: {q} -> {a}" for q, a in answers]
        lines.append(f"PROPOSED: {code[:4]}.{code[4:6]}.{code[6:8]}.{code[8:]}")
        for r in path:
            lines.append(f"LEVEL {r['lvl']}: {r['htsno'] or ''} {r['description']}".replace("  ", " "))
            alternatives = [a for a in rows if a["kind"] == "alternative" and a["lvl"] == r["lvl"]]
            lines += [f"  alternative: {a['htsno'] or ''} {a['description'][:160]}".replace("  ", " ", 1) for a in alternatives[:60]]
            if len(alternatives) > 60:
                lines.append(f"  ({len(alternatives) - 60} more alternatives at this level not shown)")
        with trace.role("Critic"):
            messages = [{"role": "system", "content": CRITIC}, {"role": "user", "content": "\n".join(lines)}]
            data, answer = self.nightshift.chat_json(messages, {"fits": (True, False), "reason": None, "question": None},
                                                     model=self.model)
            fits = data["fits"] is True
            trace.event("critique", f"{code} for {product}", ("fits" if fits else f"does not fit at {data.get('line') or '?'}")
                        + f": {data.get('reason') or ''}", target_agent="Gate", model=answer.routed_model or self.model,
                        status="success" if fits else "needs_revision", receipt_id=answer.receipt.get("receipt_id"))
        critique = {"code": code, "fits": fits, "line": str(data.get("line") or ""), "reason": str(data.get("reason") or ""),
                    "receipt_id": answer.receipt.get("receipt_id"), "routed_model": answer.routed_model}
        receipts = verdict.receipts + [{"role": "Critic", "routed_model": answer.routed_model,
                                        "receipt_id": answer.receipt.get("receipt_id"), "from_cache": answer.receipt.get("from_cache")}]
        if fits:
            verdict.critique, verdict.receipts = critique, receipts
            return verdict
        with trace.role("Gate"):
            revised = Verdict(REVIEW, "SCHEDULE_TEXT_DISAGREES", None, verdict.precedents, str(data.get("question") or ""),
                              receipts, verdict.dq, verdict.remembered, critique, verdict.proposal)
            trace.event("decide", f"{code} does not fit its Rev 21 text", "REVIEW SCHEDULE_TEXT_DISAGREES",
                        target_agent="Importer" if revised.question else "Broker", status="needs_review", rule=revised.reason)
        return revised

    def _judge_traced(self, product, precedents, answers, facts, rows, remembered):
        with trace.role("Adjudicator"):
            judged, question, answer = self.judge(product, precedents, answers, facts, rows, remembered)
            trace.event("judge", f"{product} vs {len(precedents)} precedents, {len(answers)} importer answer(s)",
                        "; ".join(f"{j.ruling_number} {j.match} {j.code or '-'}" for j in judged)
                        + (f"; question: {question}" if question else ""),
                        target_agent="Gate", model=answer.routed_model or self.model, receipt_id=answer.receipt.get("receipt_id"))
        return judged, question, answer

    def _propose_traced(self, product, precedents, answers, facts, remembered) -> Proposal:
        with trace.role("Proposer"):
            proposal = self.propose(product, precedents, answers, facts, remembered)
            trace.event("propose", f"{product} from {len(precedents)} precedents",
                        f"{proposal.code or 'no code'} at confidence {proposal.confidence:g}", target_agent="Gate",
                        model=proposal.answer.routed_model or self.model, receipt_id=proposal.answer.receipt.get("receipt_id"))
        return proposal

    def propose(self, product: str, precedents: list[Precedent], answers, facts: dict[str, str], remembered=()) -> Proposal:
        """One code from the listed rulings; without answers or memory the request is the evaluated one."""
        names = _broker_names(precedents)
        listing = "\n".join(f"{names.get(p.ruling_number, p.ruling_number)}: {p.subject}. CODES: {', '.join(p.codes)}."
                            + (f" FACTS: {facts[p.ruling_number]}" if p.ruling_number in facts else "") for p in precedents)
        told = "".join(f"\nIMPORTER ANSWERED: {q} -> {a}" for q, a in answers) + \
            "".join(f"\nIMPORTER ANSWERED EARLIER for a similar product (use only if it applies): {q} -> {a}" for q, a, _ in remembered)
        data, answer = self.nightshift.chat_json(
            [{"role": "system", "content": PROPOSE}, {"role": "user", "content": f"PRODUCT: {product}{told}\nPRIOR RULINGS:\n{listing}"}],
            {"code": None, "confidence": None}, model=self.model)
        try:
            confidence = float(data.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return Proposal(normalize_code(data["code"])[:10] or None, confidence, answer)

    def _statuses(self, code: str | None, judged: list[JudgedPrecedent], rows: dict[str, dict]) -> dict[str, str]:
        """Status of the proposed code for each precedent that cites it, per Zetaris for this request."""
        out = {}
        for j in judged:
            # A ruling counts only if judged the same or a similar article; a remembered broker decision only if the same.
            if not code or code not in j.codes or j.match not in (("full",) if j.kind == MEMORY else ("full", "partial")):
                continue
            row = rows.get(j.ruling_number)
            if j.kind == RULING and ((row and row["revoked"]) or j.status == REVOKED):
                out[j.ruling_number] = REVOKED
            else:
                out[j.ruling_number] = (row or {}).get("codes", {}).get(code) or self.liveness.status(code)
        return out

    def judge(self, product: str, precedents: list[Precedent], answers, facts: dict[str, str],
              rows: dict[str, dict] | None = None, remembered=()) -> tuple[list[JudgedPrecedent], str, Answer]:
        lines = [f"PRODUCT: {product}"]
        lines += [f"IMPORTER ANSWERED: {q} -> {a}" for q, a in answers]
        lines += [f"IMPORTER ANSWERED EARLIER for a similar product (use only if it applies): {q} -> {a}"
                  for q, a, _ in remembered]
        names = _broker_names(precedents)
        for p in precedents:
            codes = ", ".join(f"{c[:4]}.{c[4:6]}.{c[6:]}" for c in p.codes)
            detail = f" FACTS: {facts[p.ruling_number]}" if p.ruling_number in facts else ""
            entry = f"BROKER {names[p.ruling_number]}" if p.ruling_number in names else f"PRECEDENT {p.ruling_number}"
            lines.append(f"{entry} ({p.ruling_date or 'broker sign-off'}): {p.subject}. CODES: {codes}.{detail}")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]
        data, answer = self.nightshift.chat_json(messages, {"precedents": None, "question": None}, model=self.model)
        judged_rows = [row for row in data["precedents"] if isinstance(row, dict)]
        zetaris_rows = rows or {}
        return [self._judged(p, self._row_for(names.get(p.ruling_number, p.ruling_number), judged_rows),
                             p.ruling_number in facts, zetaris_rows.get(p.ruling_number))
                for p in precedents], str(data.get("question") or ""), answer

    @staticmethod
    def _row_for(name: str, rows: list[dict]) -> dict:
        """The model may write "NY N351214" for N351214 or "HQ 086616" for 086616."""
        target = _key(name)
        for row in rows:
            key = _key(row.get("ruling"))
            if key and (key == target or key.endswith(target)):
                return row
        return {}

    def _judged(self, p: Precedent, row: dict, has_facts: bool, zetaris_row: dict | None = None) -> JudgedPrecedent:
        match = row.get("match") if row.get("match") in MATCHES else "none"
        if match == "full" and not has_facts:
            match = "partial"
        code = normalize_code(row.get("code"))
        unresolved = False
        if code not in p.codes:
            if match != "none" and len(p.codes) == 1:
                code = p.codes[0]
            else:
                unresolved = match != "none"
                code = None
        status = failed = None
        if code:
            status = self.liveness.status(code)
            if zetaris_row is not None:
                status = REVOKED if zetaris_row["revoked"] else zetaris_row["codes"].get(code, status)
            failed = FAILED_RULE[status]
        kind = MEMORY if p.ruling_number.startswith("MEM:") else RULING
        return JudgedPrecedent(
            p.ruling_number, p.ruling_date, p.subject, p.codes, match, code,
            str(row.get("fact") or ("not judged" if not row else "")), status, failed,
            bool(p.ruling_date) and p.ruling_date < HS2022, kind, unresolved,
            self.liveness.successors(code) if status == SPLIT else [])

    def decide(self, judged: list[JudgedPrecedent], question: str, answer: Answer, proposal: Proposal,
               statuses: dict[str, str]) -> Verdict:
        """The rules, in order. statuses: the proposed code's status for each precedent citing it."""
        receipts = [{"role": role, "routed_model": a.routed_model, "receipt_id": a.receipt.get("receipt_id"),
                     "from_cache": a.receipt.get("from_cache")} for role, a in (("Adjudicator", answer), ("Proposer", proposal.answer))]

        def verdict(decision, reason, code=None, ask=""):
            shown = proposal.to_dict()
            if proposal.code:
                shown["status"] = self.liveness.status(proposal.code)
                shown["successors"] = self.liveness.successors(proposal.code) if shown["status"] == SPLIT else []
            return Verdict(decision, reason, code, judged, ask, receipts, self.liveness.counts, proposal=shown)

        citing = [j for j in judged if j.ruling_number in statuses]
        if not citing:
            return verdict(REVIEW, "NO_PRECEDENT_FOR_PROPOSAL", ask=question)
        living = [j for j in citing if statuses[j.ruling_number] == LIVING]
        if not living:
            if SPLIT in statuses.values():
                return verdict(REVIEW, "SPLIT_SUCCESSORS")
            if any(j.code and j.status == LIVING and j.match != "none" for j in judged):
                return verdict(REVIEW, "STALE_PROPOSAL", ask=question)
            return verdict(REFUSE, "STALE_PRECEDENT")
        if {j.code for j in judged if j.kind == MEMORY and j.match == "full" and j.code} - {proposal.code}:
            return verdict(REVIEW, "MEMORY_CONFLICT", ask=question)
        return verdict(CLASSIFIED, "BROKER_MEMORY" if all(j.kind == MEMORY for j in living) else "LIVING_PRECEDENT", proposal.code)


def _broker_names(precedents: list[Precedent]) -> dict[str, str]:
    """Memory ids grow with every write, so broker entries get per-request labels: the prompt for a
    given product and memory content stays the same after a wipe."""
    brokers = [p.ruling_number for p in precedents if p.ruling_number.startswith("MEM:")]
    return {number: f"BROKER-{i}" for i, number in enumerate(brokers, 1)}


def prompts_sha256() -> str:
    """One hash over every prompt that decides a verdict, recorded with each evaluation run."""
    return hashlib.sha256("\n".join((SYSTEM, PROPOSE, CRITIC)).encode()).hexdigest()[:16]
