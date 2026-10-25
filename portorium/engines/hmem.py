"""Python port of the Meterless H-MEM reference (Apache-2.0, https://github.com/Meterless/Meterless/tree/main/engines/hmem/reference @1742dec). Derived work; ported by reading the TS source. In-memory only (no persistDir)."""
from __future__ import annotations
import copy, math, re, time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DAY_MS = 86_400_000
DEFAULTS = dict(top_n=5, threshold=0.35, consolidation_days=7, archive_min_days=30, archive_max_access=2,
                synthesis_cosine=0.82, dream_min=2, dream_related=0.35, max_dream=8, conflict_gate=0.70,
                entity_cap=10, related_cap=5, recency_days=14)
FEEDBACK = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}
clamp01 = lambda x: min(1.0, max(0.0, x))

def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", re.sub(r"[^a-z0-9\s]", " ", text.lower())) if len(t) > 1]

def _fnv1a(s: str) -> int:
    h = 0x811C9DC5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h

def embed(text: str, dim: int = 64) -> list[float]:
    v = [0.0] * dim
    for tok in tokenize(text):
        h = _fnv1a(tok)
        v[h % dim] += (1 if (h >> 8) % 2 == 0 else -1) * (1 + ((h >> 16) % 8) / 8)
    n = math.sqrt(sum(x * x for x in v))
    return v if n == 0 else [x / n for x in v]

cosine = lambda a, b: sum(x * y for x, y in zip(a, b))

def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb: return 0.0
    return len(sa & sb) / (len(sa) + len(sb) - len(sa & sb))

TECH = ["react","python","typescript","javascript","rust","sqlite","postgres","indexeddb","node","docker",
        "graphql","redis","webrtc","tauri","electron","llm","embedding"]
DOMAIN_KW = {"tech": ["code","bug","api","database","deploy","framework","library","server", *TECH],
             "work": ["meeting","deadline","client","project","sprint","review","report"],
             "personal": ["family","hobby","prefer","like","dislike","morning","evening"]}
CORRECTION = re.compile(r"\b(actually|instead|no longer|not anymore|changed to|correction)\b", re.I)

@dataclass
class Memory:
    id: str; content: str; type: str; layer: str; timestamp: int; last_accessed: int
    access_count: int; tags: list; embedding: list; confidence: float; source: str; provenance: dict
    domain: str; namespace: str; entities: list
    related_to: list = field(default_factory=list)
    chat_id: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    derived_from: Optional[list] = None

def _snap(m: Memory) -> dict:
    d = {k: copy.deepcopy(v) for k, v in m.__dict__.items() if k != "embedding"}
    return d

class Ledger:
    def __init__(self, clock): self.clock, self.entries = clock, []
    def log(self, memory_id, action, actor, previous=None, new=None, details=None):
        e = dict(memory_id=memory_id, action=action, actor=actor, timestamp=self.clock(),
                 previous_state=previous, new_state=new, details=details)
        self.entries.append(e); return e
    def history(self, memory_id): return [e for e in self.entries if e["memory_id"] == memory_id]

