# Receipt and expense-report contract

The receipt demo produces versioned JSON with the field names and outcome
meanings below. Generated wording, text-block segmentation, and PDF bytes can
vary between runs.

## Parsed receipts

Each input has a stable `receipt_id` and `source`, retained even if processing fails.
Parsed receipt records use `schema_version: "1"` and the following fields:

| Fields | Meaning |
| --- | --- |
| `receipt_id`, `source`, `credit` | Input identity, original filename/reference, and attribution. |
| `pages` | Ordered records containing one-based `number`, extracted `text`, and reading-order `blocks`. |
| `merchant`, `date_text`, `transaction_date` | Merchant and printed date; normalize only unambiguous dates to `YYYY-MM-DD`. |
| `currency`, `transaction_type` | Payable ISO currency and `purchase` or `refund`. |
| `printed_total`, `total` | Printed evidence and the interpreted payable amount as a decimal string. |
| `line_items`, `taxes`, `discounts` | Optional lists of details with `description` and an amount when known. |
| `evidence` | Field evidence with `field`, source `page`, supporting `text`, and an unknown-value `reason` when applicable. |
| `issues`, `monetary_issues`, `error` | Secondary issues, unresolved monetary problems, and document-processing failure. |

Unknown scalar values remain null, with reasons. Do not fabricate a zero or derive
an unreadable payable total from other amounts. Keep successfully extracted text
and fields even if the receipt must be excluded.

Implementations may retain source fingerprints (`content_hash`, `pixel_hash`),
rendered-page paths (`rendered_pages`), and classified `arithmetic` evidence. Paths
inside a worker filesystem are operational references, not portable download URLs.

An arithmetic record has a `basis` (`items`, `subtotal`, or `none`), `complete`, and
`components`. Each component has `role`, decimal-string `amount`, source `page`,
and supporting `text`. Roles are `item`, `subtotal`, `discount`, `tax_added`, `fee`,
`total`, `tax_included`, `tender`, `change`, `carry_forward`, and `conversion`.

## Final entries and totals

The report contains one entry per input in input order. Each entry has
`receipt_id`, `source`, `outcome`, `reasons`, `signed_amount`, `currency`, and
`duplicate_of` when it is a confirmed duplicate.

| Outcome | Effect on totals |
| --- | --- |
| `included` | Count the resolved signed expense once in its payable currency. |
| `flagged` | Exclude unresolved critical monetary values, contradictions, or suspected duplicates; retain reasons. |
| `duplicate` | Exclude the repeated expense and identify the retained input with `duplicate_of`. Keep its original in the appendix. |
| `error` | Retain the input and document failure; processing of other inputs continues. |

Money uses decimal strings and decimal arithmetic. Refunds reduce totals. Keep
separate totals per currency and perform no currency conversion. Missing secondary
details alone do not invalidate a known expense.

Check only complete, clearly comparable printed components using one basis:
items or a single subtotal, plus applicable discounts, added tax, and fees. Do not
add the final total, included tax, tender, change, carry-forward, or conversion
amounts again. An incomplete equation alone does not justify an arithmetic flag.

Exact source identity can confirm duplicates even when model interpretations
differ. Semantic duplicates require shared transaction evidence; amount/date
similarity alone is insufficient. A suspected duplicate remains excluded for the
run. There is no subsequent human-review requirement.

## Report and coordinator result

`report.json` contains `schema_version: "1"`, `status`, `entries`, `totals` grouped
by currency, parsed `receipts`, `artifacts`, and `error` when applicable. The PDF
follows the shared [summary and appendix layout](receipt-demo.md#report-layout).

Run status is `completed`, `completed_with_flags`, or `failed`. Flags, duplicates,
and document errors are completed automated outcomes. Shared orchestration,
inference configuration, report generation, or artifact retrieval failures yield
`failed`, preserving known outcomes where possible. Never promise an artifact
that failed to generate or transfer.

The coordinator returns the final status, entries, totals, and verified artifact
references to the caller. The Python `result.json` includes artifact `path`, byte
count (`bytes`), and `sha256`, plus child-job diagnostics in `jobs`. Keep operational
diagnostics outside the expense PDF. Other implementations may expose additional
language-specific execution metadata while preserving the shared report semantics.
