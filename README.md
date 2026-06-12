# MaliYardımcı

Mali müşavir büroları için yerel pilot uygulama. Banka hareketlerini, Z raporlarını ve fiş/gider belgelerini kontrol edilebilir tablolara dönüştürür ve çıktı dosyası üretir.

Bu proje ilk aşamada yerel çalışır. Banka bağlantısı kurmaz, Luca şifresi istemez ve nihai muhasebe kararını otomatik vermez.

## Çalıştırma

```bash
python3 -m malipilot.server
```

Open:

```text
http://127.0.0.1:8765
```

## Test

```bash
python3 -m unittest discover -s tests
```

## ChatGPT ile Belge Okuma

Fiş ve Z raporu yüklemelerinde OpenAI/ChatGPT kullanmak için:

```bash
cp .env.example .env.local
# .env.local dosyasına gerçek OpenAI key girilir
python3 -m malipilot.server
```

Vercel'de aynı değerler Project Settings -> Environment Variables bölümüne eklenir:

```text
OPENAI_API_KEY=...
MALIYARDIMCI_AI_PROVIDER=openai
MALIYARDIMCI_OPENAI_MODEL=gpt-5.4-mini
```

`MALIYARDIMCI_OPENAI_MODEL` boş bırakılırsa varsayılan olarak `gpt-5.4-mini` kullanılır. Gemini yalnızca `MALIYARDIMCI_AI_PROVIDER=gemini` seçilirse devreye alınmalıdır.

## Kalıcı Veri Saklama

Vercel üzerinde verilerin sayfa yenilenince kaybolmaması için Supabase gerekir:

```bash
SUPABASE_URL=https://dein-projekt.supabase.co
SUPABASE_SERVICE_ROLE_KEY=dein_service_role_key
SUPABASE_STORAGE_BUCKET=documents
```

Supabase şeması `supabase_schema.sql` dosyasındadır. Bu değişkenler yoksa uygulama yerel SQLite ile çalışır.

## Bu Pilot Ne Yapar?

- Anonim mükellef oluşturma.
- Banka dosyası yükleme ve satırları standart formata çevirme.
- Z raporu ve fiş/gider belgesi yükleme.
- `Z GÜNLÜK RAPORU` belgelerini müşteri fişinden ayırma; `KÜM TOP` ve `KÜM KDV` değerlerini günlük tutar olarak kaydetmeme. Ayrıntı: `Z_RAPORU_KURALLARI.md`.
- ChatGPT varsa belge alanlarını doğrudan çıkarma; yoksa yerel OCR ile alanları tahmin etme.
- Büyük PDF'leri önce Supabase Storage'a, ardından ChatGPT dosya okuma akışına yönlendirme.
- Belge okuma denemelerini `extraction_runs` tablosunda denetlenebilir şekilde saklama.
- Düşük güvenli veya eksik alanları kontrol kuyruğuna alma.
- Düzeltmeleri saklama.
- Mükellef ve dönem bazlı tablo çıktısı üretme.

## Desteklenen İlk Akışlar

- `Banka hareketleri`: CSV, TSV, XLSX ve metin tabanlı XLS dosyaları.
- `Z raporları`: PDF veya görsel dosyalar.
- `Fişler`: PDF veya görsel dosyalar.
- `Kontrol`: eksik, düşük güvenli veya şüpheli kayıtları ayrı gösterme.
- `Çıktı`: tablo dosyası üretme.

## Pilot Sınırları

- Luca yerine geçmez.
- Banka API bağlantısı kurmaz.
- Resmi e-Fatura entegratörü değildir.
- Muhasebe kaydı oluşturmaz; veriyi hazırlar ve kontrol ettirir.
- İlk testlerde anonimleştirilmiş veri kullanılmalıdır.
