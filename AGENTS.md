# MaliYardımcı Agent Rules

## Product Goal

This is a real pilot for a Mali Müşavir workflow, not a throwaway demo. Every change must improve or protect the core flow:

`Mükellef seçimi -> evrak yükleme -> belge okuma -> dönem kütüphanesi -> kontrol -> çıktı -> silme/düzeltme`

## No Narrow Fixes Without Flow Review

When changing any user-facing behavior, inspect the whole affected process:

- Is the frontend control visible and connected to an event handler?
- Is the local API route present in `malipilot/server.py`?
- Is the deployed Vercel API route present in `api/index.py`?
- Do SQLite and Supabase paths both work or fail clearly?
- Does the UI show a visible success message?
- Does the UI show a visible failure message?
- Does the app refresh `/api/state` after the action?
- Does the behavior still work after a browser refresh?

## Local And Vercel Entry Points

Local development uses:

- `malipilot/server.py`

Vercel deployment uses:

- `api/index.py`

Any new API endpoint must be wired into both entry points unless the code has been deliberately centralized. A feature that only works locally is not done.

## Done Criteria

A change is not complete just because code was written. For functional changes, run the smallest relevant checks:

```bash
python3 -m unittest discover -s tests
python3 -m compileall malipilot api tests
node --check static/app.js
```

For deployed API changes, also verify the live Vercel route with `curl` after push.

## Turkish Product Surface

All visible app UI must be Turkish:

- buttons
- labels
- table headings
- status messages
- validation errors
- upload/delete/review messages

German or English terms may appear in developer documentation, but not in the product interface.

## Data Reliability Rules

Never silently drop, hide, or keep stale data. Upload, review, delete, and export flows must make these states obvious:

- successful
- failed
- partially processed
- requires manual control
- deleted
- not deleted

Critical Supabase DELETE operations must verify that rows were actually deleted, not only that a request was sent.

## Document Logic

`Z GÜNLÜK RAPORU` is not a normal customer receipt.

- `KÜM TOP` is cumulative total, not daily total.
- `KÜM KDV` is cumulative VAT, not daily VAT.
- Daily values come from the current `TOP`, `KDV`, or `VERGİ DÖKÜMÜ` section.
- If a receipt upload is actually a Z report, reroute or mark it clearly instead of storing it as a normal `Fiş`.

## Git Hygiene

- Do not commit `ali_evrak/`; it contains sample/client-like documents.
- Keep diffs focused.
- Do not revert unrelated user changes.
- Commit and push completed project fixes when the user is actively testing the deployed Vercel app.
