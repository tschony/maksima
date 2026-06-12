# QA Checklist

Use this checklist before saying a workflow is fixed.

## Smoke Test

- Open the app.
- Confirm `/api/state` loads.
- Select or create a `Mükellef`.
- Refresh the browser and confirm the selected data still exists.

## Upload Flow

- Upload one Banka file.
- Upload one Z raporu image/PDF.
- Upload one Fiş image/PDF.
- Upload multiple files at once.
- Confirm each processed item appears in the correct module.
- Confirm records are grouped by real document month, not only upload month.
- Confirm uncertain items appear in `Kontrol`.
- Confirm visible UI message says what happened.

## Library Flow

- Open `Banka`.
- Open `Z raporları`.
- Open `Fişler`.
- Open `Yüklemeler`.
- Click a month folder.
- Use search/filter.
- Confirm top totals update:
  - `Kayıt sayısı`
  - `Toplam tutar`
  - `Toplam KDV`

## Review Flow

- Click `Kontrol et`.
- Open the original document.
- Change at least one field.
- Save and remove from control.
- Confirm the row disappears from `Kontrol`.
- Confirm the library view updates after refresh.

## Delete Flow

- Delete a single Banka row.
- Delete a single Z raporu row.
- Delete a single Fiş row.
- Delete an upload under `Yüklemeler`.
- Confirm related rows disappear.
- Refresh the browser and confirm deleted data stays deleted.
- Confirm deletion errors are visible in the UI.
- Confirm Vercel routes exist:

```bash
curl -sS -X POST https://maksima.vercel.app/api/delete-item \
  -H 'Content-Type: application/json' \
  -d '{}'

curl -sS -X POST https://maksima.vercel.app/api/delete-document \
  -H 'Content-Type: application/json' \
  -d '{}'
```

These should return Turkish validation errors, not `Sayfa bulunamadı`.

## Export Flow

- Export for selected `Mükellef` and `Dönem`.
- Open the generated workbook.
- Confirm Turkish characters are preserved.
- Confirm numeric totals stay usable.
- Confirm review rows are separated.

## Required Checks

Run after functional changes:

```bash
python3 -m unittest discover -s tests
python3 -m compileall malipilot api tests
node --check static/app.js
```
