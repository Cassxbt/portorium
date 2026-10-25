"""Precedent Gate: no code without a living precedent.

Numbered checks, decided in code. The model only judges whether a precedent describes the same
product; it never chooses a code from memory.
  1 retrieve   Nemotron Embed + Rerank over CBP rulings issued before `as_of`
  2 living     Zetaris DQ: code_in_schedule / parent_survives
  3 same       Nemotron Ultra via Nightshift: full | partial | none, on product facts only
  4 decide     CLASSIFIED only when every living full-match precedent agrees at 10 digits
"""
import json
from dataclasses import asdict, dataclass, field

from .index import Precedent, PrecedentIndex
from .liveness import LIVING, SPLIT, Liveness
from .memory import BrokerMemory
from .nightshift import ULTRA, Answer, Nightshift
from .nvidia import Nvidia
from .rulings import RulingTexts

CLASSIFIED, REVIEW, REFUSE = "CLASSIFIED", "REVIEW", "REFUSE"
MATCHES = ("full", "partial", "none")
HS2022 = "2022-01-01"

SYSTEM = """You are a US customs classification analyst. Compare an importer's product with prior CBP rulings.
For each precedent decide whether it describes the same product for tariff purposes:
  "full"    the tariff-relevant facts match: material and composition, construction (e.g. knitted vs woven),
            form, function and use, and gender or age where garments depend on it
  "partial" similar, but one tariff-relevant fact differs or is unknown from the product description
  "none"    a different product
A precedent without FACTS can be at most "partial".
If a precedent cites several codes, give the code that applies to this product, else "none".
Decide only from the facts given. Do not use your own knowledge of tariff numbers.
If precedents with "full" or "partial" disagree, write the single question to the importer whose answer
would separate them; otherwise "".
Reply with only a JSON object:
{"precedents": [{"ruling": "<number>", "match": "full|partial|none", "code": "<one of its codes or none>",
"fact": "<the decisive fact, short>"}], "question": "<question or empty>"}"""


@dataclass
class JudgedPrecedent:
    ruling_number: str
    ruling_date: str
    subject: str
    codes: list[str]
    match: str
    code: str | None
    fact: str
    status: str | None
    failed_rule: str | None
    pre_hs2022: bool