class Store:
    def __init__(self, clock, ledger, actor):
        self.clock, self.ledger, self.actor, self._r, self._seq = clock, ledger, actor, {}, 0
    def classify_domain(self, content, tags, type_):
        for t in tags:
            if t.startswith("project:"): return t[8:]
            if t.startswith("tech:"): return "tech"
        toks = set(tokenize(content)); best, best_s = "general", 0
        for d, kws in DOMAIN_KW.items():
            s = sum(1 for k in kws if k in toks)
            if d == "personal" and type_ in ("personal", "preference"): s += 1
            if s > best_s: best, best_s = d, s
        return best if best_s > 0 else "general"
    def extract_entities(self, content):
        ents = {}
        for t in tokenize(content):
            if t in TECH: ents[t] = 1
        for m in re.finditer(r"\b([A-Z][a-zA-Z]{2,})\b", content):
            n = m.group(1).lower()
            if n not in ("the", "this", "that", "with"): ents[n] = 1
        return list(ents)[: DEFAULTS["entity_cap"]]
    def add(self, content, source, type="general", layer="working", tags=None, confidence=0.7,
            provenance=None, chat_id=None, supersedes=None, derived_from=None) -> Memory:
        if not source or not source.strip():
            raise ValueError("H-MEM: a memory record with no source must be rejected by construction")
        now = self.clock(); self._seq += 1; tags = list(tags or [])
        dom = self.classify_domain(content, tags, type)
        bucket = "profile" if type in ("preference", "personal") else "facts" if type == "factual" else "notes"
        m = Memory(f"mem-{self._seq}", content, type, layer, now, now, 0, tags, embed(content), clamp01(confidence),
                   source, provenance or dict(origin="manual", learnedAt=now, label=source), dom, f"{dom}/{bucket}",
                   self.extract_entities(content), chat_id=chat_id, supersedes=supersedes, derived_from=derived_from)
        self._discover(m); self._r[m.id] = m
        self.ledger.log(m.id, "create", self.actor, new=_snap(m)); return m
    def _discover(self, rec):
        for o in self._r.values():
            if len(rec.related_to) >= DEFAULTS["related_cap"]: break
            shared = [e for e in o.entities if e in rec.entities]
            if not rec.supersedes and cosine(rec.embedding, o.embedding) > 0.85 and CORRECTION.search(rec.content):
                rec.supersedes = o.id; continue
            if shared and o.domain == rec.domain:
                rec.related_to.append(o.id)
                if len(o.related_to) < DEFAULTS["related_cap"] and rec.id not in o.related_to: o.related_to.append(rec.id)
    def get(self, i): return self._r.get(i)
    def all(self): return list(self._r.values())
    def update(self, i, **patch):
        m = self._r[i]; prev = _snap(m)
        for k, v in patch.items(): setattr(m, k, v)
        self.ledger.log(i, "update", self.actor, prev, _snap(m)); return m
    def delete(self, i, reason):
        m = self._r.pop(i); self.ledger.log(i, "delete", self.actor, _snap(m), details=dict(reason=reason))
    def touch(self, i): m = self._r[i]; m.last_accessed = self.clock(); m.access_count += 1
    def promote(self, i, layer):
        m = self._r[i]; prev = _snap(m); m.layer = layer; self.ledger.log(i, "promote", self.actor, prev, _snap(m))
    def snapshot_all(self): return copy.deepcopy(list(self._r.values())), self._seq
    def restore_all(self, recs, seq): self._r = {m.id: copy.deepcopy(m) for m in recs}; self._seq = seq

LAYER_W = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}

class Retrieval:
    def __init__(self, store, ledger, clock, actor): self.store, self.ledger, self.clock, self.actor = store, ledger, clock, actor
    def _score(self, m, qe, qt, qents, qdom):
        sem = clamp01(cosine(qe, m.embedding)); kw = jaccard(qt, tokenize(m.content))
        tag = jaccard(qt, [t.lower() for t in m.tags]); dom = 1 if m.domain == qdom else 0
        ent = jaccard(qents, m.entities); rec = math.exp(-((self.clock() - m.last_accessed) / DAY_MS) / DEFAULTS["recency_days"])
        raw = (0.35*sem + 0.20*kw + 0.10*tag + 0.10*dom + 0.15*ent + 0.05*LAYER_W[m.layer] + 0.05*rec
               - 0.20*(1 if m.superseded_by else 0))
        return clamp01(raw) * m.confidence
    def score_one(self, m, q):
        return self._score(m, embed(q), tokenize(q)[:8], self.store.extract_entities(q), self.store.classify_domain(q, [], "general"))
    def query(self, q, top_n=None, threshold=None, chat_id=None):
        qe, qt = embed(q), tokenize(q)[:8]; qents = self.store.extract_entities(q); qdom = self.store.classify_domain(q, [], "general")
        th = DEFAULTS["threshold"] if threshold is None else threshold; n = top_n or DEFAULTS["top_n"]
        scored = []
        for m in self.store.all():
            if chat_id and m.chat_id and m.chat_id != chat_id: continue
            s = self._score(m, qe, qt, qents, qdom)
            if s >= th: scored.append((m, s))
        scored.sort(key=lambda x: (-x[1], x[0].id)); top = scored[:n]
        for m, _ in top:
            self.store.touch(m.id); self.ledger.log(m.id, "read", self.actor, details=dict(query=q))
        return dict(memories=[dict(memory=m, relevance=s) for m, s in top], context=self._fmt(top),
                    trace=dict(scores={m.id: round(s, 4) for m, s in top}, query_domain=qdom))
    def _fmt(self, items):
        if not items: return ""
        by = {}
        for m, _ in items: by.setdefault(m.domain, []).append(m)
        out = ["[MEMORY CONTEXT]"]
        for d in sorted(by):
            out.append(f"## {d}")
            for m in by[d]:
                mk = {"long_term": "LT", "working": "WK", "short_term": "ST"}[m.layer]
                ents = f" (entities: {', '.join(m.entities)})" if m.entities else ""
                out.append(f"- [{mk}] {m.content}{ents} <{m.id}>")
        out.append("[/MEMORY CONTEXT]"); return "\n".join(out)
    def feedback(self, i, kind):
        m = self.store.get(i)
        if not m: raise KeyError(i)
        prev = _snap(m)
        if kind == "helpful": m.access_count += 1
        m.confidence = clamp01(m.confidence + FEEDBACK[kind])
        if kind == "wrong" and "review" not in m.tags: m.tags.append("review")
        self.ledger.log(i, "feedback", self.actor, prev, _snap(m), dict(kind=kind, delta=FEEDBACK[kind]))

