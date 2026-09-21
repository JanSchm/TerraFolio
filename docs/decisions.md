# Decisions — issue 1A (#2)

> **Merge note.** Issue 1B (#3) owns `docs/decisions.md` and has its own sections
> (`D-n` departures, `A-n` resolved ambiguities, `Q-n` open questions). This file
> is 1A's contribution and is meant to be **concatenated** with 1B's rather than
> chosen between; the two branches add the file independently, so whichever
> merges second resolves by appending. 1A numbers its entries `C-n` to avoid
> colliding with either of 1B's sequences — the same numbering used in the
> `CONTRACT` comment on #1.

---

## Schema decisions

1A owns the project-file schema under the rewritten epic §8: the pydantic models
in `src/terrafolio/domain/` are its executable definition, and
`docs/pipeline-schema.md` documents what they land. The baseline is 1B's
document as written — `execution` rather than a risk block, `provenance.fields`
by seven groups, the `ppe`-only balance sheet, `UK` as an alias of `GB`, and
A-1 through A-4 — with four changes.

None of the four alters the meaning of a financial value, adds or changes a
tie-out identity, or touches a sign convention. Two changes that *would* have
are raised as open questions below rather than taken.

### C-1 — `asset.technology` is `solar_pv`, not `solar`

The value names a technology, not a resource, and is symmetric with
`onshore_wind` and `offshore_wind`. Issue 1A enumerates it explicitly, and spec
§7.4 reads "Solar PV, onshore wind, offshore wind".

Raised early and in one batch because it costs one line each in
`docs/pipeline-schema.md`, `templates/project-template.json`, 1C's emitter and
1D's `feasibility.js` adapter, and more every day after that. The assumption
set's `exit_multiples`, `degradation` and `capture_factor` tables are keyed by
the same three values.

### C-2 — three declared escalators added to `assumptions`

`priceEscalation`, `merchantEscalation`, `opexEscalation`; fractions, domain
−0.10 … 0.25.

Epic §2 and issue 2A both specify a cross-file dispersion report covering
"the three escalators", and the schema as landed carried none of them. Nothing
else in a file lets a consumer recover them, so the report could not have been
built as specified. They are declared inputs and participate in no tie-out. The
reference's values are 0.005 contracted, 0.021 merchant, 0.021 opex.

### C-3 — `assumptions.degradationRate` added

Fraction per year, domain 0 … 0.10. Issue 2C's variance report re-derives
generation from a file's declared assumptions and cannot do so without it.
Already present in 1C's posted field inventory.

### C-4 — `assumptions.targetDscr` added

Domain 1.0 … 3.0. The declared debt-sizing basis, 1.40× in the reference, and
already in 1C's inventory. It also lets the §10 DSCR plausibility check compare
against the file's own declared target rather than only a global band; 1A adds
the field, 2A owns whether the check uses it.

### C-5 — project ids must be zero-padded to a uniform width within a pipeline

Not a schema change — the id pattern is unchanged — but a determinism
requirement that nothing else states. Canonical order is plain lexicographic
`sorted(id)`, so `"P10" < "P9"`, and the GA's PRNG draws are indexed by
position: a pipeline mixing `P9` with `P10` reorders the population and changes
every result, silently.

`canonical_order()` in `domain/conventions.py` documents this rather than
repairing it. Sorting numerically instead would only move the problem to ids
that are not numeric, which the pattern permits. Generators zero-pad (1C's
`P01…P48`, 2C's `P001…P300`, both fine on their own); the loader is the right
place to reject a non-uniform set.

---

## Engineering decisions

### C-6 — `domain` is split into a pydantic-free leaf and a pydantic shell

The epic's chain is `api → runner → optimiser → economics → pipeline → config →
domain`, and the numeric core may import "numpy, stdlib and `config/` only,
never pydantic". Those two statements conflict as written: `config` imports
`domain` to key its tables, so if `domain` is pydantic then `optimiser` imports
pydantic **transitively, through an edge the rules explicitly allow**, and a
per-file import check passes while the boundary is a fiction.

So `domain/enums.py`, `conventions.py`, `scalars.py`, `mandate_bounds.py` and
`file_bounds.py` are standard library only, and `project_file.py`, `mandate.py`,
`results.py`, `reduce.py` and `errors.py` are the pydantic shell.
`domain/__init__.py` stays empty of imports — a convenience re-export there
would execute on every `from terrafolio.domain.enums import ...` and undo the
split — and a test asserts it.

`tests/unit/test_import_boundaries.py` checks this three ways: per module,
transitively over the first-party graph, and at runtime in a child process that
poisons `pydantic` on `sys.meta_path`. Only the third catches a pydantic import
added to `domain.enums`.

### C-7 — `AssumptionSet` is a tree of frozen dataclasses, not a pydantic model

Follows directly from C-6: `config` is imported by the numeric core, so nothing
on that path may touch pydantic. Issue 1A's "one frozen model" is satisfied by
`@dataclass(frozen=True, slots=True, kw_only=True)` plus a hand-written
`tomllib` loader.

The cost is hand-written parsing. It is repaid twice: `mypy --strict` needs no
plugin for the type the numeric core consumes, and the errors are better for a
file analysts edit by hand — `objective.risk_weight: missing key` rather than a
nested validation path.

### C-8 — mandate steps are published as metadata, not enforced

Ranges validate; each step goes out as `multipleOf` JSON-schema metadata so the
controls and any generated client get it, and a mandate landing between steps
is accepted.

§5's steps describe slider affordances, not properties of a mandate. Three
reasons not to enforce them:

- The API is also a programmatic surface, and §12 promises regression tests
  across releases; a caller sweeping the IRR hurdle in 0.1 steps is legitimate.
- Float modulo does not work: `1.15 % 0.05` is `0.049999999999999906`.
- It would break run immutability. §11 promises a run id reopens the exact
  result; if a future release tightened a step, re-validating a stored
  `RunRecord` would make old runs unreadable.

Reproducibility is unaffected — the mandate is hashed exactly as submitted.
Quantisation buys tidiness, not determinism.

### C-9 — `WarningCode` is an ordered `IntEnum` that crosses every boundary by name

The ordering is spec §5.4's severity ordering, ascending, so `sorted(codes)` is
display order and the most severe comes first. Values are banded with gaps, so
inserting a code never renumbers a neighbour.

The integer never leaves the process. `docs/api.md` pins
`{"code": "CAPACITY_BELOW_TARGET"}`, and the reason is durability rather than
readability: runs are immutable and addressable indefinitely (§11), so an
ordinal that shifted would silently rewrite the meaning of every run already
stored. `WarningCodeName` in `domain/results.py` serialises the name and
validates from it, and a test asserts no stored artefact contains the ordinal.

**The ordinal is refused on the way in as well.** Found in review: the first
version accepted `1010` and `"1010"` and resolved them through the enum, which
made the guarantee one-directional and worthless — a record written before a
code was inserted could be reinterpreted as a different warning after one was.

**`severity` cannot contradict its code.** It is a property of the code, not of
the occurrence; it appears on the wire because `docs/api.md` puts it there. It
may be omitted when constructing a warning in Python, in which case it is
derived, and a supplied value that disagrees is rejected. Otherwise a client
could render a blocking condition in an informational tone while the rest of the
system still treated it as blocking.

`WarningSeverity` has the two values `docs/api.md` allows, `alert` and `info`.
Blocking is not a severity: `WarningCode.disables_run` carries it, and it is
true for exactly `NO_CANDIDATES` and `LOCKS_EXCEED_CAPITAL` — the latter added
as a seventh code from §13 and `ui-contract.md` §3.6, where it is the 422 case.

### C-10 — `assumption_set_id` digests the calibration values only

`sha256(canonical_json(values))[:16]`, with `[meta]` excluded. Fixing a typo in
a label must not invalidate the comparability of every run stored against that
calibration, and `label`, `version`, `created_by` and `supersedes` move nothing.

Two further properties, both tested:

- Hashed from the **normalised structure**, never the file bytes, so
  reformatting or adding a comment does not invent a new calibration.
- Every numeric leaf is normalised to a float first, so `9` and `9.0` are one
  exit multiple. `json.dumps` would otherwise write them differently. Booleans
  are excluded from that normalisation, because `isinstance(True, int)` is true
  and a flag hashed as `1.0` would both read wrongly and collide.

Integer-typed *fields* stay strictly integers in the file — `debt_tenor_years =
18.5` is a typo worth naming rather than a value worth rounding.

**Open, for #3/#9:** `docs/api.md` §6.2 shows `"assumptionSetId": "default-2026"`,
which is the file stem, while §8.3's run record carries both `assumptionSetId`
and `assumptionSetHash`. A stem does not change when someone edits the file,
which defeats the audit trail. `AssumptionSet` therefore exposes all three —
`name` (the stem), `assumption_set_id` (the content digest) and `content_hash`
(the full prefixed digest) — so the wire can be mapped either way without a
schema change. 1A's recommendation is that `assumptionSetId` carry the content
digest.

### C-11 — `assumptions/` stays at the repository root and is copied into the wheel

The epic's file map puts it there and it is user-editable calibration: changing
a weight should be a reviewable diff, which it cannot be inside a package
directory. Files outside `src/` are not package data, so hatchling
`force-include` copies the directory to `terrafolio/_assumptions` at build time
— one source of truth in git, and an installed wheel that still works.

Resolution order in the loader: an explicit path, then
`$TERRAFOLIO_ASSUMPTIONS_DIR`, then upward from the working directory, then
upward from the installed package, then the packaged copy.

### C-12 — undefined values become `None` at the pydantic boundary, once

The numeric core works in numpy and represents an undefined IRR as `NaN` with a
defined-mask. The conversion to `None` happens exactly where arrays become
models, and from there it serialises to JSON `null` and renders as an em dash.

To make that boundary enforceable rather than conventional, `allow_inf_nan` is
**off** on every model. It defaults to *on* in pydantic, which would let a file
carry `NaN` revenue — and once a NaN can mean "corrupt input" as well as
"undefined IRR", the distinction §13 requires is gone. A NaN reaching a result
model now fails at the boundary that owes the conversion, rather than becoming a
plausible zero three layers later.

### C-13 — immutability reaches the contents, not just the attributes

`frozen=True` freezes attribute *assignment* and nothing more. Both container
kinds on these models needed closing:

- **Series are tuples, not lists.** `file.statements.cashFlow.fcfe.append(0.0)`
  would otherwise succeed on a "frozen" model, and the pipeline snapshot hash
  would stop describing what is in memory. Tuples serialise to JSON arrays
  identically and cost nothing.
- **Mappings are frozen after validation.** `provenance.fields`,
  `aggregates.countryShares` and `provenance.fileHashes` were plain `dict`s, so
  `file.provenance.fields.pop(...)` worked. They are now declared as
  `Mapping[...]` — so type checkers withhold `pop` and `__setitem__` — backed by
  a `mappingproxy` at runtime and serialised back to a `dict`. The two result
  mappings are also sorted on validation, because two identical results should
  not differ by insertion order.

Found in review; the first version of this issue claimed immutability it did not
have.

### C-14 — numbers are validated strictly, not coerced

Pydantic's default lax mode accepts `"180"` where a `float` is declared, and
converts `true` to `1.0`. For a file an analyst writes by hand — or exports from
a spreadsheet, where a column formatted as text is an ordinary accident — that
turns a type error into a *plausible financial input* that satisfies every
tie-out.

All numeric fields on files, mandates and results are therefore strict. `int`
for `float` is still accepted, because JSON has one number type and `34` is how
a spreadsheet writes `34.0`; everything else is refused. Integer fields reject
`18.5`, `18.0` and `"18"` alike, and booleans reject `1` and `"true"`.

Model-wide `strict=True` was not used: it also rejects the string forms of
enums and dates, which are how `technology` and `preparedOn` legitimately
arrive.

### C-15 — sign conventions are enforced, not just documented

`docs/pipeline-schema.md` §2 states `opex`, `depreciation`, `interestExpense`,
`taxExpense`, `capex` and `debtRepayment` as positive magnitudes that carry
their sign through the identity consuming them, and the debt schedule as all
positive. Those lines now reject negatives, along with the physicals and the
PP&E balance.

This is worth enforcing at the schema rather than leaving to the tie-outs: the
identities are all linear, so a negative `opex` satisfies every one of them
while making EBITDA — and therefore the whole portfolio's earnings — look better
than it is. `revenue`, `ebitda`, `ebit`, `pbt`, `netIncome`, `fcfe` and `dscr`
stay signed, matching what the schema document annotates.

---

## The narrowed assumption set

Issue 1A asks for this to be recorded explicitly, and it is the consequence of
the input model rather than a defect.

With each project file declaring its own tax rate, debt rate, debt tenor,
depreciation life, escalators and degradation, **per-fund rate and escalator
configuration has narrowed**. §9.4's promise that rates and escalators are
configuration now applies to a smaller surface: what remains genuinely
portfolio-level is exit multiples, the LCOE real discount rate, the CO₂ factor,
the nine objective weights with every floor, clamp and normaliser, the GA
parameters, the risk-appetite pairs, the validator's tolerances and plausibility
bands, and the generator's parameters.

The per-project rates have not become unconfigurable — they have become *inputs*
that the investment team does not turn, which is what makes the cross-file
dispersion report load-bearing rather than a nicety (A-7, A-8).

---

### C-16 — `holdings` is the run's own candidate snapshot

Conforming to 1B's A-14 rather than a decision of 1A's, recorded here because
1A's models changed shape after it landed. `holdings` carries **every project in
the run's eligible candidate set** — each with the full per-project scalar
record of `GET /pipeline`, plus `selected` and `locked` — not one row per
selected project projected onto the sixteen §7.4 columns.

The reason is §11's "a run ID reopens the exact result": §7.4's *show all
candidates* toggle, §7.3's map and §7.5's drawer all need more than the selected
rows, and none of them can be reconstructed by merging against a live pipeline
that has since moved. `ProjectScalars` models the shared record so `GET
/pipeline` and a stored holding cannot drift apart, and `Holding` is that plus
the two flags.

Three consistency rules come with it, all stated in `docs/api.md` §8.2 and none
of them previously checked: holdings are ordered by `id`, the `selected` flags
agree with `selectedIds`, and the two cash-flow series carry their documented
lengths — thirty years for `cashflow30Y_m`, the mandate's hold period for
`cashflowHold_m`. A run still in flight has no aggregates and is exempt.

---

### C-17 — a result model is bound by the same domains as the file it describes

`ProjectScalars` re-states about twenty fields that `ProjectFile` already
declares, and the first version of it carried none of their constraints: a
stored holding could put a site at latitude 999, a contracted share at 7, an id
of `!!` or a `countryCode` of `Germany`. §7.3's map plots those coordinates and
the mandate's country screen matches that code, so the constraints matter as
much on the way out of the store as on the way in.

Both sides now draw their bounds from `domain/file_bounds.py`, and the shared
`Fraction`, `Magnitude` and `Positive` types in `domain/fields.py` carry the
repeated ones. The two provenance rules are the same on both sides too: all
seven groups required, and senior debt not above total cost.

Found in review, along with four consistency gaps in validators added the round
before — duplicate holding ids passing an ordering check that used `sorted`,
the selected-ids check short-circuited by `if self.holdings and`, completeness
read from `aggregates` rather than `status`, and a naive `createdAt` accepted
into an audit trail that is ordered by time.

### C-18 — a guard that can be switched off quietly is not a guard

Three holes in the two guards, all found in review:

- The `# structural:` escape was matched as a substring of the raw line, so
  `S = "# structural: ..."` — or a docstring describing the rule — exempted its
  own line. It is tokenised now, and only a real comment counts.
- `scan_tree` decided tier A membership with `key.split("/")[0]`, so a module at
  `optimiser.py` rather than `optimiser/ga.py` was read as a package named
  `optimiser.py` and dropped out of the strictest tier. Issue 2A adds `cli.py`
  at the package root.
- Tier C called `int(value)` before testing finiteness, so `1e999` — which
  Python parses to `inf` — aborted the whole scan with `OverflowError` instead
  of reporting a finding.

And one in the import guard: `transitive_third_party` followed edges from the
starting package, but nothing names `terrafolio` as an import target, so a
third-party import added to `terrafolio/__init__.py` was invisible — although
every `import terrafolio.optimiser` executes it. The walk is seeded with the
package root now.

The two files also defined "the numeric core" twice, with different members.
The literal scan derives its set from the import rule's now, so the difference —
`pipeline` and `generate` consume the assumption set but may import pydantic —
is stated once and asserted.

### C-19 — `nan` and `inf` are not calibration values

TOML has both as literals. A non-finite weight would propagate silently through
every fitness comparison, and it also made the assumption-set digest fail inside
`json.dumps` with `Out of range float values are not JSON compliant` — naming
neither the key nor the file, in a loader whose entire error contract is to name
the key path. Rejected now, before the digest, with the path.

Two neighbouring gaps closed with it: an empty market table loaded as a valid
assumption set, and a market with a capacity factor but no baseload price — or
the reverse — was accepted and failed as a `KeyError` mid-run.

---

## Open questions from 1A

Both add a line item that participates in a new tie-out identity, which the
rewritten §8 reserves for a human. Neither is blocking; the schema stands
without them.

### N-1 — Should `incomeStatement.revenue` be split into contracted and merchant?

Tie-out would be `revenue = contractedRevenue + merchantRevenue`.

Issue 2A must compute contracted share "revenue-weighted over life, not simply
`1 − ppaShare`". With C-2's escalators present it *can* be re-derived from
declared assumptions, so this is not a blocker — but re-deriving means
reconstructing the whole price path inside `optimiser/`, across the import
boundary from the model that produced it, with its own rounding. Storing the
split costs two series and removes the duplication.

### N-2 — Should the balance sheet be complete?

`cash`, `seniorDebtBalance`, `shareCapital` and `retainedEarnings` alongside
`ppe`, with `ppe + cash = seniorDebtBalance + shareCapital + retainedEarnings`.

1B deferred this deliberately and that is a defensible v1 scope. Raised only
because epic §2 says files carry a balance sheet and 1C reports it can already
emit one that balances to 4.8e-13 — so the cost looks close to zero, and the
"balance sheet" in a file is currently a single line.