@dataclass
class Verdict:
    decision: str
    reason: str
    code: str | None
    precedents: list[JudgedPrecedent]
    question: str = ""
    receipts: list[dict] = field(default_factory=list)
    dq: dict = field(default_factory=dict)
    memory: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class PrecedentGate:
    def __init__(self, index: PrecedentIndex, nvidia: Nvidia, nightshift: Nightshift, liveness: Liveness,
                 texts: RulingTexts | None = None, model: str = ULTRA, top: int = 6):
        self.index, self.nvidia, self.nightshift, self.liveness = index, nvidia, nightshift, liveness
        self.texts, self.model, self.top = texts, model, top

    def classify(self, product: str, as_of: str, answers: list[tuple[str, str]] = (),
                 exclude: set[str] = frozenset(), memory: BrokerMemory | None = None) -> Verdict:
        precedents = self.index.search(self.nvidia, product, as_of, exclude=exclude)[: self.top]
        if not precedents:
            return Verdict(REFUSE, "NO_PRECEDENT_FOUND", None, [], dq=self.liveness.counts)
        facts = {p.ruling_number: f for p in precedents if self.texts and (f := self.texts.facts(p.ruling_number))}
        judged, question, answer = self._judge(product, precedents, answers, facts)
        verdict = self._decide(judged, question, answer)
        return apply_memory(verdict, product, memory, self.liveness) if memory else verdict

    def _judge(self, product: str, precedents: list[Precedent], answers,
               facts) -> tuple[list[JudgedPrecedent], str, Answer]:
        lines = [f"PRODUCT: {product}"]
        lines += [f"IMPORTER ANSWERED: {q} -> {a}" for q, a in answers]
        for p in precedents:
            codes = ", ".join(f"{c[:4]}.{c[4:6]}.{c[6:]}" for c in p.codes)
            detail = f" FACTS: {facts[p.ruling_number]}" if p.ruling_number in facts else ""
            lines.append(f"PRECEDENT {p.ruling_number} ({p.ruling_date}): {p.subject}. CODES: {codes}.{detail}")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]
        data, answer = self.nightshift.chat_json(messages, {"precedents": None, "question": None}, model=self.model)
        by_number = {row.get("ruling"): row for row in data["precedents"] if isinstance(row, dict)}
        judged = []
        for p in precedents:
            row = by_number.get(p.ruling_number, {})
            match = row.get("match") if row.get("match") in MATCHES else "none"
            if match == "full" and p.ruling_number not in facts:
                match = "partial"
            code = str(row.get("code") or "").replace(".", "")
            code = code if code in p.codes else (p.codes[0] if match != "none" and len(p.codes) == 1 else None)
            judged.append(JudgedPrecedent(
                p.ruling_number, p.ruling_date, p.subject, p.codes, match, code, str(row.get("fact") or ""),
                self.liveness.status(code) if code else None, self.liveness.rule_failed(code) if code else None,
                p.ruling_date < HS2022))
        return judged, str(data.get("question") or ""), answer

    def _decide(self, judged: list[JudgedPrecedent], question: str, answer: Answer) -> Verdict:
        receipt = {"routed_model": answer.routed_model, "receipt_id": answer.receipt.get("receipt_id"),
                   "from_cache": answer.receipt.get("from_cache")}
        full = [j for j in judged if j.match == "full" and j.code]
        living_full = [j for j in full if j.status == LIVING]
        split_full = [j for j in full if j.status == SPLIT]
        partial_living = [j for j in judged if j.match == "partial" and j.code and j.status == LIVING]

        def verdict(decision, reason, code=None, ask=""):
            return Verdict(decision, reason, code, judged, ask, [receipt], self.liveness.counts)

        if living_full:
            codes = {j.code for j in living_full}
            if len(codes) == 1:
                return verdict(CLASSIFIED, "LIVING_PRECEDENT_AGREES", codes.pop())
            return verdict(REVIEW, "PRECEDENTS_DISAGREE", ask=question)
        if split_full:
            return verdict(REVIEW, "SPLIT_SUCCESSORS")
        if partial_living:
            return verdict(REVIEW, "ONLY_PARTIAL", ask=question)
        if full:
            return verdict(REFUSE, "STALE_PRECEDENT")
        return verdict(REFUSE, "NO_LIVING_PRECEDENT")


def apply_memory(v: Verdict, product: str, memory: BrokerMemory, liveness: Liveness) -> Verdict:
    """Broker memory (Meterless H-MEM) can settle a REVIEW or challenge a CLASSIFIED verdict.

    It only ever chooses among codes that living precedents already support, so it cannot
    introduce a code without a precedent.
    """
    recalls = [r for r in memory.recall(product) if liveness.status(r.code) == LIVING]
    v.memory = [{"code": r.code, "memory_id": r.memory_id, "relevance": r.relevance, "broker": r.broker} for r in recalls]
    if not recalls:
        return v
    remembered = {r.code for r in recalls}
    if v.decision == CLASSIFIED and remembered - {v.code}:
        v.decision, v.reason, v.code = REVIEW, "MEMORY_CONFLICT", None
    elif v.decision == REVIEW and len(remembered) == 1:
        candidates = {j.code for j in v.precedents if j.code and j.match in ("full", "partial") and j.status == LIVING}
        code = next(iter(remembered))
        if code in candidates:
            v.decision, v.reason, v.code = CLASSIFIED, "BROKER_MEMORY", code
    return v


def verdict_json(v: Verdict) -> str:
    return json.dumps(v.to_dict(), indent=1)
