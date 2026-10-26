import json

import numpy as np

from portorium.index import PrecedentIndex


class FakeNvidia:
    def embed(self, texts, input_type):
        return np.ones((1, 8), dtype=np.float32) / np.sqrt(8)

    def rerank(self, query, passages):
        return [float(len(p)) for p in passages]


def _write(directory, meta, vectors=None, info=None):
    directory.mkdir()
    (directory / "meta.jsonl").write_text("".join(json.dumps(m) + "\n" for m in meta))
    if vectors is not None:
        np.save(directory / "vectors.npy", vectors)
    if info:
        (directory / "info.json").write_text(json.dumps(info))


def test_sample_mode_replays_the_recorded_candidates_on_any_later_day(tmp_path):
    rng = np.random.default_rng(3)
    meta = [{"ruling_number": f"N{i:06d}", "ruling_date": f"20{10 + i % 15}-03-01", "subject": "s" * (i % 9 + 1), "codes": ["1"]}
            for i in range(300)]
    _write(tmp_path / "full", meta, rng.normal(size=(300, 8)).astype(np.float16))
    full = PrecedentIndex(tmp_path / "full", live=True, recordings_dir=tmp_path / "rec")
    expected = full.search(FakeNvidia(), "a product", "2026-10-26", exclude={"N000007"})

    used = {p.ruling_number for p in expected} | {c for c, _ in
            json.loads((tmp_path / "rec" / "index.jsonl").read_text().splitlines()[0])["response"]["candidates"]}
    _write(tmp_path / "sample", [m for m in meta if m["ruling_number"] in used], info=full.info)
    sample = PrecedentIndex(tmp_path / "sample", live=False, recordings_dir=tmp_path / "rec")
    assert sample.search(FakeNvidia(), "a product", "2027-01-15", exclude={"N000007"}) == expected
