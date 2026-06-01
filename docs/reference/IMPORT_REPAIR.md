# Finimatic Import CSV Repair Handoff

## Operator Goal

Fix the Import screen so a CSV/TXT upload reliably imports all creator data, not only email/name.

The desired user-facing CSV shape is:

```csv
email,creator_name,website_url,notes,tags,info
creator@example.com,Creator Name,https://creator-site.example,"private notes","youtube, ai, creator","Brief context about what this creator does; the AI should use this when writing emails."
```

The important requirement is that `info` is not a source URL. It is brief creator context for the LLM to use later while generating personalized emails.

Do not solve this by renaming the database `source` column. Keep `source` as internal provenance such as `manual`, `csv_import`, `txt_import`, or filename/import source. Add or map `info` into an existing LLM-used field.

Recommended mapping:

- `email` -> `Contact.email`
- `creator_name` / `creator name` / `name` -> `Contact.creator_name`
- `website_url` / `website` / `youtube` / `channel_url` -> `Contact.website_url`
- `notes` -> `Contact.notes`
- `tags` -> `Contact.custom_fields.tags`
- `info` / `creator_info` / `context` / `about` / `description` -> `Contact.personalization`
- `source` -> `Contact.source`, optional internal provenance only

## Current Verified Behavior

The Import UI currently shows only these preview columns:

```text
row | email | status | reason
```

So the UI can make a correct import look broken because it hides creator name, website, notes, tags, source, and personalization/info during preview.

Current browser state after uploading `youtubers_import_template.csv` showed:

```text
Loaded youtubers_import_template.csv
row email status reason
1 business@mkbhd.com duplicate Contact already exists
...
10 business@minutephysics.com duplicate Contact already exists
```

That duplicate result is expected because those contacts were already inserted earlier. The preview table still does not prove whether the hidden fields parsed correctly.

## Root Cause

There are two separate import paths, and they do not behave the same.

### 1. File upload parses CSV in the frontend

File: `frontend/src/App.tsx`

Relevant area:

```tsx
function parseImportFileContent(content: string, filename: string): Record<string, string>[] {
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const isCsv = filename.toLowerCase().endsWith(".csv");
  if (!lines.length) return [];
  if (isCsv) {
    const headers = splitCsvLine(lines[0]).map((header) => header.trim());
    return lines.slice(1).map((line) => {
      const cells = splitCsvLine(line);
      const row: Record<string, string> = {};
      headers.forEach((header, index) => {
        row[header] = cells[index] ?? "";
      });
      row.source = row.source || "csv_import";
      return row;
    });
  }
  ...
}
```

And:

```tsx
async function handleFileSelect(event: React.ChangeEvent<HTMLInputElement>) {
  const file = event.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  const format = file.name.toLowerCase().endsWith(".csv") ? "csv" : "txt";
  const rows = parseImportFileContent(text, file.name);
  setFileName(file.name);
  setPaste("");
  previewMutation.mutate({ format, rows, filename: file.name });
}
```

Problem: CSV upload sends already-parsed `rows` to the backend. The frontend keeps headers exactly as written. It does not normalize human headers like `creator name`, `website`, `info`, `youtube`, or `creator info`.

### 2. Backend header normalization is bypassed for uploaded rows

File: `backend/app/imports/service.py`

The backend has useful aliases:

```python
HEADER_ALIASES = {
    "email": "email",
    "creator": "creator_name",
    "name": "creator_name",
    "website": "website_url",
    "website_url": "website_url",
    "notes": "notes",
    "tags": "tags",
    "source": "source",
}
```

But the backend skips that parser when `rows` are provided:

```python
def parse_payload(format_name: str, rows: list[dict] | None = None, content: str | None = None) -> list[dict]:
    if rows is not None:
        return rows
    ...
```

Because file upload sends `rows`, header alias normalization is not used.

### 3. Commit only reads canonical field names

File: `backend/app/imports/service.py`

Commit creates contacts using canonical keys only:

```python
contact = Contact(
    email=data["email"],
    creator_name=data.get("creator_name"),
    business_name=data.get("business_name"),
    website_url=data.get("website_url"),
    source=data.get("source") or preview.get("format") or "manual",
    provenance=preview.get("filename"),
    notes=data.get("notes"),
    personalization=data.get("personalization"),
    lead_category=data.get("lead_category"),
    custom_fields=custom_fields_with_tags(None, data.get("tags")),
    import_batch_id=batch.id,
)
```

