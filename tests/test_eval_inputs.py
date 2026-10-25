import re

import pytest

from portorium.config import DATA
from portorium.eval import load_items
from portorium.leakage import answer_fragments

REVEALING = re.compile(r"classifiab|classified (under|in|as)|ssential character|\bheadings?\b|\bsubheading|"
                       r"\bchapters?\b|\bGRI\b|Genera?l? Rule|TARIFF NO|HTSUS|\bNote \d", re.I)


@pytest.mark.skipif(not (DATA / "eval" / "dev.jsonl").exists(), reason="eval sets not built")
@pytest.mark.parametrize("split", ["dev", "test"])
def test_no_eval_input_reveals_its_answer(split):
    for item in load_items(split):
        digits = set(re.sub(r"\D", " ", item.query).split())
        assert not digits & answer_fragments([item.answer]), item.ruling_number
        assert not REVEALING.search(item.query), item.ruling_number
