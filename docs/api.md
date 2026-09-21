# Wire contract

Every endpoint, field by field. **Normative** for issue #9, which builds the API, the runner and the
exports, and for #11, which consumes it. #7 owns the store behind it and #12 the regression tests
against it.

If you need a field that is not here, add it here first. Changing a name or a type in this document
is a shared-contract change: comment on the epic (issue #1) before doing it.

Section numbers cited as §n refer to [`spec.md`](spec.md); decisions as A-n to
[`decisions.md`](decisions.md).

---

## 1. Conventions

These hold for every request and response. Getting one wrong is a silent bug, not a 400.

### 1.1 Naming and case

JSON is **camelCase** throughout, matching the project file schema. Python models are snake_case
with an alias generator; the wire is the contract, not the Python attribute names.

### 1.2 Units — the `_m` suffix

Files carry **€m**; `ProjectArrays` and everything downstream carry **euros and GWh in float64**;
the API converts back to €m at its boundary. Those are the only two conversion points in the system
(epic §5, [`pipeline-schema.md` §2](pipeline-schema.md#2-units-and-conventions)).

**Every money field is in €m and its name ends `_m`.** A money field without the suffix is a defect.
No other field carries a unit suffix except `Mw`, `Gwh` and `Kt`, which are part of the noun.

**Shares, rates and ratios are fractions of one, never percentages.** `solarShare: 0.45`,
`minLeverage: 0.6`, `equityIrr: 0.124`. The UI multiplies by 100; the wire never does. A percentage
on the wire is a defect, and one that looks plausible for two orders of magnitude.

### 1.3 Ordering

**Every array of projects is ordered by `id` ascending.** The optimiser's PRNG draws are indexed by
position, so this is a determinism requirement, not tidiness (epic §5). It applies to `projects`,
`selectedIds`, `lockedIds`, `excludedIds` and every holdings array.

### 1.4 Undefined IRR

An IRR whose cash-flow series has no sign change is **`null`**. Never `0`, never `0.0`, never
omitted, never a sentinel. It is excluded from every weighted average, and the UI renders it as an
em dash (§13, A-6). The chain is engine `NaN` → API `null` → UI `—`, and it is easy to lose at each
hand-off. The same applies to `moic` and to `paybackYear`.

### 1.5 The two cash-flow series

They are **never** given the same name, and neither is derived from the other by slicing (A-6):

| Field | Length | Terminal value | Drives |
|---|---|---|---|
| `cashflow30Y_m` | exactly 30 | **no** | The §7.2 chart, the `30-year FCFE` tile, `cashflow.csv` |
| `cashflowHold_m` | `mandate.holdYears` | **yes**, added into the last element | `equityIrr` and `moic`, nothing else |

Conflating them is the single most likely silent bug in the feature, and it produces numbers that
look right.

### 1.6 What is mandate-dependent

Nothing mandate-dependent is stored in a project file, and no cache of it is keyed without
`holdYears` (epic §5). On the wire that means **`GET /pipeline` takes `holdYears`** and its ETag
includes it — see [§2](#2-get-pipeline). `equityIrr`, `moic`, `terminalValue_m` and `paybackYear`
move with the hold period; `minDscr`, `lcoe`, `gearing` and `equity_m` do not.

### 1.7 Errors

All errors share one body:

```json
{ "error": { "code": "…", "message": "…", "detail": { } } }
```

`code` is a stable machine token; `message` is one sentence fit to show a user; `detail` carries
whatever the caller needs to act. Codes are listed with the endpoints that raise them and
summarised in [§11](#11-status-codes).

---

## 2. `GET /pipeline`

The current candidate set with its derived economics and a snapshot hash. Cacheable; the hash pins a
run to the data it saw (§11).

**Query**

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `holdYears` | integer 5–30 | 10 | Sets the exit year for `equityIrr` and `moic`. Part of the ETag. |
| `assumptionSetId` | string | the active set | Exit multiples, LCOE rate, CO₂ factor, caps. |

**Scalars only, never the 30-year arrays.** 300 projects × ~40 scalars is about 250 KB of JSON; the
arrays would be thirty times that, on an endpoint the mandate screen hits on every load. §7.5's
detail sheet needs only scalars. The arrays live at
[`GET /projects/{id}/statements`](#4-get-projectsidstatements).

**200**

```json
{
  "pipelineHash": "sha256:9f2c…",
  "assumptionSetId": "default-2026",
  "engineVersion": "1.0.0",
  "baseYear": 2027,
  "holdYears": 10,
  "projectCount": 300,
  "projects": [ … ]
}
```

Each entry of `projects`, ordered by `id`:

| Field | Type | Notes |
|---|---|---|
| `id` `name` | string | |
| `country` `countryCode` `iso3` | string | |
| `lat` `lon` | number | Degrees. Required by the map. |
| `technology` | `solar` \| `onshore_wind` \| `offshore_wind` | |
| `stage` | `greenfield` \| `ready_to_build` \| `construction` | |
| `capacityMw` | number | |
| `codYear` | integer | |
| `netCapacityFactor` | number | Fraction. |
| `annualGenerationGwh` | number | P50 in a full operating year. |
| `opexPerKwYear` | number | €/kW/yr. |
| `ppaShare` `ppaTenorYears` `ppaPrice` | number | Fraction, years, €/MWh. |
| `countryBaseloadPrice` `captureFactor` `capturePrice` | number | €/MWh, fraction, €/MWh. |
| `developmentRiskScore` | number | 1.0–5.0. |
| `gridSecured` `omContracted` | boolean | |
| `currency` | string | ISO 4217. The **revenue** currency, which drives the EUR-only screen and the drawer's `hedge required` flag. Statements are always euros (`pipeline-schema.md` A-12). |
| `totalCapex_m` `seniorDebt_m` `equity_m` | number | €m. |
| `gearing` `maxGearing` | number | Fractions. |
| `capexPerKw` | number | €/kW. |
| `debtRate` `debtTenorYears` | number, integer | The file's declared senior terms. §7.5's drawer renders `€{n}m at {pct}, {rate}, {tenor}y`, so both have to be on the wire. |
| `opexPerKwYear` | number | €/kW/yr. Drawer. |
| `lcoe` | number | €/MWh at the assumption set's real rate. Mandate-independent. |
| `minDscr` | number \| null | Over the debt life, **excluding the ramp year** (§9.4). `null` where the project carries no debt. |
| `thirtyYearFcfe_m` | number | Undiscounted sum, no terminal value. |
| `equityIrr` | number \| null | **At `holdYears`.** `null` per [§1.4](#14-undefined-irr). |
| `moic` | number \| null | At `holdYears`. |
| `paybackYear` | integer \| null | |
| `provenance` | object | `estimateBasis` and `confidence` per group, for the detail sheet. |

**ETag and 304.**

```
ETag: sha256(pipelineHash ‖ assumptionSetId ‖ engineVersion ‖ holdYears)
```

**Not `pipelineHash` alone.** Conflating them makes an assumption-set change look like a pipeline
change — and, worse, leaves a client holding IRRs computed at a different hold period while the hash
says nothing moved. All four components are required.

`If-None-Match` matching the current ETag returns **304** with no body.

---

## 3. `GET /pipeline/status`

Load and validation state. The mandate screen shows a banner from it; #12 asserts against it.

**200**

```json
{
  "pipelineHash": "sha256:9f2c…",
  "loadedAt": "2026-09-21T09:14:02Z",
  "fileCount": 300,
  "loadedCount": 298,
  "durationMs": 1180,
  "rejected": [
    { "file": "P217.json", "check": "debt.closing[last] = 0",
      "year": 2056, "residual_m": 0.42, "message": "…" }
  ],
  "warnings": [
    { "id": "P104", "check": "plausibility.gearing",
      "message": "Gearing 0.78 exceeds the 0.72 ready-to-build ceiling." }
  ],
  "dispersion": {
    "taxRate":       { "count": 298, "median": 0.2,  "min": 0.19, "max": 0.25, "outliers": ["P104"] },
    "debtRate":      { "count": 298, "median": 0.055, "min": 0.048, "max": 0.071, "outliers": [] },
    "debtTenorYears":{ "count": 298, "median": 18,   "min": 15,   "max": 20,   "outliers": [] }
  }
}
```

`rejected` files did not load — a tie-out failed. `warnings` loaded normally: plausibility checks
and the dispersion report **warn and never block**, because one stale file must not stop all work
(A-7). `residual_m` is the signed miss against the €0.01m / 0.1% tolerance, so a reader can see
whether a rejection is a typo or a broken model.

---

## 4. `GET /projects/{id}/statements`

The 30-year arrays for one project, exactly as
[`pipeline-schema.md` §5](pipeline-schema.md#5-statements) defines them, in €m and GWh. Mandate-
independent, so no `holdYears`.

**200** — `{ "id", "assumptions", "years", "physicals", "incomeStatement", "cashFlow",
"debtSchedule", "balanceSheet", "ratios", "provenance" }`, with `ratios.dscr` null outside the debt
life.

`assumptions` is the file's own declared block — `baseYear`, `taxRate`, `depreciationYears`,
`debtRate`, `debtTenorYears` — so a caller can interpret the arrays without a second request.
`baseYear` is the same for every project in a pipeline (`pipeline-schema.md` §4.6.1).

**404** `PROJECT_NOT_FOUND`.

---

## 5. `POST /mandate/preview`

Recomputes the §5.4 feasibility figures for a mandate without starting a run.

The screen itself computes these **client-side**, because §12 budgets the feedback at under 100 ms
and a round trip cannot promise that. This endpoint exists so the two implementations can be held to
the same answer: #12's screen-parity tests assert that the client and the server agree on every
field for a fixed set of mandates. If they diverge, the client is wrong.

**Request** — `{ "mandate": {…}, "lockedIds": [], "excludedIds": [] }`, mandate as [§6.1](#61-the-mandate-object).

**200**

```json
{
  "eligibleCount": 214,
  "totalCount": 300,
  "eligibleCapacityMw": 24180,
  "eligibleEquity_m": 6420,
  "eligibleSolarShare": 0.52,
  "eligibleGearing": 0.66,
  "lockedEquity_m": 0,
  "warnings": [ { "code": "CAPACITY_BELOW_TARGET", "severity": "alert",
                  "message": "Eligible pipeline is 1,180 MW — below the 1,500 MW target." } ],
  "runnable": true
}
```

and, when the locks alone cannot be funded:

```json
{
  "eligibleCount": 214, "totalCount": 300,
  "lockedEquity_m": 1420,
  "warnings": [ { "code": "LOCKS_EXCEED_CAPITAL", "severity": "blocking",
                  "message": "Locked projects need €1,420m of equity against €1,200m available. Release a lock to run.",
                  "detail": { "availableCapital_m": 1200, "excess_m": 220,
                              "lockedIds": ["P01","P17","P44"] } } ],
  "runnable": false
}
```

`warnings` are ordered by the §5.4 severity, which is **not** the order the design mockup emits them
in (A-5). `severity` is `blocking`, `alert` or `info`. Strings are pinned in
[`ui-contract.md` §3.5](ui-contract.md#35-warning-strings).

| Code | Severity | |
|---|---|---|
| `NO_CANDIDATES` | blocking | Nothing passes the screens. |
| `LOCKS_EXCEED_CAPITAL` | blocking | The locked projects alone need more equity than is available. `detail` carries `lockedEquity_m`, `availableCapital_m`, `excess_m` and `lockedIds`. |
| `CAPACITY_BELOW_TARGET` | alert | |
| `LEVERAGE_UNREACHABLE` | alert | |
| `SOLAR_MIX_UNREACHABLE` | info | |
| `CAPITAL_UNDERUSED` | info | |
| `LOCKS_PRESENT` | info | |

**`runnable` is `false` if and only if some warning is `blocking`**, and those are exactly the two
conditions `POST /optimisations` answers with `422`. Preview and run must agree: a preview that
reports `runnable: true` for a mandate the run rejects would light up a button that cannot work, and
would break the client/server parity this endpoint exists to establish.

Every other warning is advisory, because the user is allowed to run an infeasible-looking mandate
and see how close the optimiser gets (§5.4).

---

## 6. `POST /optimisations`

Starts a run. **202**, never a result: a Standard run takes seconds and the client watches the
stream.

### 6.1 The mandate object

| Field | Type | Range | Default |
|---|---|---|---|
| `availableCapital_m` | number | 200–4000 | 1200 |
| `capacityTargetMw` | number | 200–4000 | 1500 |
| `solarShare` | number | 0–1 | 0.45 |
| `targetIrr` | number | 0.06–0.18 | 0.11 |
| `holdYears` | integer | 5–30 | 10 |
| `countries` | string[] | ISO alpha-2 | all 14 |
| `stages` | string[] | the three stage enums | all three |
| `minLeverage` | number | 0–0.85 | 0.6 |
| `minDscr` | number | 1.0–2.0 | 1.25 |
| `maxMerchantShare` | number | 0–1 | 0.35 |
| `maxCountryShare` | number | 0.1–1 | 0.35 |
| `maxProjectShare` | number | 0.05–1 | 0.15 |
| `codFrom` `codTo` | integer | 2027–2033 | 2027, 2032 |
| `riskAppetite` | `low` \| `balanced` \| `high` | — | `balanced` |
| `gridSecuredOnly` | boolean | — | false |
| `eurRevenueOnly` | boolean | — | false |
| `omContractedOnly` | boolean | — | false |

`codFrom > codTo` is **400** `INVALID_MANDATE`. An empty `countries` or `stages` is accepted and
yields `NO_CANDIDATES` on preview; it is **422** here, because there is nothing to search.

### 6.2 Request

```json
{
  "mandate": { … },
  "lockedIds": [], "excludedIds": [],
  "effort": "standard",
  "seed": null,
  "pipelineHash": "sha256:9f2c…",
  "assumptionSetId": "default-2026"
}
```

`effort` is `fast` (50 × 35), `standard` (90 × 60) or `exhaustive` (160 × 110) — §10.1.

`seed` omitted or `null` means the server draws one and **records it**. A run with no recorded seed
is not a valid run (epic §5), so the resolved seed always comes back in the result.

`pipelineHash` is the snapshot the client was looking at. If it no longer matches, the run is
**409** rather than silently running against different data.

### 6.3 Responses

**202**

```
Location: /optimisations/01JB2Q…
```

```json
{ "runId": "01JB2Q…", "status": "queued",
  "streamUrl": "/optimisations/01JB2Q…/stream", "totalGenerations": 60 }
```

**409** `PIPELINE_MOVED` — the pipeline changed since `pipelineHash` was issued. `detail` carries
`currentPipelineHash`. Stored runs keep their own snapshot; only a *new* run is blocked, and the UI
warns that the pipeline moved (§13).

**422** `LOCKS_EXCEED_CAPITAL` — the locked projects alone require more equity than
`availableCapital_m`. §13 requires this to block rather than warn, and to say which locks to
release, so `detail` carries:

```json
{ "lockedEquity_m": 1420, "availableCapital_m": 1200,
  "lockedIds": ["P01","P17","P44"], "excess_m": 220 }
```

**422** `NO_CANDIDATES` — no project passes the screens.

**400** `INVALID_MANDATE` — a field out of range, or `codFrom > codTo`. `detail.field` names it.

---

## 7. `GET /optimisations/{id}/stream`

Server-sent events for a running optimisation. `Content-Type: text/event-stream`,
`Cache-Control: no-store`. At least one `generation` event every **100 ms** (§10.3), driven by real
per-generation data from the engine and never a simulated animation (§6).

These names keep the technical vocabulary deliberately: the stream is not user-facing, and §14's ban
applies to copy, not to the protocol (A-11). The UI relabels for display.

```
event: generation
data: {"generation":7,"totalGenerations":60,
       "bestFitness":6.2121,"meanFitness":2.0041,
       "best":{"projectCount":11,"capacityMw":1661,"equity_m":1154,"blendedIrr":0.121}}
```

| Field | Type | Notes |
|---|---|---|
| `generation` | integer | 1-based. |
| `totalGenerations` | integer | Constant for the run. |
| `bestFitness` `meanFitness` | number | **Quantised to 6 dp**, as the engine compares them (epic §5). |
| `best.blendedIrr` | number \| null | The equity-weighted per-project blend used during the search, not the solved portfolio IRR. `null` if every contributor is undefined. |

```
event: done
data: {"runId":"01JB2Q…","status":"succeeded","durationMs":2483,"resultUrl":"/optimisations/01JB2Q…"}
```

```
event: failed
data: {"runId":"01JB2Q…","error":{"code":"ENGINE_ERROR","message":"…"}}
```

A client joining late receives the events already emitted, then continues live, so a reconnect does
not lose the curve. **404** `RUN_NOT_FOUND`; **410** `RUN_EXPIRED` if the stream has closed and the
result is available instead.

---

## 8. `GET /optimisations/{id}`

The stored result. Runs are immutable and addressable: the same id always reopens the same result,
including the mandate that produced it (§11).

**200**

```json
{
  "runId": "01JB2Q…",
  "runRef": "A-4",
  "status": "succeeded",
  "createdAt": "2026-09-21T09:22:11Z",
  "durationMs": 2483,
  "mandate": { … },
  "lockedIds": [], "excludedIds": [],
  "effort": "standard",
  "selectedIds": ["P01","P09","P23"],
  "aggregates": { … },
  "holdings": [ … ],
  "cashflow30Y_m": [ -28.93, … ],
  "cashflowHold_m": [ -28.93, … ],
  "convergence": [ { "generation": 1, "bestFitness": -12.4, "meanFitness": -31.2 }, … ],
  "provenance": { … }
}
```

### 8.1 `aggregates`

One field per §7.1 tile, plus what the tiles are compared against.

| Field | Type | Notes |
|---|---|---|
| `projectCount` `solarCount` `windCount` | integer | |
| `capacityMw` | number | |
| `solarShare` | number | Capacity-weighted. |
| `totalCapex_m` `seniorDebt_m` `equity_m` | number | |
| `gearing` | number | `seniorDebt ÷ totalCapex`. |
| `capitalDeployed` | number | `equity ÷ availableCapital`. |
| `equityIrr` | number \| null | Solved **once**, on the winning chromosome, over `cashflowHold_m` (§10.3). |
| `moic` | number \| null | Over the same truncated series. |
| `weightedLcoe` | number | Generation-weighted, €/MWh. |
| `annualGenerationGwh` | number | |
| `co2AvoidedKt` | number | At the assumption set's factor, never a literal. |
| `merchantShare` | number | Capex-weighted. |
| `weightedRiskScore` | number | Capex-weighted. |
| `worstMinDscr` | number \| null | The lowest project-level min DSCR. |
| `countryShares` | object | `countryCode` → capex share, ordered by code. |
| `largestCountryCode` `largestCountryShare` | string, number | |
| `thirtyYearFcfe_m` | number | `sum(cashflow30Y_m)`. |
| `fitness` | number | The §10.2 score of the winning chromosome, 6 dp. |

Every field that a mandate constrains is reported **with its breach**, not hidden: a locked set that
breaches a concentration cap still runs, and the breach surfaces on the tile (§13).

### 8.2 `holdings` — the run's own candidate snapshot

**One entry per project in the run's eligible candidate set**, ordered by `id`, not merely per
selected project. Each carries **the full per-project scalar record of
[`GET /pipeline`](#2-get-pipeline)** — including `lat`, `lon`, the PPA and capture terms, the debt
terms and `provenance` — plus two flags:

| Field | Type | Notes |
|---|---|---|
| `selected` | boolean | In `selectedIds`. |
| `locked` | boolean | Was forced into the population for this run. |

This makes a stored run **self-contained**, which is what §11's "a run ID reopens the exact result"
actually requires. Three things on the portfolio screen need more than the selected rows:

- §7.4's *Show all candidates* toggle shows the eligible set "so the user can see what the optimiser
  rejected". Merging against the live `GET /pipeline` cannot reproduce it once the pipeline has
  moved, and §13 explicitly expects a run reopened after a pipeline change to keep its snapshot.
- §7.3's map needs `lat`/`lon` for every selected site. Nothing else on the result carries them.
- §7.5's drawer needs the capture price, PPA terms, opex, payback and debt terms.

A project deleted from the pipeline after the run still appears here, with the figures the run saw.

Size: 500 candidates × ~35 scalars is roughly 400 KB, on an endpoint fetched once per result rather
than on every page load. `GET /pipeline` stays scalars-only for the reason in [§2](#2-get-pipeline);
this is the one place the trade goes the other way, and it buys immutability.

`holdings.csv` is `holdings` filtered to `selected`, projected onto the sixteen §7.4 columns, so the
table and the export cannot disagree (§7.7).

### 8.3 `provenance` — the run record

Required by §12's reproducibility and audit trail. Every one of these is recorded, and a run missing
`seed` is not a valid run (epic §5).

| Field | Notes |
|---|---|
| `seed` | The **resolved** seed, whether supplied or drawn. |
| `pipelineHash` | The snapshot the run saw. |
| `fileHashes` | `id` → content hash, per project file. |
| `assumptionSetId` `assumptionSetHash` | |
| `engineVersion` | |
| `numpyVersion` | |
| `blasThreads` | Thread count at run time — it changes reduction order and so the result. |
| `pythonVersion` `platform` | |

Re-running with the same mandate, pipeline hash, assumption set and seed must reproduce the result
exactly. #12 asserts this across releases.

**404** `RUN_NOT_FOUND`. A run still in flight returns **200** with `status: "running"` and no
`aggregates`.

---

## 9. Exports

Generated **server-side**, from the stored result, so they match it exactly (§11).

| Endpoint | Content-Type | Contents |
|---|---|---|
| `GET /optimisations/{id}/holdings.csv` | `text/csv` | All sixteen §7.4 columns, **selection only**, ordered by `id`. |
| `GET /optimisations/{id}/cashflow.csv` | `text/csv` | 30 rows: `year`, `fcfe_m`, from `cashflow30Y_m` — **no terminal value** (A-6). |
| `GET /optimisations/{id}/pack` | `text/html` | The §12 offline committee pack. |

Both CSVs carry the run reference and the mandate in a comment header, and use `\r\n` with a UTF-8
BOM so they open correctly in Excel. Money columns are named `_m` and carry raw numbers, not
formatted strings — a CSV is for a spreadsheet, not for reading.

The pack is **a single self-contained HTML file that opens with no network access**: styles, fonts,
map geometry and data inlined, no external reference of any kind. That is the §12 requirement, and a
pack that fetches anything fails it.

---

## 10. `GET /assumptions`

The active assumption set, so the UI can show what a run used and #12 can assert a run against it.

`{ "id", "hash", "createdAt", "exitMultiples", "lcoeDiscountRate", "objectiveWeights", "riskCaps",
"co2FactorTPerMwh", "validation", "generator" }` — the groups enumerated in
[`decisions.md` A-8](decisions.md#a-8--the-narrowed-assumption-set). Every rate, weight, floor,
clamp, tolerance and band the engine uses appears here and nowhere else in code (§9.4, §10.2).

---

## 11. Status codes

| Code | When | Error code |
|---|---|---|
| `200` | Success with a body. | — |
| `202` | Run accepted. `Location` carries the run URL. | — |
| `304` | `If-None-Match` matches the current ETag. | — |
| `400` | Malformed request, or a mandate field out of range. | `INVALID_MANDATE` |
| `404` | No such run or project. | `RUN_NOT_FOUND`, `PROJECT_NOT_FOUND` |
| `409` | `pipelineHash` no longer matches. | `PIPELINE_MOVED` |
| `410` | The stream has closed; read the result instead. | `RUN_EXPIRED` |
| `422` | Well-formed but unrunnable: locks exceed capital, or nothing passes the screens. | `LOCKS_EXCEED_CAPITAL`, `NO_CANDIDATES` |
| `500` | Engine or store failure. | `ENGINE_ERROR`, `STORE_ERROR` |

`409` and `422` are the two that carry product meaning rather than plumbing, and both are §13 cases:
a pipeline that moved mid-session, and locks that alone exceed available capital.

---

## 12. Not in v1

`GET /pipeline` and the run endpoints are unauthenticated in this backlog. §11 calls a run id
shareable while §12 requires role-based visibility by fund; these conflict, and the resolution is
recorded as [Q-7](decisions.md#q-7--shareability-versus-role-based-visibility). The run id is
treated as an internal identifier, **not a bearer token**, and authentication needs its own issue
before this is deployed anywhere real.
