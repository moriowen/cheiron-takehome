# ClinicalTrials.gov API v2: what data we can use

All numbers below come from live calls made on 2026-11-12 against API version 2.0.5. The "missing" rates come from a sample of 1,000 studies matching `query.cond=lung cancer`. Treat them as rough guides, not as global rates.

## Basics

- Base URL: `https://clinicaltrials.gov/api/v2`. No auth.
- The registry holds 611,552 studies. `GET /version` returns `dataTimestamp`, and the data refreshes daily. Put that timestamp in every response so a result can be traced to a data snapshot.
- Responses carry no rate-limit headers, and a 429 carries no `Retry-After`. Measured limit: about 10 requests in a burst, then about 1 request/s sustained. At 2 requests/s, 35% of requests got HTTP 429; at 1 request/s, 45 of 45 succeeded. A 429 clears in about 9 s. A page of 1,000 slim records takes about 0.9 s, so a sequential fetch already runs close to the limit.
- `filter.advanced` can partition a result set exactly. `AREA[Field]MISSING` matches records with no value. Phase needs exclusive clauses, because a Phase 1/2 trial matches both `AREA[Phase]PHASE1` and `AREA[Phase]PHASE2`; `AREA[Phase]PHASE2 AND NOT AREA[Phase]PHASE1 AND NOT AREA[Phase]PHASE3` is Phase 2 alone. For lung cancer, the 9 phase buckets sum to 14,719 (the total), and so do the 14 overall-status, 3 study-type, and 9 sponsor-class buckets. Intervention-type buckets sum to more (17,986), because one trial can have several types.
- `GET /api/int/suggest?input=X&dictionary=D` is the autocomplete behind the website's search box. It is not in the v2 docs. Dictionaries: `Condition`, `InterventionName`, `LeadSponsorName`, `LocationFacility`. It tolerates typos: `pembrolizimab` → `pembrolizumab`, `alzhiemer` → `alzheimer`, `pfize` → `Pfizer`; gibberish (`xqzvmab`) returns `[]`. Some suggestions escape brackets (`\[Keytruda\]`).
- The registry has its own typos: `query.intr=pembrolizimab` matches 3 trials (`pembrolizumab`: 3,006). So "0 matches" alone does not catch a misspelling.
- `GET /studies/enums` lists every enum value (statuses, phases, sponsor classes, intervention types). Overall status has 14 values, including expanded-access ones such as `AVAILABLE`. The sponsor class list includes `AMBIG`.

## Endpoints

| Endpoint | Use for us |
|---|---|
| `GET /studies` | Search. Returns study records, `totalCount` (with `countTotal=true`), and `nextPageToken`. This is the main endpoint. |
| `GET /studies/{nctId}` | One full record (median about 10 KB, p99 about 150 KB). Useful for fetching a richer citation excerpt. |
| `GET /studies/metadata` | The full field tree with types and enum names. Source for our field registry. |
| `GET /studies/search-areas` | Which fields each `query.*` parameter searches. |
| `GET /studies/enums` | All enum values (phase, status, intervention type, sponsor class, and so on). |
| `GET /stats/field/values?fields=X` | Value counts for a field over the whole registry. It rejects `query.*` parameters, so it cannot count a filtered subset. Useful only for global questions and for building value lists. |
| `GET /stats/size` | Record size distribution. |

## Search parameters on `/studies`

Text queries (each searches a fixed set of fields, with synonym expansion):

| Param | Searches |
|---|---|
| `query.cond` | Condition, title, condition MeSH terms and ancestors, keywords |
| `query.intr` | Intervention name, other names, type, arm labels, intervention MeSH terms and ancestors |
| `query.spons` | Lead sponsor name, collaborator names, org name |
| `query.locn` | City, state, country, facility, zip |
| `query.titles`, `query.outc`, `query.id`, `query.patient` | Titles, outcomes, IDs, a patient-oriented mix |
| `query.term` | Everything above plus summary text. Broadest and least precise. |

Synonym expansion works well for drugs. `query.intr=pembrolizumab`, `Keytruda`, and `MK-3475` each return exactly 3,006 studies. `query.term=pembrolizumab` returns 3,146 because it also matches summary text. So `query.intr` and `query.cond` are the precise choices, and we should prefer them over `query.term`.

Filters:

