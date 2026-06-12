# Gemini Belge Okuma Planı

Stand: 2026-06-12

## Ziel

MaliYardımcı soll bei Testbelegen nicht nur Rohtext zeigen, sondern direkt strukturierte Felder erzeugen:

- Fiş tarihi
- Satıcı adı
- VKN/TCKN
- Belge no
- Toplam tutar
- KDV tutarı
- Ödeme şekli
- İşleme durumu
- Güven oranı
- Kontrol gerekli mi?

Für Z raporları werden entsprechend Datum, Z no, cihaz bilgisi, toplam tutar, KDV satırları und ödeme dağılımı extrahiert.

## Arbeitsweise

1. Nutzer lädt auf der Webseite einen Beleg oder Z raporu hoch.
2. Backend speichert die Datei temporär.
3. Wenn ein Gemini-Key gesetzt ist, wird die Datei an Gemini geschickt.
   - Kleine Dateien gehen direkt über `inlineData`.
   - Größere PDFs gehen über die Gemini Files API, damit Vercel und Gemini nicht an der Base64-Größe scheitern.
4. Gemini muss streng JSON zurückgeben.
5. Das Backend normalisiert und validiert die Felder.
6. Gute Felder landen direkt in `Fişler` oder `Z Raporları`.
7. Unsichere, fehlende oder widersprüchliche Felder landen in `Kontrol`.
8. Wenn Gemini lokal nicht verfügbar ist, nutzt das System die bisherige lokale OCR als Fallback.
9. Auf Vercel wird kein leerer OCR-Fallback erzeugt: Wenn Gemini nicht liefert, wird das Dokument als `failed` markiert.
10. Jeder Gemini-Lauf wird in `extraction_runs` protokolliert: Modell, Dateigröße, Methode, Dauer, Status, Antwort oder Fehler.

## Umgebung

Lokal:

```bash
cp .env.example .env.local
```

Dann `.env.local` öffnen und den echten Key eintragen:

```bash
GEMINI_API_KEY=DEIN_ECHTER_KEY
MALIYARDIMCI_GEMINI_MODEL=gemini-3.5-flash
```

Danach den lokalen Server neu starten:

```bash
python3 -m malipilot.server
```

Vercel:

- Project Settings öffnen.
- Environment Variables öffnen.
- `GEMINI_API_KEY` eintragen.
- Optional `MALIYARDIMCI_GEMINI_MODEL` eintragen.
- Danach neu deployen.

Der API-Key darf nie in GitHub, Markdown, Screenshots oder Testdateien gespeichert werden.
`.env.local` ist durch `.gitignore` geschützt und bleibt lokal.

## Technische Dateien

- `malipilot/ai_extractor.py`: Gemini REST-Aufruf, JSON-Schema, Normalisierung.
- `.env.example`: Vorlage für lokale Gemini-Konfiguration ohne echten Key.
- `.env.local`: lokale echte Konfiguration, wird nicht versioniert.
- `malipilot/server.py`: Upload-Ablauf, Gemini zuerst, lokale OCR nur außerhalb von Vercel als Fallback.
- `malipilot/persistence.py`: Speichert Dokumentstatus und Gemini-Diagnose.
- `supabase_schema.sql`: Enthält die Tabelle `extraction_runs` für auditierbare Verarbeitungen.
- `static/index.html`: sichtbare Anzeige, ob Gemini oder lokale OCR aktiv ist.
- `static/app.js`: rendert den aktuellen Belge-okuma-Status.
- `GEMINI_BELEG_PIPELINE.md`: diese Arbeitsnotiz.
- `README.md`: kurze öffentliche Setup-Anleitung.

## Große PDFs

Für große PDF-Dateien gibt es zwei Grenzen:

- Browser zu Vercel: Dateien über ca. 3 MB werden direkt in Supabase Storage geladen und danach verarbeitet.
- Vercel zu Gemini: PDFs über ca. 18 MB werden nicht mehr als Base64 `inlineData` gesendet, sondern über die Gemini Files API hochgeladen und dann per `fileUri` verarbeitet.
- PDFs über ca. 50 MB werden nicht still verarbeitet, sondern klar als zu groß abgelehnt.

Wenn eine Datei trotz Files API zu lange dauert, ist der nächste technische Schritt eine asynchrone Job-Warteschlange mit Statusanzeige statt synchronem Warten im Browser.

## Testnotizen pro Beleg

Bei jedem echten Test mit Ali oder eigenen Belegen sollte festgehalten werden:

| Feld | Notiz |
| --- | --- |
| Dosya adı | Welche Datei wurde hochgeladen? |
| Belge türü | Fiş, e-arşiv, Z raporu oder gemischt? |
| Gemini Ergebnis | Wurden einzelne Belege korrekt getrennt? |
| Fehlende Felder | Welche Felder blieben leer? |
| Falsche Felder | Was wurde falsch gelesen? |
| Kontrol oranı | Wie viele Zeilen mussten noch kontrolliert werden? |
| Zeitersparnis | War es schneller als manuell? |

## Validierungsregeln

Ein Fiş bleibt in der Kontrolle, wenn:

- Datum fehlt.
- Satıcı adı fehlt.
- VKN/TCKN fehlt.
- Toplam tutar fehlt.
- Gemini `confidence < 0.85` liefert.
- Gemini selbst `needs_review = true` setzt.

Ein Z raporu bleibt in der Kontrolle, wenn:

- Datum fehlt.
- Z no fehlt.
- Bruttosumme fehlt.
- Gemini `confidence < 0.85` liefert.
- Gemini selbst `needs_review = true` setzt.

## Grenzen

- Das System bucht nicht automatisch.
- Das System entscheidet nicht steuerlich final.
- Gemini darf vorschlagen, aber die App validiert.
- Sehr große PDF-Bündel können trotz Files API länger dauern; dann braucht es Job-Verarbeitung statt synchronem Upload.
- Vercel speichert die Pilotdaten über Supabase dauerhaft, wenn die Supabase-Umgebungsvariablen gesetzt sind.

## Nächste Ausbaustufe

1. Persistente Datenbank anbinden.
2. Upload-Dateien dauerhaft speichern.
3. Pro Beleg eine Vorher/Nachher-Bewertung erfassen.
4. Ali-Testpaket mit 20 Belegen laufen lassen.
5. Fehlerliste aus den 20 Belegen in Parser- und Prompt-Regeln übersetzen.
6. Asynchrone Job-Verarbeitung bauen, falls große PDFs weiterhin in Vercel-Zeitlimits laufen.
