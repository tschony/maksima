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

## Erledigt, wenn

- `/api/state` zeigt `"storage": {"provider": "supabase"}`.
- Ein Mükellef bleibt nach Refresh erhalten.
- Ein hochgeladener Fiş bleibt nach Refresh erhalten.
- Die Review Queue bleibt nach Refresh erhalten.
- Supabase Storage enthält die hochgeladene Datei.