- `filter.overallStatus=RECRUITING,COMPLETED` (comma list of the Status enum)
- `filter.advanced` takes Essie expressions on any field: `AREA[Phase]PHASE3 AND AREA[StartDate]RANGE[2020-01-01,MAX]`, `AREA[LeadSponsorClass]INDUSTRY`. An unknown field returns a clear 400 error (`Unknown area name: 'Bogus'`), which is useful for validation.
- `filter.geo=distance(lat,lon,50mi)`
- `filter.ids=NCT...,NCT...`
- `aggFilters=phase:3,status:rec` (shorthand filters)

Paging and shape:

- `pageSize` max is 1000. Larger values are silently capped at 1000.
- `pageToken` from the previous page's `nextPageToken`.
- `fields=NCTId,Phase,StartDate,...` returns only those fields. A 1,000-record page with 15 fields is about 1.1 KB per study and took about 1 second.
- `sort=StartDate:desc` and similar. Default order is relevance.
- `format=csv` exists, but it uses different column names from `fields`. JSON is simpler.

## Scale and what it means for fetching

| Query | Matching studies |
|---|---|
| `query.cond=diabetes` | 24,647 |
| `query.cond=breast cancer` | 17,009 |
| `query.cond=lung cancer` | 14,719 |
| `query.cond=alzheimer` | 4,102 |
| `query.intr=pembrolizumab` | 3,006 |
| `query.intr=aspirin` | 2,210 |

A broad condition needs about 25 pages (about 25 seconds and 25 MB with slim fields) to fetch every record. Drug queries usually fit in 3 to 5 pages. Two ways to count:

1. Fetch all records with slim `fields` and aggregate locally. This works for any dimension, any network, and gives citations for free. It is slow for broad conditions.
2. Run one `countTotal=true&pageSize=1` call per bucket (one per phase, one per year). This gives exact totals fast for enum dimensions, but it gives no records to cite and cannot build networks or top-N lists of free-text values.

The design question this raises: do we cap the fetch and report "based on N of M studies", or do we combine both methods? See the open questions at the end.

## Useful fields

Paths are relative to `protocolSection` unless they start with `derivedSection`. Each row is something we can group by, filter on, or measure.

| Concept | Field (piece name / path) | Type | Missing in sample | Notes |
|---|---|---|---|---|
| Trial ID | `NCTId` | text | 0% | Citation key. Link: `https://clinicaltrials.gov/study/{nctId}` |
| Title | `BriefTitle` | text | 0% | Good human-readable citation excerpt |
| Phase | `Phase` / `designModule.phases` | enum list | 21% | Values: NA, EARLY_PHASE1, PHASE1, PHASE2, PHASE3, PHASE4. A list, see gotchas. |
| Study type | `StudyType` | enum | 0% | INTERVENTIONAL 79%, OBSERVATIONAL 21% in sample |
| Status | `OverallStatus` | enum | 0% | RECRUITING, COMPLETED, TERMINATED, ... |
| Why stopped | `WhyStopped` | free text | most | Only on stopped trials |
| Start date | `StartDate` / `statusModule.startDateStruct` | date + ACTUAL/ESTIMATED | 1% | Time-trend axis |
| Completion dates | `PrimaryCompletionDate`, `CompletionDate` | date + type | low | With start date gives duration |
| First posted | `StudyFirstPostDate` | date | 0% | Alternative time axis ("registered per year") |
| Has results | `HasResults` | bool | 0% | |
| Lead sponsor | `LeadSponsorName` | free text | 0% | Not normalized |
| Sponsor class | `LeadSponsorClass` | enum | 0% | INDUSTRY, NIH, FED, OTHER_GOV, NETWORK, INDIV, OTHER, ... |
| Collaborators | `CollaboratorName`, `CollaboratorClass` | list | often empty | Sponsor ↔ collaborator networks |
| Conditions | `Condition` | free text list | 0% | As written by the sponsor |
| Condition MeSH | `derivedSection.conditionBrowseModule.meshes[].term` | list | low | Normalized by NLM. Also has ancestors (for example "Lung Neoplasms"). |
| Interventions | `armsInterventionsModule.interventions[]` (`name`, `type`, `otherNames`) | list | 9% | Free-text names |
| Intervention type | `InterventionType` | enum | 9% | DRUG, BIOLOGICAL, DEVICE, PROCEDURE, RADIATION, BEHAVIORAL, ... |
| Intervention MeSH | `derivedSection.interventionBrowseModule.meshes[].term` | list | low | Normalized drug names. Best key for drug networks. |
| Arms | `armGroups[]` (`label`, `type`, `interventionNames`) | list | | Tells which drugs are given together in one arm |
| Enrollment | `EnrollmentCount` + `EnrollmentType` | int + ACTUAL/ESTIMATED | 2% | Histogram and scatter measure |
| Design | `DesignAllocation`, `DesignMasking`, `DesignPrimaryPurpose`, `DesignInterventionModel` | enum | interventional only | |
| Eligibility | `Sex`, `StdAge`, `MinimumAge`, `MaximumAge`, `HealthyVolunteers` | enum / text | low | `StdAge` is CHILD, ADULT, OLDER_ADULT |
| Locations | `contactsLocationsModule.locations[]` (`facility`, `city`, `state`, `country`, `geoPoint`) | list | 9% | Median 1 site per study, p95 47, max 889 |
| Officials | `OverallOfficialName`, `OverallOfficialAffiliation` | list | often empty | Investigator networks, sparse |
| Results | `resultsSection` (outcome measures, adverse events, baseline) | deep nested | only on studies with results | Complex. Likely out of scope for v1. |