If upload rows contain keys like `creator name`, `website`, or `info`, those values are present in `raw_data`, but they are ignored by commit because they are not named `creator_name`, `website_url`, or `personalization`.

### 4. Diagnostic proof

No-commit preview probe with uploaded-row-shaped JSON:

```json
{
  "format": "csv",
  "filename": "diagnostic.csv",
  "rows": [{
    "email": "diagnostic-upload-only@example.invalid",
    "creator name": "Diagnostic Creator",
    "website": "https://example.invalid",
    "notes": "Brief creator info",
    "tags": "test, import",
    "info": "Extra creator context"
  }]
}
```

Current result:

```json
{
  "email": "diagnostic-upload-only@example.invalid",
  "status": "missing_field",
  "reason": "creator_name or business_name is required",
  "parsed_data": {
    "creator name": "Diagnostic Creator",
    "website": "https://example.invalid",
    "info": "Extra creator context",
    "notes": "Brief creator info",
    "source": "manual"
  }
}
```

The same data as pasted CSV content is accepted because backend normalization runs:

```json
{
  "email": "diagnostic-paste-only@example.invalid",
  "status": "accepted",
  "parsed_data": {
    "creator_name": "Diagnostic Creator",
    "website_url": "https://example.invalid",
    "notes": "Brief creator info",
    "tags": "test, import",
    "info": "Extra creator context",
    "source": "paste"
  }
}
```

## LLM Context Reality

File: `backend/app/ai/prompts.py`

Draft generation currently uses this:

```python
what_they_do = contact.personalization or contact.notes or contact.lead_category or "unknown"
```

So if the operator wants imported creator context to help the AI write better emails, that context should land in `personalization` first, or `notes` second.

The `source` field is not the right place for creator context. It is currently displayed in contacts and used as provenance/source metadata. It is not the main AI context field.

## Recommended Fix

Use the smallest safe fix:

1. Let the backend parse uploaded file text instead of parsing CSV in the frontend.
2. Normalize row keys on the backend anyway, so any future caller that sends `rows` also works.
3. Add `info` aliases and map them to `personalization`.
4. Update the Import preview table so the user can see all parsed fields before committing.
5. Update the Import form label so the user no longer thinks `Source` is where creator context belongs.

No database migration is required.

## Exact Implementation Steps

### Step 1 - Backend: extend header aliases

File: `backend/app/imports/service.py`

Extend `HEADER_ALIASES`:

```python
HEADER_ALIASES = {
    "email": "email",
    "email_address": "email",
    "email_id": "email",
    "e_mail": "email",
    "creator_name": "creator_name",
    "creator": "creator_name",
    "name": "creator_name",
    "full_name": "creator_name",
    "business_name": "business_name",
    "business": "business_name",
    "company": "business_name",
    "website": "website_url",
    "website_url": "website_url",
    "url": "website_url",
    "youtube": "website_url",
    "youtube_url": "website_url",
    "channel": "website_url",
    "channel_url": "website_url",
    "notes": "notes",
    "note": "notes",
    "personalization": "personalization",
    "info": "personalization",
    "creator_info": "personalization",
    "creator_context": "personalization",
    "context": "personalization",
    "about": "personalization",
    "description": "personalization",
    "lead_category": "lead_category",
    "niche": "lead_category",
    "tags": "tags",
    "source": "source",
}
```

### Step 2 - Backend: normalize provided rows too

File: `backend/app/imports/service.py`

Add helper:

```python
def normalize_import_row(raw: dict, default_source: str) -> dict:
    normalized: dict = {}
    extras: dict = {}
    for key, value in (raw or {}).items():
        normalized_key = normalize_header(str(key))
        if normalized_key in {
            "email",
            "creator_name",
            "business_name",
            "website_url",
            "notes",
            "personalization",
            "lead_category",
            "tags",
            "source",
        }:
            if value not in (None, ""):
                normalized[normalized_key] = value
        else:
            extras[str(key)] = value

    normalized["source"] = normalized.get("source") or default_source
    if extras:
        normalized["_extra"] = extras
    return normalized
```

Then change:

```python
if rows is not None:
    return rows
```

to:

```python
if rows is not None:
    default_source = "csv_import" if format_name == "csv" else format_name or "manual"
    return [normalize_import_row(row, default_source) for row in rows]
```

