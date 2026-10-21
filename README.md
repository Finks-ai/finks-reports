# Finks reports

The public Finks reports site, served at https://finks-ai.github.io/finks-reports/. Everything here is public
and readable by anyone on the internet. It carries only allowlisted summaries generated from internal reports:
counts, workstream and dataset status, lake size and rows by category, eval suite check counts and pass rates,
and cost trends indexed to a baseline of 100 with no amounts.

## How it is built

The model is built and schema-checked in [finks-lakehouse](https://github.com/Finks-ai/finks-lakehouse), which
exports the single file `data/model.json` to this repo's `data` branch. CI then renders, leak-scans and deploys
Pages from `main`:

1. finks-lakehouse pushes `data/model.json` to the `data` branch;
2. the `render` check fetches it, runs `render.py`, which renders the six HTML files and runs `scan.py`, failing
   on any finding;
3. on a push to `main`, the `deploy` job publishes the rendered site to GitHub Pages.

## Contributing

- A new page or section means editing `render.py` and its tests, then updating `tests/expected` with the new output:
  `python render.py --model tests/model.json --out /tmp/out && cp -R /tmp/out/. tests/expected/`, review the diff,
  then `pytest -q`. Model strings may only ever reach text nodes, never attribute values; the scan is the tag and
  attribute gate.
- New data (a new category, suite, status, or any string that appears on a page) is not added here: it needs a
  finks-lakehouse pull request that extends the model and its allowlist, since finks-lakehouse owns the allowlist.
- Every pull request must pass the `render` check.

## Previewing locally

```
git fetch origin data
git show origin/data:data/model.json > model.json
python render.py --model model.json --out _site
```

## Leak scan

`scan.py` re-reads every rendered file and refuses anything outside the allowlisted HTML shapes and generic
leak shapes: AWS account ids and access keys, s3:// paths, URLs, dollar amounts, emails and handles, PR and
issue refs, snake_case identifiers, dotted names and hostnames, ARNs, UUIDs, hex ids, IP addresses, credential
and token words, SQL, billing words and storage sizes. Storage sizes and token words are exempt on the lake
page only. A finding names the file and rule, never the matched text.

## Never commit private values

This repository is public. Never commit account ids, bucket names, internal database or table names, vendor or
provider names, or any other private value here.
