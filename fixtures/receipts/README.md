# Shared demo receipts

Small inputs for demonstrating agents on Nebius Token Factory Sandboxes with
Token Factory inference. This is not a model benchmark or a human-review
workflow.

There are **12 distinct receipts and 6 variants**. Files are checked in, so normal
demo use does not need Wikimedia access or fixture-generation packages. Every
input is one receipt, including each two-page PDF. See [credits](CREDITS.md).

## Use the inputs

The [manifest](manifest.json) provides paths, stable input/source identities,
checksums, media dimensions, and named input sets. Paths are relative to this
directory. List a set using only the Python standard library:

```sh
python3 fixtures/receipts/check.py --list demo
```

| Profile | Contents |
| --- | --- |
| `base` | All 12 distinct receipts |
| `demo` | The 12 receipts plus an exact duplicate and a corrupt file (14 inputs) |
| `minimal` | Control, unreadable total, corrupt file (3 inputs) |
| `duplicates` | Control, exact copy, different PNG encoding (3 inputs) |
| `missing_merchant` | Masked-merchant synthetic variant in isolation |
| `all` | All 18 inputs, including orientation and blur variants |

Run the [receipt demo](../../examples/python/receipt_demo/README.md) from
`examples/python` to process a profile:

```sh
python3 -m receipt_demo --profile minimal --output receipt-output-minimal
```

The coordinator passes the selected **input files and distinct input IDs**
to workers and retains originals for the report appendix. `source_receipt_id`,
coverage tags, and fixture classifications are fixture bookkeeping, not extraction
evidence or a shortcut for duplicate detection. Detect duplicates from the actual
files/receipt evidence. Never send synthetic source definitions or expected
behaviors to extraction agents as evidence.

## Coverage

| ID | Receipt / purpose |
| --- | --- |
| r01 | Real English retail photograph; low resolution and no visible date |
| r02 | Real German scan with a VAT adjustment and final payable amount |
| r03 | Real German retail scan; cashier redacted, merchant still visible |
| r04 | Real Swiss restaurant photograph; CHF total and EUR equivalent |
| s01 | Synthetic English native-text PDF, two pages with carry-forward amounts |
| s02 | Synthetic German native-text PDF, two pages and decimal commas |
| s03 | Refund printed as a positive amount; normalized expense is negative |
| s04 | Unreadable total and line amount; no visible arithmetic can recover the value |
| s05 | Intentional arithmetic mismatch: lines 12.00 + 8.00, printed total 25.00 |
| s06 | Explicit discount: 30.00 - 5.00 = 25.00 |
| s07 | Two tax rates included in gross line amounts; do not add tax twice |
| s08 | Straightforward USD control, including sales tax |
| v01 | Byte-identical copy of s08 |
| v02 | Different PNG encoding of s08, with identical decoded pixels |
| v03 | s08 rotated clockwise 90 degrees |
| v04 | s08 blurred and JPEG-compressed |
| v05 | Intentionally invalid PDF tied to s08; processing should continue |
| v06 | s08 with its merchant heading masked; other evidence retained |

All variants share s08's receipt identity, so they are not six additional
purchases. Outcomes depend on the chosen set: a lone v06 exercises missing
merchant behavior, while mixed with s08 it can also be a duplicate candidate.
Known totals can remain included when only a secondary field is missing;
unknown totals and monetary contradictions are final flags/exclusions.

The merchant is visible in r03; v06 exercises a missing merchant heading.
Low-resolution public inputs may produce incomplete extraction.

## Lightweight checks and rebuilding

[expected-behaviors.json](expected-behaviors.json) records a few known synthetic
values and intended behaviors. It is not an exhaustive annotation schema or a
measured model result. [synthetic-sources.json](synthetic-sources.json) is the
editable recipe; its hidden source value for s04 must never become an extracted
amount. Only the PDF/image input is evidence.

From the repository root:

```sh
python3 -m venv /tmp/receipt-fixture-tools
/tmp/receipt-fixture-tools/bin/python -m pip install -r fixtures/receipts/requirements.txt
/tmp/receipt-fixture-tools/bin/python fixtures/receipts/check.py
```

The checker verifies all hashes, media decoding, multipage PDF text/rendering,
expected corrupt-file rejection, exact/pixel duplicate identities, and the
synthetic arithmetic. It does not call Token Factory or launch sandboxes.

To deliberately rebuild synthetic inputs and their manifest:

```sh
/tmp/receipt-fixture-tools/bin/python fixtures/receipts/build.py
/tmp/receipt-fixture-tools/bin/python fixtures/receipts/check.py
```

The builder never downloads or modifies public originals. It uses fixed dates
and deterministic PDF metadata; rendering uses the pinned packages. Rebuilds
with other platforms/library versions can change raster bytes, so keep the
checked-in assets and manifest together. Visually inspect regenerated pages if
changing the recipes. Increment `fixture_version` when intentionally publishing
a new fixture set.
