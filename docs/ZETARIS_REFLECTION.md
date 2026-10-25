# Reflection: Zetaris as Portorium's data layer

Michael Hay asked participants to reflect on their USL: how it saved time, how it helps in the work, and how to improve it. These are measured answers, not estimates.

## What the USL holds
- **USL `portorium`:**
  - `hts_line`: HTS 2026 Rev 21, 19,949 ten-digit lines, duty rate inherited down the hierarchy;
  - `hts_code`: 8- and 10-digit lines;
  - `hts_release`: a live REST source, USITC `currentRelease`;
  - `ruling`: 75,722 CBP CROSS rulings;
  - `ruling_code`: 75,870 ruling-to-code rows, with foreign keys to `ruling` and `hts_line`.
- **USL `audit`:** `verdict_log`. Every live Portorium verdict is inserted with its HTS release and its Nightshift receipt.

## The rules that decide
| Rule | Table | What it decides in Portorium |
|---|---|---|
| `code_in_schedule` | ruling_code | A precedent's code still exists in HTS Rev 21 (living) |
| `parent_survives` | ruling_code | When it doesn't: was the line split (REVIEW, with successor lines) or removed (stale)? |
| `release_is_snapshot` | hts_release | The live USITC release is still the one our schedule came from |
| `classified_code_living` | verdict_log | Re-screens every past CLASSIFIED verdict against today's schedule |
| `classified_has_receipt` | verdict_log | No classification without its Nightshift receipt |
| `code_is_numeric`, `not_future_dated`, `subject_present`, `rate_resolved` | — | Hygiene on the loaded data; all pass except `subject_present` (76 CBP rulings have an empty subject) |

**Per decision:** the gate asks Zetaris for each candidate precedent's date, revocation flag and code status (same logic as the two rules). At startup it cross-checks that logic against the rules' own invalid records and refuses to run on any mismatch. Across 75,870 ruling codes:
- 53,723 living;
- 7,938 split;
- 14,209 removed.

These match an independent recomputation in plain Python (`scripts/verify_dq_offline.py`) exactly.

## Did it save time? Honestly
- **Lines of code are not the headline here.** The two liveness rules replace 18 lines of Python, which we wrote anyway as the cross-check. The hierarchy flatten is one window expression in the activation query, `last(NULLIF(general_rate, ''), true) OVER (ORDER BY row_idx)`, where Python needs a parent-walk by indent. Recomputing that expression's logic in Python, it agrees with a true parent-walk on 19,948 of 19,949 leaves; the exception is 9802.00.91.00, whose "rate" is a text note.
- **What it actually gave us:**
  - The rules live with the data, run on demand (`RUN DQ`), and return the failing records to the agent (`SHOW DQ INVALID RECORD`). The gate acts on the database's answer instead of re-implementing it.
  - **Decisions are audited by the same rules.** When USITC publishes Revision 22, one `RUN DQ classified_code_living` lists every past classification whose code died. That is the re-screen an importer needs, done without new code.
  - The live release is a REST source with a rule on it. "Has the schedule changed since our snapshot?" is a data-quality question, not a scheduler.
  - The USL documents the model (tables, keys, rules, descriptions), so a broker's IT team can read what the agent relies on.

## How it helps the work
A small importer or junior broker can see, per verdict:
- which rulings were considered;
- which were still alive in the schedule, and which rule failed for the others;
- which ruling was revoked;
- after a revision, which past decisions need another look.

That makes the classification auditable instead of being a model's confident guess.

## What we would improve (found while building, on Zetaris 2.4.3.1 local)
- **Writes:**
  - `INSERT INTO` works on a CSV filestore table only when its path is a **folder** that already holds a header part file.
  - A table registered before its file exists gets zero columns.
  - Neither is documented next to `CREATE LIGHTNING FILESTORE TABLE`.
- **Single-suffix lines:** USITC lists an 8-digit line with one statistical suffix only at 10 digits. Our first `parent_survives` assumed every 8-digit line has its own row, and Zetaris reported 38,073 invalid rows instead of 14,209. That was our error, caught because the counts were impossible. A worked example in the getting-started repo would save others the same mistake.
- **Rule editing:** `UPDATE DQ ... SET EXPRESSION =` was rejected by the parser in our build. `REMOVE DQ` then `REGISTER DQ` worked.
- **API sessions:** each REST call runs in its own session, so a `CREATE TEMPORARY VIEW` is gone by the next call (relevant to the `sourceTable` write route).
- **Wish:** a rule type with a parameter (here, the snapshot release name) instead of a literal in the expression.
