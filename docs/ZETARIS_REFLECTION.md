# Reflection: Zetaris as Portorium's data layer

Written against the Zetaris evaluation criteria. The queries and their real output are in [`zetaris_evidence.md`](zetaris_evidence.md), generated live by `scripts/zetaris_evidence.py`; the DDL, activations and rule registrations are in [`scripts/zetaris_usl.sql`](../scripts/zetaris_usl.sql).

## 1a. The use case and its sources

**Use case.** An import broker needs a tariff code that is defensible today: cited by a US Customs ruling that still stands, alive in the current schedule, and still meaning what it meant. Portorium's gate asks Zetaris those questions for every decision.

| # | Source | Publisher, form | What only it answers |
|---|---|---|---|
| 1 | HTS 2026 Revision 21 | USITC export, CSV snapshot | which codes exist today, and what each line and its parents say |
| 2 | CBP CROSS rulings and the codes they cite | US Customs and Border Protection, CSV | the precedents: which code CBP gave which product, and whether the ruling was revoked |
| 3 | Current HTS release | USITC REST API, read live by Zetaris on every query | whether the snapshot is still the schedule in force |
| 4 | Portorium's verdict ledger | written back by the agent | what the agent decided, on which release, with which receipts |

Sources 1 and 3 come from the same publisher but are different things: a frozen document and a live service. Source 4 is the agent's own output, kept so the same rules can check it.

## 1b. Exploratory queries per source

[`zetaris_evidence.md`](zetaris_evidence.md) has two or three per source with their results, for example:
- 19,949 ten-digit lines in Rev 21, and what `6110.20.20` covers today, line by line;
- 75,722 rulings, 516 revoked;
- the release USITC publishes right now (`2026HTSRev21`);
- how the ledger's live verdicts were decided.

## 1c. The USL, shown active, and its rules executed

- **USLs:** `portorium` (five tables, keys between rulings, codes and the schedule) and `audit` (`verdict_log`). DDL in `scripts/zetaris_usl.sql`.
- **Active:** cross-source questions that only work through the USL, for example: of the 75,870 codes rulings cite, **53,723** are living, **7,938** split and **14,209** removed in Rev 21; and which 2026 rulings already cite a code that is not a line of Rev 21.
- **Rules executed:** `code_in_schedule` (22,147 invalid of 75,870), `parent_survives` (14,209), `release_is_snapshot` (0 invalid: Rev 21 is still current), `classified_code_living` and `classified_has_receipt` on the ledger, and the hygiene rule `subject_present` (76 rulings have no subject).

**Where rules change a decision.** Per request, the Auditor runs the same logic for the retrieved precedents, and the gate acts on it: a split code goes to review with the codes its line carries now, a revoked ruling cannot carry its code, a missing row means `EVIDENCE_MISSING` rather than a fallback, and a release other than the snapshot withholds classification (`SCHEDULE_OUTDATED`). The per-request query mirrors the two registered rules; at start-up the gate checks its own view of the schedule against the rules' invalid records and refuses to run on any mismatch.

## 2a. Did it save time against hand coding? Measured where we can

We did not build the project twice, so there is no stopwatch on the total. What we can measure:

