# Data

All data is public US government data, fetched during the hackathon (22–27 Oct 2026). Works of the US federal government are not subject to copyright in the US (17 U.S.C. § 105).

| Path | What it is | Source | In the repo |
|---|---|---|---|
| `hts/hts_2026HTSRev21.json.gz` | The full Harmonized Tariff Schedule, 2026 Revision 21 (35,805 rows) | USITC, `https://hts.usitc.gov/reststop/exportList?from=0101&to=9999&format=JSON&styles=false` | yes |
| `hts/release_2026HTSRev21.json` | The release the snapshot came from | USITC, `https://hts.usitc.gov/reststop/currentRelease` (Zetaris also reads it live, see `scripts/zetaris_usl.sql`) | yes |
| `eval/dev.jsonl`, `eval/test.jsonl` | 780 dev and 684 test items: CBP "Classification" rulings issued in 2026 that cite exactly one 10-digit code. Dev: Jan–May 2026, used for tuning. Test: from 1 Jun 2026, after the Nemotron 3 Ultra and 3.5 Lightning post-training cutoff (May 2026), run once. | built by `scripts/build_eval_sets.py` from the CROSS snapshot | yes |
| `facts/<ruling>.txt` | The product description of each ruling the gate or the evaluation has read: the text between the salutation and the legal analysis, with any sentence carrying a title and name, a postal address, an email or a phone number removed (`portorium/rulings.py`) | CBP CROSS, `https://rulings.cbp.gov/api/ruling/<number>` | yes |
| `sample/` | The sample-mode bundle: recorded responses of NVIDIA, Nightshift and Zetaris for the documented demo inputs, each search's candidate list, and the index entries those name | `scripts/build_sample.py` | yes |
| `eval/results/` | Evaluation runs: predictions, judgments and the score table | `scripts/run_eval.py` | final runs only |
| `cross/` | CROSS search snapshot (75,733 rulings, 35 MB). Subject lines name importers, so it is not committed. | `scripts/pull_cross.py` (1 request/s) | no |
| `index/` | Nemotron Embed vectors for the 56,371 labelled rulings issued before 1 Jun 2026 (231 MB) | `scripts/build_index.py` | no |
| `build/`, `fulltext/`, `recordings/`, `memory/` | Local caches, Zetaris CSV inputs, live recordings and broker memory | generated at runtime | no |

## Regenerate

```bash
python scripts/pull_cross.py months && python scripts/pull_cross.py build   # CROSS snapshot -> data/cross/rulings.jsonl
python scripts/build_eval_sets.py data/cross/rulings.jsonl                    # dev/test splits
python scripts/build_index.py data/cross/rulings.jsonl                        # needs NVIDIA_API_KEY
python scripts/build_zetaris_inputs.py data/cross/rulings.jsonl               # CSVs for the Zetaris USL
python scripts/load_zetaris.py                                                # registers the USL, tables and DQ rules
```

The evaluation never shows a system a ruling's own number, its codes or any ruling it relates to (`portorium/leakage.py`, checked by `tests/test_eval_inputs.py`), and precedent search only returns rulings issued before the start of the split.
