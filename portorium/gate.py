"""Precedent Gate: no code without a living precedent.

Numbered checks, decided in code. The model only judges whether a precedent describes the same
product; it never chooses a code from memory.
  1 retrieve   Nemotron Embed + Rerank over CBP rulings issued before `as_of`
  2 living     Zetaris: code_in_schedule / parent_survives against HTS 2026 Rev 21
  3 same       Nemotron Ultra via Nightshift: full | partial | none, on product facts only
  4 decide     CLASSIFIED only when every living full-match precedent agrees at 10 digits

Broker decisions recalled from memory (Meterless H-MEM) are judged in the same call as the
rulings, so a remembered decision counts only for a product the model finds to be the same.
Anything the gate cannot resolve goes to REVIEW.
"""
import re
from dataclasses import asdict, dataclass, field

from .index import Precedent, PrecedentIndex
from .liveness import LIVING, SPLIT, Liveness
from .memory import BrokerMemory
from .nightshift import ULTRA, Answer, Nightshift
from .nvidia import Nvidia
from .rulings import RulingTexts

CLASSIFIED, REVIEW, REFUSE = "CLASSIFIED", "REVIEW", "REFUSE"
MATCHES = ("full", "partial", "none")
RULING, MEMORY = "ruling", "memory"
HS2022 = "2022-01-01"

SYSTEM = """You are a US customs classification analyst. Compare an importer's product with prior decisions:
CBP rulings, and decisions a licensed broker signed off earlier (BROKER entries).
For each one decide whether it describes the same product for tariff purposes:
  "full"    the tariff-relevant facts match: material and composition, construction (e.g. knitted vs woven),
            form, function and use, and gender or age where garments depend on it
  "partial" similar, but one tariff-relevant fact differs or is unknown from the product description
  "none"    a different product
An entry without FACTS can be at most "partial".
If an entry cites several codes, give the code that applies to this product, else "none".
Decide only from the facts given. Do not use your own knowledge of tariff numbers.
If entries with "full" or "partial" disagree, write the single question to the importer whose answer
would separate them; otherwise "".
Reply with only a JSON object:
{"precedents": [{"ruling": "<id as given>", "match": "full|partial|none", "code": "<one of its codes or none>",
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

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_code(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _key(value) -> str:
    return re.sub(r"[^A-Z0-9:]", "", str(value or "").upper())


class PrecedentGate:
    def __init__(self, index: PrecedentIndex, nvidia: Nvidia, nightshift: Nightshift, liveness: Liveness,
                 texts: RulingTexts | None = None, model: str = ULTRA, top: int = 6):
        self.index, self.nvidia, self.nightshift, self.liveness = index, nvidia, nightshift, liveness
        self.texts, self.model, self.top = texts, model, top

    def classify(self, product: str, as_of: str, answers: list[tuple[str, str]] = (),
                 exclude: set[str] = frozenset(), memory: BrokerMemory | None = None) -> Verdict:
        precedents = self.index.search(self.nvidia, product, as_of, exclude=exclude)[: self.top]
        facts = {p.ruling_number: f for p in precedents if self.texts and (f := self.texts.facts(p.ruling_number))}
        for recall in memory.recall(product) if memory else []:
            if self.liveness.status(recall.code) == LIVING:
                entry = Precedent(f"MEM:{recall.memory_id}", "", f"broker {recall.broker}", [recall.code], [])
                precedents.append(entry)
                facts[entry.ruling_number] = recall.content
        if not precedents:
            return Verdict(REFUSE, "NO_PRECEDENT_FOUND", None, [], dq=self.liveness.counts)
        judged, question, answer = self.judge(product, precedents, answers, facts)
        return self.decide(judged, question, answer)

    def judge(self, product: str, precedents: list[Precedent], answers,
              facts: dict[str, str]) -> tuple[list[JudgedPrecedent], str, Answer]:
        lines = [f"PRODUCT: {product}"]
        lines += [f"IMPORTER ANSWERED: {q} -> {a}" for q, a in answers]
        for p in precedents:
            codes = ", ".join(f"{c[:4]}.{c[4:6]}.{c[6:]}" for c in p.codes)
            label = "BROKER" if p.ruling_number.startswith("MEM:") else "PRECEDENT"
            detail = f" FACTS: {facts[p.ruling_number]}" if p.ruling_number in facts else ""
            lines.append(f"{label} {p.ruling_number} ({p.ruling_date or 'broker sign-off'}): {p.subject}. "
                         f"CODES: {codes}.{detail}")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]
        data, answer = self.nightshift.chat_json(messages, {"precedents": None, "question": None}, model=self.model)
        rows = [row for row in data["precedents"] if isinstance(row, dict)]
        return [self._judged(p, self._row_for(p, rows), p.ruling_number in facts) for p in precedents], \
            str(data.get("question") or ""), answer

    @staticmethod
    def _row_for(p: Precedent, rows: list[dict]) -> dict:
        """The model may write "NY N351214" for N351214 or "HQ 086616" for 086616."""
        target = _key(p.ruling_number)
        for row in rows:
            key = _key(row.get("ruling"))
            if key and (key == target or key.endswith(target)):
                return row
        return {}

    def _judged(self, p: Precedent, row: dict, has_facts: bool) -> JudgedPrecedent:
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
        status = self.liveness.status(code) if code else None
        kind = MEMORY if p.ruling_number.startswith("MEM:") else RULING
        return JudgedPrecedent(
            p.ruling_number, p.ruling_date, p.subject, p.codes, match, code,
            str(row.get("fact") or ("not judged" if not row else "")), status,
            self.liveness.rule_failed(code) if code else None, bool(p.ruling_date) and p.ruling_date < HS2022,
            kind, unresolved, self.liveness.successors(code) if status == SPLIT else [])

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
