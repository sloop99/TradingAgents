# Filing-level capital-structure evidence

The optional filing pass retrieves primary SEC annual/quarterly filings and
extracts numeric Inline XBRL candidates. This complements the aggregate companyfacts
feed: share-class, debt-instrument and other context dimensions stay attached to
the filing facts instead of being treated as consolidated company totals.

```powershell
python -m tradingagents.research FTNT --as-of 2026-09-16 --filings --no-market
```

Set the identifying SEC User-Agent in the local environment as usual. The contact
is not embedded in reports or committed configuration. By default the pass requests
one primary filing; `--max-filings 2` or `3` expands that bounded selection. The
ordinary companyfacts response is reused within the invocation. `--fixture`
continues to disable all live providers, even when `--filings` is present.

The normal `packet.json` and `coverage.md` retain eligible candidate facts. A separate
`filing-evidence.json` preserves the extraction response, including context metadata,
source node identifiers, snippets, rejected candidates and the source hash. The
HTML snapshot is cached for offline replay with its original retrieval timestamp.

## Interpretation

All extracted metrics use the `filing_` prefix. A known taxonomy tag does not
promote a candidate into a consolidated financial input. Reconciliation records
these as `candidate_only`; financial calculations and valuation readiness remain
independent of the number of candidates found.

The parser checks context issuer, dates, units, numeric transformations, scale and
sign. It preserves explicit dimensions, including share classes. Unsupported or
ambiguous values are rejected with diagnostics. Custom capital-related tags remain
issuer-specific candidates; a debt-security asset is not assumed to be a borrowing.

This pass does not establish that all share classes have been captured, that an
ADR ratio is one, that no split occurred, or that a debt schedule covers every
interest-bearing obligation. It does not infer missing preferred stock or minority
interest as zero. Those conclusions require further sourced reconciliation. Text
notes and untagged tables can also contain necessary evidence outside this numeric
extractor's coverage.

## Retrieval boundaries

The provider uses SEC filing references to select documents published by the
requested cutoff. Primary URLs are restricted to the expected SEC issuer/accession
path. Requests, response sizes and retries are bounded; permanent HTTP failures
produce evidence gaps. Cached source integrity is checked before replay. Parsing
does not execute scripts, fetch document links or resolve external XML entities.

The fixed-zero numeric transform follows the
[XBRL transformation registry](https://www.xbrl.org/Specification/inlineXBRL-transformationRegistry/REC-2020-02-12/inlineXBRL-transformationRegistry-REC-2020-02-12.html).
Unsupported transformations are reported rather than guessed; this is a focused
capital-structure extractor, not a complete XBRL validation engine.