This fixes the current uploaded-row failure even if the frontend still sends `rows`.

### Step 3 - Backend: normalize structured content rows through the same helper

File: `backend/app/imports/service.py`

In `parse_structured_content`, after constructing each row from headers or positional fields, pass it through `normalize_import_row(row, default_source)` before appending.

Reason: one canonical normalization path prevents CSV upload, pasted CSV, TXT, and direct API imports from drifting again.

### Step 4 - Frontend: send file content, not parsed rows

File: `frontend/src/App.tsx`

Change `handleFileSelect` from:

```tsx
const rows = parseImportFileContent(text, file.name);
setFileName(file.name);
setPaste("");
previewMutation.mutate({ format, rows, filename: file.name });
```

to:

```tsx
setFileName(file.name);
setPaste("");
previewMutation.mutate({ format, content: text, filename: file.name });
```

Reason: the backend already has the better parser with header aliases and positional fallback. Let one parser be the source of truth.

After this, `parseImportFileContent` may only be needed for other paths. If nothing uses it, remove it and `splitCsvLine` in the same change.

### Step 5 - Frontend: replace visible Source input with Info / AI Context

File: `frontend/src/App.tsx`

Current manual form state:

```tsx
const [source, setSource] = useState("manual");
```

Recommended manual form state:

```tsx
const [info, setInfo] = useState("");
```

Current manual row payload:

```tsx
[{ email, creator_name: name, website_url: website, notes, tags, source }]
```

Recommended manual row payload:

```tsx
[{ email, creator_name: name, website_url: website, notes, tags, personalization: info, source: "manual" }]
```

Current UI:

```tsx
<label>Source<input value={source} onChange={(e) => setSource(e.target.value)} /></label>
```

Recommended UI:

```tsx
<label>Info / AI Context<input value={info} onChange={(e) => setInfo(e.target.value)} /></label>
```

Also reset `info` after successful submit.

Do not remove the backend `source` field. Keep setting it internally to `manual`, `csv_import`, or `txt_import`.

### Step 6 - Frontend: show all parsed fields in preview

File: `frontend/src/App.tsx`

Current `ImportRows` only shows:

```tsx
columns={["row", "email", "status", "reason"]}
```

Replace it with a preview table that reads from `row.parsed_data`.

Suggested columns:

```tsx
[
  "row",
  "email",
  "creator",
  "website",
  "notes",
  "tags",
  "info",
  "source",
  "status",
  "reason"
]
```

Suggested rendering logic:

```tsx
function ImportRows({ rows }: { rows: ImportPreview["rows"] }) {
  return (
    <DataTable
      columns={["row", "email", "creator", "website", "notes", "tags", "info", "source", "status", "reason"]}
      rows={rows.map((row) => {
        const data = row.parsed_data ?? {};
        return [
          String(row.row_num),
          row.email,
          String(data.creator_name ?? data.business_name ?? ""),
          String(data.website_url ?? ""),
          String(data.notes ?? ""),
          Array.isArray(data.tags) ? data.tags.join(", ") : String(data.tags ?? ""),
          String(data.personalization ?? data.info ?? ""),
          String(data.source ?? ""),
          row.status,
          row.reason ?? ""
        ];
      })}
    />
  );
}
```

Important: keep text truncation or CSS wrapping reasonable if notes/info are long. The UI should not explode for long creator context.

### Step 7 - Optional Contacts UI improvement

File: `frontend/src/App.tsx`

Contacts page currently displays:

```text
email | name | status | source | tags | auto-reply | actions
```

It does not display website, notes, or personalization/info. That can continue to make users think import lost data.

Add either:

- columns for `website` and `info`, or
- a small details/expand view per contact.

This is optional for fixing import correctness, but useful for user trust.

## Tests To Add

File: `backend/tests/test_import_policy_ai_followups.py`

Add tests like these.

### Test 1 - uploaded rows with human headers are normalized

```python
def test_import_preview_normalizes_uploaded_row_headers(client):
    rows = [
        {
            "email": "upload-human@example.com",
            "creator name": "Upload Human",
            "website": "https://upload-human.example",
            "notes": "operator notes",
            "tags": "youtube, education",
            "info": "Creator teaches practical AI workflows.",
        }
    ]

    preview = client.post("/api/import/preview", json={"format": "csv", "rows": rows, "filename": "leads.csv"}).json()

    assert preview["rows"][0]["status"] == "accepted"
    data = preview["rows"][0]["parsed_data"]
    assert data["creator_name"] == "Upload Human"
    assert data["website_url"] == "https://upload-human.example"
    assert data["notes"] == "operator notes"
    assert data["tags"] == "youtube, education"
    assert data["personalization"] == "Creator teaches practical AI workflows."
    assert data["source"] == "csv_import"
```

