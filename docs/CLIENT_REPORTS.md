# Client evidence reports

Every accepted shared facility export writes `building_NN_report.pdf` beside
the exact canonical `building_NN.json`. CLI generation, HTTP jobs and saved
editor revisions use this shared writer. The manifest's `client_report` index
records PDF/source SHA256, project, variant, drawing revision, profile version
and each ordered page's source identity and status. The review interface and
authenticated artifact inventory expose the actual indexed file.

The vector A4 report explains programmed-area deviation, derived route lengths,
door crossings, footprint ratio, every saved facility check and its complete
evidence, declared rule sources/editions/dates/parameters and coordination
issues. Long sections paginate without dropping evidence; continuation pages
repeat their check ID/status. Additional declared rules without a linked check
are identified as declarations. UNKNOWN stays unresolved. Every page prints
`Engineering acceptance: PENDING` and the exact canonical source hash.

This is an explanation of saved source evidence, not a new engineering
calculation, a legal/GMP verdict, a permit or an authenticated review. Actual
process/QA, cleanroom/HVAC and architectural decisions remain separate.

`CLIENT_REPORT_READBACK` independently inspects the delivered PDF against the
canonical source and index: file/hash, A4 page geometry, vector/text content,
ordered sections, printed identities/statuses/metrics, complete check evidence,
rule provenance and issue states. Recomputing a modified PDF's hash does not
conceal changed identities, statuses, evidence or metrics. This checker does
not authenticate the source author or establish engineering acceptance.

Malformed, missing, duplicate or contradictory source check records are rejected
before report creation. Limits are 8 MiB canonical source, one million report
characters and 256 pages; readback accepts at most 16 MiB PDF and 1 MiB decoded
page stream. Bundled Unicode fonts and measured wrapping avoid machine-specific
font assumptions. Legacy bundles without a report index retain their previous
checks; an indexed missing or invalid report fails bundle QA.

Fresh acceptance covers actual CLI bundles, authenticated HTTP PDF download,
saved editor revisions, installed-wheel generation, tampered text/index/hash,
long continuation sections and Unicode data. A 14-page pilot and 10-page dense
fixture were rendered and visually inspected.