| | Hand-coded (`scripts/verify_dq_offline.py`) | Zetaris |
|---|---|---|
| Liveness for all 75,870 ruling codes | 29 lines of Python, 0.75 s | two registered one-line rules, about 1.1 s each with `RUN DQ` |
| Result | 22,147 and 14,209 invalid | 22,147 and 14,209 invalid: identical |
| Rate inherited down the hierarchy | a parent walk by indentation | one window expression in the activation; agrees with a true parent walk on 19,948 of 19,949 lines (9802.00.91.00's "rate" is a text note) |

Speed and line count are not the gain. The gain is where the logic lives:
- the rules sit with the data, any tool can run them, and `SHOW DQ INVALID RECORD` returns the failing rows themselves;
- the agent's own decisions are written back and checked by the same rules;
- "has the schedule changed?" became a rule on a live REST table, not a scheduler.

## 2b. How it helps the work, and a forecast

For a broker, each verdict shows which rulings were considered, which were alive and which rule failed for the others, and which were revoked.

**What it does not do yet.** When USITC publishes Revision 22, `release_is_snapshot` fails and Portorium withholds new classifications. But re-checking past verdicts needs the new schedule loaded first, and a code that survives a revision while its meaning moves needs its wording re-judged, not only its membership. That is the requirement below.

**A project that would benefit.** A broker's compliance desk re-validating its product master file, say 5,000 SKUs, after each revision. By hand: export the new schedule, diff the codes, look up affected SKUs and their rulings, and check revocations, all in spreadsheets. With this USL: load the revision, run two rules and one join. We estimate the mechanical diff falls from about a day of spreadsheet work to under an hour. That is an estimate, not a measurement; judging changed meanings would still be the broker's work.

## 2c. A requirement for Zetaris, written as a PM

**Title: Versioned data-quality contracts: bind a decision to the source snapshot it used, and re-judge it when the source moves.**

**Problem.** Today a DQ rule answers about the data as it is now. An agent's decision was made on data as it was then. When a source changes, nothing links the old decision to the old snapshot, so "which of my decisions are now unsupported?" needs custom code. Portorium hard-codes `'2026HTSRev21'` in a rule because rules take no parameters.

**Requirements.**
1. `REGISTER DQ ... VERSIONED BY <column or source property>`: each run of a rule records the source version it saw.
2. Parameterised rules: `REGISTER DQ release_is(:release) ...`, run as `RUN DQ release_is('2026HTSRev21')`.
3. A decision table can declare `EVIDENCE (source, version, key)` columns, and `RUN DQ ... AS OF VERSION v` evaluates a rule against the snapshot a decision used.
4. `SHOW DQ DRIFT <table> FROM v1 TO v2` returns decisions whose evidence rows changed between versions, even when the decision's own row did not.

**Acceptance tests.**
- Load Rev 21, record 100 verdicts, load Rev 22 with 3 codes split and 1 line reworded: `SHOW DQ DRIFT` returns exactly the verdicts citing those 4 lines, and none of the other 96.
- `RUN DQ code_in_schedule AS OF VERSION 'Rev21'` returns the same counts after Rev 22 is loaded as before.
- A parameterised rule run with two different releases returns two different results without re-registering.

**Mocked feature (our own SQL today, to show the shape).**

```sql
-- What we would like to write:
REGISTER DQ code_in_schedule TABLE portorium.ruling_code VERSIONED BY hts_release.name
  AS hts10 IN (SELECT hts10 FROM portorium.hts_line);
SHOW DQ DRIFT audit.verdict_log FROM '2026HTSRev21' TO '2026HTSRev22';

-- What it replaces: a versioned schedule table and a hand-written drift join.
CREATE TABLE hts_line_versioned (release varchar(40), hts10 varchar(10), description varchar(2000));
SELECT v.verdict_id, v.code FROM audit.verdict_log v
  JOIN hts_line_versioned a ON a.hts10 = v.code AND a.release = v.hts_release
  LEFT JOIN hts_line_versioned b ON b.hts10 = v.code AND b.release = '2026HTSRev22'
WHERE b.hts10 IS NULL OR b.description <> a.description;
```

## Found while building (Zetaris 2.4.3.1, local)

- **Writes:** `INSERT INTO` works on a CSV filestore table only when its path is a folder that already holds a header part file, and a table registered before its file exists gets zero columns. Neither is documented next to `CREATE LIGHTNING FILESTORE TABLE`.
- **Single-suffix lines:** USITC lists an 8-digit line with one statistical suffix only at 10 digits. Our first `parent_survives` assumed every 8-digit line has its own row, and Zetaris reported 38,073 invalid rows instead of 14,209. That was our error, caught because the count was impossible; a worked example in the getting-started repo would save others the same mistake.
- **API sessions:** each REST call runs in its own session, so a `CREATE TEMPORARY VIEW` is gone by the next call.
- **Expired tokens:** an expired session returns HTTP 400 `{"msg":"Bad Request"}` rather than 401, so clients cannot tell it from a SQL error without matching the body.
