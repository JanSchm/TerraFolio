# Edge-case project files

One deliberately broken or deliberately unusual project file per ingestion case in issue #13
(4C), for `docs/spec.md` §13 and the cases the file-based input model adds (epic §2).

Every file here is **one documented edit** to
[`tests/golden/fixtures/pipeline/P01-almonte-solar.json`](../../golden/fixtures/pipeline/P01-almonte-solar.json),
1C's reference-derived corpus. Deriving rather than hand-writing is not laziness: a valid file
carries thirty years of statements that must satisfy eighteen tie-outs
(`docs/pipeline-schema.md` §7), so a hand-written "minimal" file would fail for reasons its name
does not claim, and the test asserting the named reason would pass for the wrong one.

`tests/unit/test_edge_cases_ingestion.py::test_every_edge_fixture_differs_from_its_base_only_where_its_name_says`
holds these files to that: it repairs each documented pointer from the base and asserts the result
is the base again. That one assertion proves each fixture is current with 1A's schema, current with
1C's corpus, and broken in **exactly one** way.

## Ids are width 3 on purpose

`pipeline/loader.py::_require_uniform_id_width` fails the whole load when ids mix widths (C-5:
canonical order is lexicographic and the GA's draws are position-indexed, so `P9` beside `P10`
silently changes every result). Golden ids are `P01`–`P48`; the shipped `pipeline/` uses `P001`.
These fixtures take `P49`–`P53` so each one drops straight into a copy of the golden corpus and
inherits 48 valid companions — which is what makes "the rest of the pipeline is untouched"
assertable.

## The files

| File | Edit to the base | Expected outcome |
|---|---|---|
| `P49-fails-a-tie-out.json` | `statements.debtSchedule.closing[5] += 1.0` | Rejected. Fails §7.4 `closing = opening - repayment + drawdown` in 2032 by €1.000000m, and — because `closing[5]` feeds `opening[6]` — §7.4 `opening[t] = closing[t-1]` in 2033. The other 48 files load. |
| `P50-supplies-derived-results.json` | adds top-level `irr`, `moic`, `terminalValue` | Rejected by §9's reject-derived-fields rule, naming all three keys and explaining that they depend on the mandate's hold period. |
| `P51-twenty-nine-statement-years.json` | `statements.years` truncated to 29 | Rejected at the schema, naming the field and the expected length of 30. |
| `P52-thirty-one-statement-years.json` | `statements.years` extended to 31 | As above, with 31. |
| `P53-declares-outlier-assumptions.json` | `assumptions.taxRate` 0.20 → 0.35 | **Loads.** The corpus is otherwise unanimous at 0.20, so `taxRate` joins the dispersion report's disagreements with P53 at the upper tail, and a `plausibility.tax` warning fires. Neither blocks (A-7, epic §12 Q3). |
| `P01-duplicates-an-existing-id.json` | `name` only; keeps `id: "P01"` | Aborts the **load**, naming both this file and `P01-almonte-solar.json` (§7.9, 2A-11). No subset of the pipeline is usable, so nothing loads. |

`name` differs on every file so a rejection is legible in a report; `id` differs on all but the
duplicate, whose whole point is that it does not. The drift guard ignores both.

## Cases that need no fixture

Three of §13's rows are *unusual but valid*, and 1C's corpus already contains them — a real file
beats a synthesised one:

| Case | Files |
|---|---|
| Already operating in the base year (`codYear == baseYear == 2027`) | `P03`, `P08`, `P10`, `P14`, `P16`, `P22`, `P24`, `P31`, `P36`, `P42` |
| Non-EUR revenue, for the EUR-only screen | 14 files across PLN, RON, DKK, SEK and GBP |
| COD after the hold period, and therefore an undefined IRR | `P45` and `P48` at `holdYears=5`, whose exit year is 2031 against a 2032 COD |

An empty `pipeline/` is a `tmp_path` directory, not a committed one: git cannot hold an empty
directory, and a directory that is empty only because a README is not `*.json` would be testing the
glob rather than the case.
