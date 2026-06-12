# Supabase + Vercel Setup

Stand: 2026-06-12

## Ziel

Die Live-Seite darf keine Belege und Ergebnisse verlieren, wenn die Seite aktualisiert wird oder Vercel eine neue Function startet.

## Architektur

- Vercel hostet die Web-App und Python-API.
- Supabase Postgres speichert strukturierte Daten.
- Supabase Storage speichert hochgeladene Dokumente.
- ChatGPT liest Fiş/Z raporu und gibt strukturierte Felder zurück.

## Supabase

Benötigt wird ein Supabase-Projekt mit:

- Postgres Tabellen aus `supabase_schema.sql`
- privater Storage Bucket `documents`
- Service Role Key für den Server

Aktuelles Projekt:

```text
Projektname: maksima
Projekt-Ref: ybrombuglnzfkrgwtdec
Region: eu-central-1
URL: https://ybrombuglnzfkrgwtdec.supabase.co
Bucket: documents
```

Die Tabellen sind:

- `clients`
- `documents`
- `bank_transactions`
- `z_reports`
- `receipts`
- `account_code_rules`
- `feedback`
- `extraction_runs`

## Vercel Environment Variables

In Vercel unter Project Settings -> Environment Variables eintragen:

```env
SUPABASE_URL=https://ybrombuglnzfkrgwtdec.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_STORAGE_BUCKET=documents
OPENAI_API_KEY=...
MALIYARDIMCI_AI_PROVIDER=openai
MALIYARDIMCI_OPENAI_MODEL=gpt-5.4-mini
```

Alle Variablen für `Production and Preview` setzen.

`SUPABASE_SERVICE_ROLE_KEY` und `OPENAI_API_KEY` müssen geheim bleiben. Sie gehören nie in GitHub, Screenshots oder Frontend-Code.

Den `SUPABASE_SERVICE_ROLE_KEY` findest du in Supabase unter:

```text
Project Settings -> API -> Project API keys -> service_role
```

Der Supabase-Connector zeigt aus Sicherheitsgründen nur publishable/anon keys, nicht den service_role key.

## Lokale Entwicklung

Lokal kann die App weiter ohne Supabase laufen. Dann nutzt sie SQLite.

Für lokalen Supabase-Test:

```bash
cp .env.example .env.local
```

Dann echte Werte in `.env.local` eintragen und Server neu starten:

```bash
python3 -m malipilot.server
```

## Löschregeln

Beim Löschen muss Supabase genauso geprüft werden wie SQLite.

Ein einzelner extrahierter Datensatz löscht:

- passenden Eintrag in `bank_transactions`, `z_reports` oder `receipts`
- zugehörige `feedback`-Einträge
- nicht das ursprüngliche Dokument

Eine komplette yükleme unter `Yüklemeler` löscht:

- `documents`
- alle verbundenen `bank_transactions`
- alle verbundenen `z_reports`
- alle verbundenen `receipts`
- alle verbundenen `feedback`-Einträge
- alle verbundenen `extraction_runs`
- das Storage-Objekt im Bucket `documents`, soweit Supabase Storage es erlaubt

Wichtig: Ein DELETE gilt nur als erfolgreich, wenn die wichtigen Tabellen tatsächlich gelöschte Zeilen zurückgeben oder danach verifiziert wird, dass der Datensatz weg ist. Ein bloßer HTTP-Request reicht nicht als Erfolg.

## Vercel-Routen

Lokale Routen in `malipilot/server.py` reichen nicht. Vercel nutzt `api/index.py`.

Diese produktkritischen Routen müssen in beiden Einstiegspunkten vorhanden sein:

- `/api/state`
- `/api/clients`
- `/api/upload`
- `/api/upload-url`
- `/api/process-stored-upload`
- `/api/review-item`
- `/api/delete-item`
- `/api/delete-document`
- `/api/export`
- `/api/document`

## Erledigt, wenn

- `/api/state` zeigt `"storage": {"provider": "supabase"}`.
- Ein Mükellef bleibt nach Refresh erhalten.
- Ein hochgeladener Fiş bleibt nach Refresh erhalten.
- Die Review Queue bleibt nach Refresh erhalten.
- Supabase Storage enthält die hochgeladene Datei.
- Löschen bleibt nach Refresh sichtbar gelöscht.
- `/api/delete-item` und `/api/delete-document` liefern live türkische Validierungsfehler statt `Sayfa bulunamadı`, wenn sie ohne Payload getestet werden.