## Data quality gotchas

These come from the lung cancer sample and the single full record we inspected.

1. Phase is a list. 7% of the sample is `PHASE1|PHASE2` and 2% is `PHASE2|PHASE3`. We must decide whether a Phase 1/2 trial counts as its own bucket or counts in both. Counting in both makes bar totals exceed the trial count.
2. Phase is missing for 21% (mostly observational studies) and `NA` for another 18% (device and behavioral trials). These need explicit "Not applicable" or "Unknown" buckets, not silent drops.
3. Dates have mixed precision. 65% are `YYYY-MM-DD` and 35% are `YYYY-MM`. Year buckets are safe. Month buckets need care.
4. Dates can be in the future. Start dates in the sample go to 2027, and these are `ESTIMATED`. A "trials per year" chart should mark or exclude estimated dates.
5. Free-text drug names vary. The sample has `Pembrolizumab`, `pembrolizumab`, `Keytruda®`, `MK-3475`, `Pembrolizumab (KEYTRUDA(R))`, and combined strings such as `pembrolizumab, pemetrexed, gemcitabine, cisplatin, carboplatin`. `Cisplatin` and `cisplatin` appear as separate top values. The intervention MeSH terms collapse these, so we should group and build networks on MeSH terms, with free text as a fallback.
6. Many trials have no drug. 41% of the sample has no DRUG-type intervention. "Drug" questions should consider BIOLOGICAL too (pembrolizumab is a biologic but is often entered as DRUG).
7. Locations can be removed. The full record NCT02142738 has zero locations and a `derivedSection.miscInfoModule.removedCountries` list of 16 countries. A country chart built only from `locations` undercounts completed trials. We can say so in `meta`.
8. One trial counts in many countries. A trial with sites in 16 countries adds 1 to each country bar. Bars then sum to more than the trial count, which `meta` must state.
9. Sponsor names are not normalized ("Merck Sharp & Dohme LLC" vs older variants). `LeadSponsorClass` is a clean enum.
10. Text queries are fuzzy. `query.cond=lung cancer` matches on MeSH ancestors and titles, so results include trials where lung cancer is one of several conditions. That is fine, but `meta` should record the exact API query used.

## What this data can answer

Measures we can compute: count of trials, sum or median enrollment, trial duration (completion minus start), count of sites.

Dimensions we can group by: phase, overall status, study type, start year, completion year, first-posted year, lead sponsor, sponsor class, intervention type, intervention (MeSH), condition (MeSH), country, allocation, masking, primary purpose, sex, age group, has results.

Relationships we can build as networks (all from fields on the same record):

- sponsor ↔ drug (lead sponsor, intervention MeSH)
- drug ↔ drug co-occurrence (two interventions on the same trial, or in the same arm for a stricter version)
- drug ↔ condition
- sponsor ↔ collaborator
- sponsor ↔ country (via locations)

Questions the data cannot answer well: efficacy or outcome comparisons (results data is sparse and not uniform), drug approval status, cost, and anything that needs data outside the registry.

## Citations: what is possible

Every number we show is an aggregate over records, and every record has an `nctId`. So each datum can carry the list of NCT IDs that contributed to it, plus the exact field value that put the trial in that bucket. For example, a "Phase 3" bar can cite `{nct_id: "NCT02142738", field: "protocolSection.designModule.phases", value: ["PHASE3"]}`. That value is an exact copy from the API response, so it is correct by construction. `BriefTitle` gives a readable excerpt to show next to it.

## Open questions this raises

1. Fetch cap vs. exact counts for broad queries (see "Scale").
2. How to count multi-phase trials and multi-country trials.
3. Whether to include estimated (future) start dates in time trends.
4. Whether drug identity is the MeSH term or the free-text name.
