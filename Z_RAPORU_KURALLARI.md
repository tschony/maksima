# Z Raporu Kuralları

Stand: 2026-06-12

## Belge Tipi

`Z GÜNLÜK RAPORU`, `Z RAPORU`, `Z SAYAÇ` veya `Z NO` görünen belge normal müşteri fişi değildir.
Bu belge, Türk Yeni Nesil ÖKC/fiskal kasa günlük kapanış raporudur.

Uygulama bu belgeyi şu şekilde düşünmelidir:

```json
{
  "document_type": "z_report",
  "country": "TR",
  "language": "tr",
  "is_customer_receipt": false
}
```

## Günlük Değerler

Tek bir Z raporunun muhasebe hazırlığı için esas değerler günlük satış ve günlük KDV değerleridir.

Örnek:

```text
%20 TOPLAM   3.255,00
KDV            542,50

TOP          3.255,00
KDV            542,50
Z NO: 0674
```

Bu kayıt için:

- `report_date`: `2026-05-04`
- `z_no`: `0674`
- `gross_total`: `3255.00`
- `vat_lines`: `[{"rate": "20", "amount": "542.50"}]`
- `currency`: `TRY` varsayımı yapılabilir, fakat veritabanında ayrı alan yoksa not olarak kalır.

## KÜM TOP ve KÜM KDV

`KÜM` kısaltması `kümülatif` demektir.

- `KÜM TOP`: kasanın bu Z raporuna kadar biriken toplam satış sayacı
- `KÜM KDV`: kasanın bu Z raporuna kadar biriken toplam KDV sayacı

Bunlar tek günün tutarı değildir. Bu yüzden `gross_total` veya günlük KDV olarak kaydedilmemelidir.

Örnek fark hesabı:

```text
Önceki Z 0673:
KÜM TOP  4.670.639,68
KÜM KDV    368.516,78

Sonraki Z 0674:
KÜM TOP  4.673.894,68
KÜM KDV    369.059,28
```

Fark:

- `4.673.894,68 - 4.670.639,68 = 3.255,00`
- `369.059,28 - 368.516,78 = 542,50`

Bu farklar günlük değerlerle uyuşuyorsa yardımcı doğrulama olarak kullanılabilir. Ama kümülatif değerlerin kendisi günlük belge tutarı değildir.

## Segmentasyon

Tek fotoğrafta birden fazla belge görünebilir:

- Aynı uzun kâğıt üzerinde iki ayrı Z raporu olabilir.
- Fotoğraf kenarında başka müşteri fişi veya tablo olabilir.
- Üstteki ve alttaki Z raporlarının `Z NO` değerleri farklı olabilir.

Kural:

1. Önce ana kâğıt şeridi belirlenir.
2. Her `Z NO` / `Z SAYAÇ` bölümü ayrı Z raporu olarak düşünülür.
3. Arka plandaki başka belge parçaları ayrı kayıt gibi uydurulmaz.
4. Emin olunmayan alanlar `needs_review=true` kalır.

## Fiş Modülü İçin Koruma

Bir kullanıcı yanlışlıkla Z raporunu `Fiş` modülüne yüklerse uygulama bunu müşteri fişi gibi kaydetmemelidir.

Beklenen davranış:

- Önce belge okuma notları ve OCR metni Z raporu sinyali için kontrol edilir.
- Mümkünse belge otomatik olarak `Z raporları` bölümüne aktarılır.
- Aktarım başarılıysa `z_reports` tablosuna Z kaydı yazılır ve yükleme kaydının modülü `z` yapılır.
- Aktarım yapılamazsa fiş olarak güvenli şekilde reddedilir:
  - `bookkeeping_status`: `islenmez`
  - `needs_review`: `true`
  - `gross_total`: boş
  - Not: `Z raporu olarak görünüyor; fiş olarak işlenmemeli.`
