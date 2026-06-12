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
4. Gemini muss streng JSON zurückgeben.
5. Das Backend normalisiert und validiert die Felder.
6. Gute Felder landen direkt in `Fişler` oder `Z Raporları`.
7. Unsichere, fehlende oder widersprüchliche Felder landen in `Kontrol`.
8. Wenn Gemini nicht verfügbar ist, nutzt das System die bisherige lokale OCR als Fallback.

## Umgebung

Lokal:

```bash
export GEMINI_API_KEY="DEIN_KEY"
export MALIYARDIMCI_GEMINI_MODEL="gemini-3.5-flash"
python3 -m malipilot.server
```

Vercel:

- Project Settings öffnen.
- Environment Variables öffnen.
- `GEMINI_API_KEY` eintragen.
- Optional `MALIYARDIMCI_GEMINI_MODEL` eintragen.
- Danach neu deployen.

Der API-Key darf nie in GitHub, Markdown, Screenshots oder Testdateien gespeichert werden.

## Technische Dateien

- `malipilot/ai_extractor.py`: Gemini REST-Aufruf, JSON-Schema, Normalisierung.
- `malipilot/server.py`: Upload-Ablauf, Gemini zuerst, lokale OCR als Fallback.
- `static/index.html`: sichtbare Anzeige, ob Gemini oder lokale OCR aktiv ist.
- `static/app.js`: rendert den aktuellen Belge-okuma-Status.
- `GEMINI_BELEG_PIPELINE.md`: diese Arbeitsnotiz.
- `README.md`: kurze öffentliche Setup-Anleitung.

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
- Große PDF-Bündel müssen später über einen robusteren Datei-Upload oder Seitensplitting verarbeitet werden.
- Vercel speichert aktuell keine Daten dauerhaft; für echten Pilotbetrieb braucht es Postgres/Supabase/Neon plus Dateispeicher.

## Nächste Ausbaustufe

1. Persistente Datenbank anbinden.
2. Upload-Dateien dauerhaft speichern.
3. Gemini-Rohantworten auditierbar speichern.
4. Pro Beleg eine Vorher/Nachher-Bewertung erfassen.
5. Ali-Testpaket mit 20 Belegen laufen lassen.
6. Fehlerliste aus den 20 Belegen in Parser- und Prompt-Regeln übersetzen.

