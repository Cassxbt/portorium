import json

import numpy as np

from portorium.index import PrecedentIndex


def _write(directory, meta, vectors):
    directory.mkdir()
    with (directory / "meta.jsonl").open("w") as f:
        f.writelines(json.dumps(m) + "\n" for m in meta)
    np.save(directory / "vectors.npy", vectors)


def test_a_subset_of_touched_rulings_returns_the_full_index_candidates(tmp_path):
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(400, 16)).astype(np.float16)
    vectors[200:210] = vectors[100]  # rulings with identical subjects tie exactly
    meta = [{"ruling_number": f"N{i:06d}", "ruling_date": f"20{10 + i % 15}-01-01", "subject": f"s{i}", "codes": ["1"]}
            for i in range(len(vectors))]
    _write(tmp_path / "full", meta, vectors)
    full = PrecedentIndex(tmp_path / "full")
    queries = [(full.vectors[100] + 0.01, "2020-06-01", {"N000100"}), (rng.normal(size=16), "2026-01-01", set())]

    touched = sorted({i for q, as_of, ex in queries for i in full._candidates(full.vectors @ q, as_of, 20, ex)})
    _write(tmp_path / "subset", [meta[i] for i in touched], vectors[touched])
    subset = PrecedentIndex(tmp_path / "subset")

    for q, as_of, ex in queries:
        expected = [meta[i]["ruling_number"] for i in full._candidates(full.vectors @ q, as_of, 20, ex)]
        got = [subset.meta[i]["ruling_number"] for i in subset._candidates(subset.vectors @ q, as_of, 20, ex)]
        assert got == expected