class Sleep:
    def __init__(self, store, ledger, clock, actor):
        self.store, self.ledger, self.clock, self.actor, self.backups, self._n = store, ledger, clock, actor, {}, 0
    def preview(self):
        now, allm = self.clock(), self.store.all(); ref = set()
        for m in allm:
            ref.update(m.related_to); ref.add(m.supersedes) if m.supersedes else None; ref.update(m.derived_from or [])
        cons = [m.id for m in allm if m.layer == "short_term" and now - m.last_accessed >= DEFAULTS["consolidation_days"]*DAY_MS]
        arch = [m.id for m in allm if now - m.timestamp >= DEFAULTS["archive_min_days"]*DAY_MS
                and m.access_count <= DEFAULTS["archive_max_access"] and m.id not in ref
                and not m.supersedes and not (m.derived_from or []) and len(m.related_to) < 3]
        syn, used = [], set()
        for i, a in enumerate(allm):
            if a.id in used: continue
            g = [a] + [b for b in allm[i+1:] if b.id not in used and cosine(a.embedding, b.embedding) >= DEFAULTS["synthesis_cosine"]]
            if len(g) >= 2: used.update(x.id for x in g); syn.append([x.id for x in g])
        return dict(to_consolidate=cons, to_archive=arch, to_synthesize=syn)
    def execute(self, plan=None):
        plan = plan or self.preview(); self._n += 1; bid = f"backup-{self._n}"
        self.backups[bid] = self.store.snapshot_all()
        for i in plan["to_consolidate"]:
            if self.store.get(i): self.store.promote(i, "long_term"); self.ledger.log(i, "sleep_consolidate", self.actor)
        for i in plan["to_archive"]:
            m = self.store.get(i)
            if m: self.ledger.log(i, "sleep_archive", self.actor); self.store.delete(i, "sleep archive")
        for g in plan["to_synthesize"]:
            ms = [x for x in (self.store.get(i) for i in g) if x]
            if len(ms) < 2: continue
            merged = self.store.add("Synthesis: " + " | ".join(x.content for x in ms), "sleep:synthesis", type="factual",
                                    layer="long_term", tags=sorted({t for x in ms for t in x.tags}),
                                    confidence=max(x.confidence for x in ms), derived_from=[x.id for x in ms],
                                    provenance=dict(origin="sleep_synthesis", learnedAt=self.clock(), label=f"synthesized from {len(ms)} memories"))
            self.ledger.log(merged.id, "sleep_synthesize", self.actor, details=dict(sources=g))
            for x in ms: self.store.delete(x.id, "merged by sleep synthesis")
        return dict(backup_id=bid)
    def restore(self, bid):
        recs, seq = self.backups[bid]; self.store.restore_all(recs, seq); self.ledger.log("*", "restore", self.actor, details=dict(backupId=bid))

OPPOSING = [(r"\balways\b", r"\bnever\b"), (r"\benable[ds]?\b", r"\bdisable[ds]?\b"), (r"\bprefer[s]?\b", r"\bavoid[s]?|dislike[s]?\b"),
            (r"\bon\b", r"\boff\b"), (r"\byes\b", r"\bno\b")]

class Conflicts:
    def __init__(self, store, ledger, clock, actor): self.store, self.ledger, self.clock, self.actor, self.found, self._n = store, ledger, clock, actor, [], 0
    def scan(self):
        allm, out = self.store.all(), []
        for i in range(len(allm)):
            for j in range(i+1, len(allm)):
                c = self._check(allm[i], allm[j])
                if c:
                    out.append(c); self.found.append(c)
                    self.ledger.log(c["a"], "conflict_detected", self.actor, details=dict(with_=c["b"], reason=c["reason"], confidence=c["confidence"]))
        return out
    def _check(self, a, b):
        if a.supersedes == b.id or b.supersedes == a.id or a.superseded_by == b.id or b.superseded_by == a.id: return None
        sim, reason, conf = cosine(a.embedding, b.embedding), "", 0.0
        for p, q in OPPOSING:
            if (re.search(p, a.content, re.I) and re.search(q, b.content, re.I)) or (re.search(q, a.content, re.I) and re.search(p, b.content, re.I)):
                if sim > 0.5: reason, conf = "opposing phrase pair on similar content", 0.5 + 0.3*sim
        if not reason and sim > 0.8:
            na, nb = re.findall(r"\d+(?:\.\d+)?", a.content), re.findall(r"\d+(?:\.\d+)?", b.content)
            if na and nb and ",".join(na) != ",".join(nb): reason, conf = "high-similarity conflicting numeric values", 0.55 + 0.25*sim
        if not reason: return None
        if [e for e in a.entities if e in b.entities]: conf += 0.1
        if a.domain == b.domain: conf += 0.05
        if b.id in a.related_to: conf -= 0.1
        self._n += 1
        return dict(id=f"conflict-{self._n}", a=a.id, b=b.id, reason=reason, confidence=clamp01(conf), resolved=False)
    def auto_resolve(self, c):
        a, b = self.store.get(c["a"]), self.store.get(c["b"])
        sc = lambda m: (0.3*(m.last_accessed/max(1, self.clock())) + 0.2*min(1, m.access_count/10) + 0.25*m.confidence
                        + 0.1*min(1, len(m.content)/200) + 0.15*(1 if m.provenance.get("origin") == "user_correction" else 0.5))
        sa, sb = sc(a), sc(b); margin = abs(sa - sb) / max(sa, sb)
        if clamp01(c["confidence"]*0.6 + margin*0.8) < DEFAULTS["conflict_gate"]: return "queued"
        w, l = (a, b) if sa >= sb else (b, a); prev = _snap(l); l.superseded_by = w.id; c["resolved"] = True
        self.ledger.log(l.id, "conflict_resolved", self.actor, prev, _snap(l), dict(winner=w.id)); return "resolved"

