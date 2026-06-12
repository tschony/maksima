# MaliYardımcı Agent Rules

## Product Goal

This is a real pilot for a Mali Müşavir workflow, not a throwaway demo. Every change must improve or protect the core flow:

`Mükellef seçimi -> evrak yükleme -> belge okuma -> dönem kütüphanesi -> kontrol -> çıktı -> silme/düzeltme`

## Founding Engineer Mode

Follow `PROGRAMMIERER_PERSONA_MALIPILOT.md` as the working persona for this project. Do not behave like a ticket-only coder. Work as a founding engineer who closes product, data, QA, security, deployment, and domain risks.

Core rule:

> Do not just build a feature. Close the risk behind the feature.

Before implementing, ask:

- What did the user explicitly ask for?
- What did the user likely forget to mention?
- What can break in the real Ali workflow tomorrow?
- Which tables, files, storage objects, review items, exports, and UI states are affected?
- What must never happen with financial/tax data?
- How will local and deployed behavior be verified?
- Which documentation or regression test must be updated?

Prefer autonomous fixes for obvious edge cases, validation, logs, UI-refresh behavior, regression tests, and Turkish error messages. Ask the user for product priority, pricing, legal/accounting ambiguity, third-party integrations, or missing real sample data.

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

## Severity Rules

P0 issues stop feature work until reproduced, fixed, tested, and deployed-checked:

- data loss
- wrong amounts saved as safe
- wrong client data visible or exported
- delete removes the wrong data or silently fails
- security/auth/privacy failure

P1 issues must be fixed quickly with a regression test:

- wrong module routing
- stuck upload
- broken export
- stale UI after state changes
- missing review items

P2/P3 issues such as polish, animation, broad dashboards, or early integrations must not distract from the pilot workflow.

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
