# Example payloads

This directory contains public-safe sample inputs for onboarding, smoke tests, and documentation.

## Files

- `invoice.sample.json` — sample payload for `./project-tool invoice`
- `document.sample.json` — sample payload for `./project-tool doc`
- `sample_document.txt` — local text file referenced by `document.sample.json`

## Usage

```bash
./project-tool invoice examples/invoice.sample.json
./project-tool doc examples/document.sample.json
```

These examples are intentionally generic and contain no real customer data, tokens, or private delivery targets.