class Dreaming:
    def __init__(self, store, ledger, clock, actor): self.store, self.ledger, self.clock, self.actor, self.p, self._n = store, ledger, clock, actor, {}, 0
    def _rel(self, a, b):
        return 0.5*max(0, cosine(a.embedding, b.embedding)) + 0.2*jaccard(a.tags, b.tags) + 0.1*(a.domain == b.domain) + 0.2*jaccard(a.entities, b.entities)
    def dream(self):
        out = []
        def propose(**kw):
            self._n += 1; p = dict(id=f"dream-{self._n}", status="proposed", **kw); self.p[p["id"]] = p; out.append(p)
            self.ledger.log(p["id"], "dream_proposed", self.actor, details=dict(type=p["type"]))
        allm, used, n = self.store.all(), set(), 0
        for seed in allm:
            if seed.id in used or n >= DEFAULTS["max_dream"]: continue
            g = [seed] + [o for o in allm if o.id != seed.id and o.id not in used and self._rel(seed, o) >= DEFAULTS["dream_related"]]
            if len(g) >= DEFAULTS["dream_min"]:
                used.update(x.id for x in g)
                propose(type="insight", content=f"Across {len(g)} related memories: " + " + ".join(x.content for x in g), derived_from=[x.id for x in g]); n += 1
        n = 0
        for m in allm:
            if n >= DEFAULTS["max_dream"]: break
            if m.domain != "general": continue
            s = self.store.classify_domain(m.content, m.tags, m.type)
            if s != "general": propose(type="domain", content=f'Reclassify "{m.content[:60]}" as {s}', derived_from=[m.id], suggested_domain=s); n += 1
        by = {}
        for m in allm:
            if m.type in ("preference", "personal") or "event:user_correction" in m.tags: by.setdefault(m.domain, []).append(m)
        n = 0
        for d, lst in by.items():
            if n >= DEFAULTS["max_dream"]: break
            if len(lst) < 2: continue
            propose(type="invariant", content=f"Stable pattern in {d}: " + "; ".join(x.content for x in lst), derived_from=[x.id for x in lst]); n += 1
        return out
    def approve(self, pid):
        p = self.p.get(pid)
        if not p or p["status"] != "proposed": raise KeyError(pid)
        p["status"] = "approved"; self.ledger.log(pid, "dream_approved", self.actor, details=dict(type=p["type"]))
        if p["type"] == "domain":
            for i in p["derived_from"]:
                if self.store.get(i): self.store.update(i, domain=p["suggested_domain"])
            return None
        return self.store.add(p["content"], "dreaming", type="factual" if p["type"] == "insight" else "preference", layer="long_term",
                              tags=[f"dream:{p['type']}"], confidence=0.75, derived_from=p["derived_from"],
                              provenance=dict(origin="dream", learnedAt=self.clock(), label=f"approved {p['type']} proposal"))

class HMEM:
    def __init__(self, clock: Callable[[], int] = lambda: int(time.time()*1000), actor="system"):
        self.clock = clock; self.ledger = Ledger(clock); self.store = Store(clock, self.ledger, actor)
        self.retrieval = Retrieval(self.store, self.ledger, clock, actor); self.conflicts = Conflicts(self.store, self.ledger, clock, actor)
        self.sleep = Sleep(self.store, self.ledger, clock, actor); self.dreaming = Dreaming(self.store, self.ledger, clock, actor)
    def add(self, **kw): return self.store.add(**kw)
    def query(self, q, **kw): return self.retrieval.query(q, **kw)
    def feedback(self, i, kind): self.retrieval.feedback(i, kind)
