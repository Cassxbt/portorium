"""Precedent Gate: no code without a living precedent.

Numbered checks, decided in code. The model only judges whether a precedent describes the same
product; it never chooses a code from memory.
  1 retrieve   Nemotron Embed + Rerank over CBP rulings issued before `as_of`
  2 living     Zetaris, per decision: ruling not revoked; code_in_schedule / parent_survives (HTS 2026 Rev 21)
  3 same       Nemotron Ultra via Nightshift: full | partial | none, on product facts only
  4 decide     CLASSIFIED only when every living full-match precedent agrees at 10 digits

Broker decisions recalled from memory (Meterless H-MEM) are judged in the same call as the
rulings, so a remembered decision counts only for a product the model finds to be the same.
Anything the gate cannot resolve goes to REVIEW.
"""
import re
from dataclasses import asdict, dataclass, field

from . import trace
from .index import Precedent, PrecedentIndex
from .liveness import FAILED_RULE, LIVING, REVOKED, SPLIT, Liveness, precedent_rows
from .memory import BrokerMemory
from .nightshift import ULTRA, Answer, Nightshift
from .nvidia import EMBED_MODEL, RERANK_MODEL, Nvidia
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
class Verdict:
    decision: str
    reason: str
    code: str | None
    precedents: list[JudgedPrecedent]
    question: str = ""
    receipts: list[dict] = field(default_factory=list)
    dq: dict = field(default_factory=dict)
    remembered: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_code(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _key(value) -> str:
    return re.sub(r"[^A-Z0-9:]", "", str(value or "").upper())


class PrecedentGate:
    def __init__(self, index: PrecedentIndex, nvidia: Nvidia, nightshift: Nightshift, liveness: Liveness,
                 texts: RulingTexts | None = None, zetaris: Zetaris | None = None, model: str = ULTRA, top: int = 6,
                 old_precedent_rule: bool = False):
        """old_precedent_rule: a lone precedent from before HS 2022 cannot classify on its own."""
        self.index, self.nvidia, self.nightshift, self.liveness = index, nvidia, nightshift, liveness
        self.texts, self.zetaris, self.model, self.top = texts, zetaris, model, top
        self.old_precedent_rule = old_precedent_rule

    def classify(self, product: str, as_of: str, answers: list[tuple[str, str]] = (),
                 exclude: set[str] = frozenset(), memory: BrokerMemory | None = None) -> Verdict:
        with trace.role("Retriever"):
            precedents = self.index.search(self.nvidia, product, as_of, exclude=exclude)[: self.top]
            facts = {p.ruling_number: f for p in precedents if self.texts and (f := self.texts.facts(p.ruling_number))}
            trace.event("retrieve", product, f"{len(precedents)} rulings issued before {as_of}, {len(facts)} with product facts: "
                        + ", ".join(p.ruling_number for p in precedents),
                        target_agent="Adjudicator", model=f"{EMBED_MODEL} + {RERANK_MODEL}")
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
        with trace.role("Adjudicator"):
            judged, question, answer = self.judge(product, precedents, answers, facts, rows, remembered)
            trace.event("judge", f"{product} vs {len(precedents)} precedents, {len(answers)} importer answer(s)",
                        "; ".join(f"{j.ruling_number} {j.match} {j.code or '-'}" for j in judged)
                        + (f"; question: {question}" if question else ""),
                        target_agent="Gate", model=answer.routed_model or self.model, receipt_id=answer.receipt.get("receipt_id"))
        with trace.role("Gate"):
            verdict = self.decide(judged, question, answer)
            verdict.remembered = [{"question": q, "answer": a, "memory_id": m} for q, a, m in remembered]
            trace.event("decide", "; ".join(f"{j.ruling_number} {j.match} {j.code or '-'} {j.status or ''} {j.failed_rule or ''}".strip()
                                            for j in judged),
                        f"{verdict.decision} {verdict.reason} {verdict.code or ''}".strip(),
                        target_agent="Importer" if verdict.question else ("Broker" if verdict.decision == REVIEW else None),
                        status={CLASSIFIED: "success", REVIEW: "needs_review", REFUSE: "refused"}[verdict.decision],
                        rule=verdict.reason)
        return verdict

    def judge(self, product: str, precedents: list[Precedent], answers, facts: dict[str, str],
              rows: dict[str, dict] | None = None, remembered=()) -> tuple[list[JudgedPrecedent], str, Answer]:
        lines = [f"PRODUCT: {product}"]
        lines += [f"IMPORTER ANSWERED: {q} -> {a}" for q, a in answers]
        lines += [f"IMPORTER ANSWERED EARLIER for a similar product (memory {m}; use only if it applies): {q} -> {a}"
                  for q, a, m in remembered]
        for p in precedents:
            codes = ", ".join(f"{c[:4]}.{c[4:6]}.{c[6:]}" for c in p.codes)
            label = "BROKER" if p.ruling_number.startswith("MEM:") else "PRECEDENT"
            detail = f" FACTS: {facts[p.ruling_number]}" if p.ruling_number in facts else ""
            lines.append(f"{label} {p.ruling_number} ({p.ruling_date or 'broker sign-off'}): {p.subject}. "
                         f"CODES: {codes}.{detail}")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]
        data, answer = self.nightshift.chat_json(messages, {"precedents": None, "question": None}, model=self.model)
        judged_rows = [row for row in data["precedents"] if isinstance(row, dict)]
        zetaris_rows = rows or {}
        return [self._judged(p, self._row_for(p, judged_rows), p.ruling_number in facts, zetaris_rows.get(p.ruling_number))
                for p in precedents], str(data.get("question") or ""), answer

    @staticmethod
    def _row_for(p: Precedent, rows: list[dict]) -> dict:
        """The model may write "NY N351214" for N351214 or "HQ 086616" for 086616."""
        target = _key(p.ruling_number)
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

    def decide(self, judged: list[JudgedPrecedent], question: str, answer: Answer) -> Verdict:
        receipt = {"routed_model": answer.routed_model, "receipt_id": answer.receipt.get("receipt_id"),
                   "from_cache": answer.receipt.get("from_cache")}
        rulings = [j for j in judged if j.kind == RULING]
        broker = [j for j in judged if j.kind == MEMORY and j.match == "full" and j.code]
        full = [j for j in rulings if j.match == "full" and j.code]
        living_full = [j for j in full if j.status == LIVING]
        split_full = [j for j in full if j.status == SPLIT]
        candidates = {j.code for j in rulings if j.code and j.match in ("full", "partial") and j.status == LIVING}

        def verdict(decision, reason, code=None, ask=""):
            return Verdict(decision, reason, code, judged, ask, [receipt], self.liveness.counts)

        if any(j.unresolved and j.match == "full" for j in judged):
            return verdict(REVIEW, "UNRESOLVED_MATCH", ask=question)
        broker_codes = {j.code for j in broker}
        if living_full:
            codes = {j.code for j in living_full}
            if len(codes) > 1:
                return verdict(REVIEW, "PRECEDENTS_DISAGREE", ask=question)
            code = codes.pop()
            if broker_codes - {code}:
                return verdict(REVIEW, "MEMORY_CONFLICT", ask=question)
            if self.old_precedent_rule and len(living_full) < 2 and all(j.pre_hs2022 for j in living_full):
                return verdict(REVIEW, "OLD_PRECEDENT_ONLY", ask=question)
            return verdict(CLASSIFIED, "LIVING_PRECEDENT_AGREES", code)
        if len(broker_codes) == 1 and next(iter(broker_codes)) in candidates:
            return verdict(CLASSIFIED, "BROKER_MEMORY", next(iter(broker_codes)))
        if split_full:
            return verdict(REVIEW, "SPLIT_SUCCESSORS")
        if candidates:
            return verdict(REVIEW, "ONLY_PARTIAL", ask=question)
        if full:
            return verdict(REFUSE, "STALE_PRECEDENT")
        return verdict(REFUSE, "NO_LIVING_PRECEDENT")
