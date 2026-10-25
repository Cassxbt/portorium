"""Keep the answer out of anything a model sees during evaluation.

A CBP ruling states its own classification in the header (TARIFF NO., and the chapter inside the
CLA-2-xx file reference), in the legal analysis and often in passing. Evaluation inputs are the
product description only, with every code-like string and the answer's own digits removed.
"""
import re

_SALUTATION = re.compile(r"Dear [^:]{1,80}:")
_ANALYSIS = re.compile(
    r"\b(GRI\b|General Rules? of Interpretation|The applicable (sub)?heading|HTSUS|Harmonized Tariff|"
    r"provided for in|classification of the|Explanatory Notes?|In your letter|you requested|"
    r"classifiab|classified (under|in|as)|essential character|(sub)?headings?\b|chapters?\b|Section [IVX]+|"
    r"\bNote \d|TARIFF NO|clerical error|corrected ruling|prima facie|imparts?\b|Genera?l? Rules?\b|ssential character)",
    re.IGNORECASE,
)
_PATTERNS = [
    re.compile(r"\bCLA-\d+-\d+(:[A-Z0-9:]+)?", re.IGNORECASE),
    re.compile(r"\b(sub)?headings?\s+\d[\d.]*", re.IGNORECASE),
    re.compile(r"\bchapters?\s+\d+", re.IGNORECASE),
    re.compile(r"\b\d{4}\.\d{2}(\.\d{2,4})?(\.\d{2})?\b"),
    re.compile(r"\b\d{8}(\d{2})?\b"),
    re.compile(r"\b(NY|HQ|N|H|W|R)\s?[A-Z]?\d{5,6}\b"),
    re.compile(r"\b\d{6}\b"),
]


def answer_fragments(codes: list[str]) -> set[str]:
    """Every prefix of the answer a reader could recognise: heading, subheading, 8 and 10 digits."""
    fragments = set()
    for code in codes:
        digits = re.sub(r"\D", "", code)
        fragments.update(digits[:n] for n in (4, 6, 8, 10) if len(digits) >= n)
    return fragments


def extract_facts(text: str) -> str:
    """The product description: after the salutation, before the first legal analysis or request boilerplate."""
    start = _SALUTATION.search(text)
    body = text[start.end():] if start else text
    facts = []
    for sentence in re.split(r"(?<=[.!?])\s+|\t+", body):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _ANALYSIS.search(sentence):
            if facts:
                break
            continue
        facts.append(sentence)
    return " ".join(facts)


def strip_codes(text: str, answer_codes: list[str]) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(" ", text)
    for fragment in sorted(answer_fragments(answer_codes), key=len, reverse=True):
        text = re.sub(rf"(?<!\d){fragment}(?!\d)", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()