### Test 2 - commit stores info where draft generation can use it

```python
def test_import_info_is_stored_as_personalization_for_llm_context(client):
    rows = [
        {
            "email": "llm-context@example.com",
            "creator name": "LLM Context",
            "website": "https://llm-context.example",
            "info": "This creator teaches AI automation to small business owners.",
            "tags": "youtube, ai",
        }
    ]

    committed = client.post("/api/import/commit", json={"format": "csv", "rows": rows, "filename": "leads.csv"}).json()
    assert committed["summary"]["accepted"] == 1

    contact = next(item for item in client.get("/api/contacts").json()["items"] if item["email"] == "llm-context@example.com")
    assert contact["creator_name"] == "LLM Context"
    assert contact["website_url"] == "https://llm-context.example"
    assert contact["personalization"] == "This creator teaches AI automation to small business owners."
    assert contact["custom_fields"]["tags"] == ["youtube", "ai"]
    assert contact["source"] == "csv_import"
```

### Test 3 - pasted CSV and uploaded CSV behave the same

Use the same content once as `content` and once as `rows`, then assert both previews produce the same canonical `parsed_data` keys.

## Browser Acceptance Test

Use a new test CSV with unique emails so duplicate detection does not hide the success path.

Suggested CSV:

```csv
email,creator name,website,notes,tags,info
import-check-1@example.com,Import Check One,https://one.example,"private note one","youtube, ai","AI context one"
import-check-2@example.com,Import Check Two,https://two.example,"private note two","education, creator","AI context two"
```

Manual UI steps:

1. Open `http://localhost:5173/`.
2. Go to Import.
3. Click `Choose File`.
4. Upload the CSV.
5. Confirm preview table displays creator, website, notes, tags, info/source, status, and reason.
6. Confirm both rows are `accepted`.
7. Click `Commit`.
8. Go to Contacts.
9. Confirm both contacts exist.
10. Confirm creator name, website, tags, and info/personalization are preserved through `/api/contacts`.

Do not use the Paste box for this acceptance test.

## Success Criteria

The fix is complete only when all of these are true:

- Uploading a CSV file with `email,creator name,website,notes,tags,info` works.
- Uploading a CSV file with `email,creator_name,website_url,notes,tags,personalization` also works.
- TXT import still works positionally.
- Paste import still works.
- Preview table visibly shows parsed creator, website, notes, tags, info, source, status, and reason.
- Commit stores:
  - `creator_name`
  - `website_url`
  - `notes`
  - `custom_fields.tags`
  - `personalization` from CSV `info`
  - internal `source` as `csv_import` or equivalent provenance
- Draft generation uses the imported `info` because `draft_user_prompt()` reads `contact.personalization` before `contact.notes`.
- Existing duplicate, suppression, invalid email, and missing field behavior remains intact.

## What Not To Do

- Do not rename the database column `source` to `info`.
- Do not put creator context into `source`.
- Do not add a new database table.
- Do not add a migration unless the product explicitly needs a brand-new field. It does not for this fix.
- Do not make the frontend and backend maintain two different CSV parsers.
- Do not judge import success only from the current preview table, because it hides most fields.

## Short Prompt For Main Chat

```text
Fix Finimatic Import CSV upload. Do not redesign the app. Read IMPORT_REPAIR.md first and implement exactly that narrow repair.

Main bug: file upload parses CSV in frontend and sends rows, so backend header alias normalization is bypassed. Human headers like "creator name", "website", and "info" are not mapped to creator_name, website_url, and personalization. Preview table also hides all parsed fields except email/status/reason.

Required behavior: CSV/TXT upload should preserve email, creator name, website, notes, tags, and info. "info" means brief creator context for LLM email generation and should map to Contact.personalization, not Contact.source. Keep source as internal provenance like csv_import/manual.

Success: upload a real CSV file with headers email,creator name,website,notes,tags,info; preview shows all parsed fields; commit stores creator_name, website_url, notes, custom_fields.tags, personalization, and internal source; draft generation can use imported info through draft_user_prompt().
```
