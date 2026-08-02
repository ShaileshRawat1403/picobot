# Pico artifacts

Artifacts are durable, owner-scoped work products. They are stored inside the
configured Pico workspace, versioned on every revision, and inspectable without
depending on the model provider that created them.

## Contract

An artifact has two independent classifications:

- **Kind** — the purpose of the work product: note, brief, plan, report,
  draft, checklist, data, link, code, or config.
- **Format** — the serialization: Markdown, plain text, JSON, YAML, CSV, TSV,
  JSONL, or a URI list.

Kinds stay deliberately small. A meeting note, decision record, or research
pack can use note, brief, or report with a useful title rather than
introducing a new kind for every workflow.

Every artifact records:

- owner and session provenance;
- current revision and immutable revision files;
- content type and safe local relative path;
- optional source run and mission references; and
- unverified, verified, or stale verification state.

Revising an artifact creates a new revision and marks the current artifact
stale. Verification is an explicit owner action.

## Supported formats

| Format | MIME type | Typical use |
| --- | --- | --- |
| Markdown | text/markdown | Plans, briefs, reports, checklists |
| Plain text | text/plain | Notes, prompts, code, configuration |
| JSON | application/json | Structured records and link bundles |
| YAML | application/yaml | Configuration and personal operating rules |
| CSV | text/csv | Tables and spreadsheet-compatible exports |
| TSV | text/tab-separated-values | Tables with comma-heavy values |
| JSONL | application/x-ndjson | Bounded event or observation records |
| URI list | text/uri-list | Simple source-link collections |

JSON, YAML, JSONL, CSV, and TSV are validated before they are written. YAML is
parsed with a safe loader. JSON constants such as NaN are rejected. CSV and
TSV parsing is strict enough to catch malformed quoting. JSONL is bounded to
10,000 records.

## Link artifacts

A link artifact may be either:

1. a text/uri-list containing absolute HTTP(S) URLs; or
2. an application/json link bundle:

       {
         "links": [
           {
             "url": "https://example.com/reference",
             "label": "Reference",
             "note": "Read this first",
             "source": "browser-review"
           }
         ]
       }

Link artifacts never fetch URLs automatically. file:, data:, javascript:,
embedded credentials, and other non-HTTP(S) URLs are rejected.

## Sharing

The local workbench can download an artifact with a meaningful, format-aware
filename such as research-brief-v2.md or source-pack-v1.json. The download
contains only the selected artifact revision. Pico does not upload artifacts to
an external sharing service.

## Deferred exports

PDF, DOCX, XLSX, HTML, and ICS are intentionally derived-export work for a
later slice. Pico should keep a small, inspectable canonical artifact first,
then add exporters with their own rendering, dependency, and verification
tests. Binary uploads, executable files, and automatic code execution are not
part of the artifact store.
