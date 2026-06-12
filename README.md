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

## Gemini ile Belge Okuma

Fiş ve Z raporu yüklemelerinde Gemini kullanmak için:

```bash
cp .env.example .env.local
# .env.local dosyasına gerçek Gemini key girilir
python3 -m malipilot.server
```

Vercel'de aynı değerler Project Settings -> Environment Variables bölümüne eklenir. Ayrıntılı akış için `GEMINI_BELEG_PIPELINE.md` dosyasına bak.

## Bu Pilot Ne Yapar?

- Anonim mükellef oluşturma.
- Banka dosyası yükleme ve satırları standart formata çevirme.
- Z raporu ve fiş/gider belgesi yükleme.
- Yerel OCR ile alanları tahmin etme.
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
