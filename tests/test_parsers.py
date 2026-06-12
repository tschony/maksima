import os
import tempfile
import unittest
from pathlib import Path

from malipilot.exporters import write_workbook
from malipilot.ai_extractor import (
    GeminiExtractionError,
    MAX_INLINE_BYTES,
    MAX_PDF_BYTES,
    extract_with_gemini,
    gemini_diagnostic,
    normalize_gemini_receipt,
    normalize_gemini_z_report,
    schema_for,
    should_use_file_api,
    unwrap_gemini_file_response,
)
from malipilot.ocr import extract_receipt, extract_z_report
from malipilot.parsers import parse_bank_file, parse_decimal, read_xlsx


class ParserTests(unittest.TestCase):
    def test_parse_turkish_decimal(self):
        self.assertEqual(str(parse_decimal("1.234,56 TL")), "1234.56")
        self.assertEqual(str(parse_decimal("-45,10")), "-45.10")

    def test_bank_csv_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bank.csv"
            path.write_text(
                "Tarih;Açıklama;Borç;Alacak;Bakiye\n"
                "01.06.2026;SGK ODEME;1.250,00;;8.750,00\n"
                "02.06.2026;MUSTERI TAHSILAT;;2.000,00;10.750,00\n",
                encoding="utf-8",
            )
            result = parse_bank_file(path, client_id=1, period="2026-06", bank_name="TestBank", rules=[])
            self.assertEqual(len(result.rows), 2)
            self.assertEqual(result.rows[0]["date"], "2026-06-01")
            self.assertEqual(result.rows[0]["debit"], "1250.00")
            self.assertEqual(result.rows[0]["suggested_account_code"], "361")

    def test_z_report_extraction(self):
        raw = "BEKO Z NO: 12345 Tarih 05.06.2026 KDV %20 120,00 GENEL TOPLAM 720,00 NAKIT 200,00 KREDI 520,00"
        item = extract_z_report(raw, client_id=1, period="2026-06", source_file="z.jpg")
        self.assertEqual(item["report_date"], "2026-06-05")
        self.assertEqual(item["z_no"], "12345")
        self.assertEqual(item["gross_total"], "720.00")

    def test_z_report_extraction_when_ocr_drops_commas(self):
        raw = "BEKO\nTarih 05062026\nKDV%20 12000\nGENEL TOPLAM 72000\nNAKIT20000 KREDI 520.00"
        item = extract_z_report(raw, client_id=1, period="2026-06", source_file="z.jpg")
        self.assertEqual(item["report_date"], "2026-06-05")
        self.assertEqual(item["gross_total"], "720.00")

    def test_receipt_extraction(self):
        raw = "MARKET AŞ\nTarih: 06.06.2026\nVKN 1234567890\nKDV 18,00\nTOPLAM 118,00"
        item = extract_receipt(raw, client_id=1, period="2026-06", source_file="fis.jpg")
        self.assertEqual(item["receipt_date"], "2026-06-06")
        self.assertEqual(item["vkn_tckn"], "1234567890")
        self.assertEqual(item["bookkeeping_status"], "uygun")

    def test_receipt_extraction_from_iphone_scan_ocr(self):
        raw = (
            "E-ARŞİV FATURA\n"
            "A101 YENI MAGAZACILIK A.\n"
            "USKUDAR/9480423762\n"
            "Tarih: 10/05/2026 Saat : 19:50\n"
            "Belge No: 737400210608050171\n"
            "MAL/HIZMET TOPLAM TUTARI * 237.62\n"
            "TOPKDV * 2.38\n"
            "ÖDENECEK TUTAR * 240.00\n"
            "KREDI KARTI *240.00\n"
        )
        item = extract_receipt(raw, client_id=1, period="2026-06", source_file="Fiş 01 - scan.pdf")
        self.assertEqual(item["receipt_date"], "2026-05-10")
        self.assertEqual(item["merchant_name"], "A101 YENI MAGAZACILIK A.")
        self.assertEqual(item["vkn_tckn"], "9480423762")
        self.assertEqual(item["document_no"], "737400210608050171")
        self.assertEqual(item["gross_total"], "240.00")
        self.assertEqual(item["vat_total"], "2.38")
        self.assertEqual(item["payment_method"], "kart")
        self.assertTrue(item["needs_review"])

    def test_gemini_receipt_normalization_marks_complete_receipt_safe(self):
        item = normalize_gemini_receipt(
            {
                "receipt_date": "10/05/2026",
                "merchant_name": "A101 YENI MAGAZACILIK A.Ş.",
                "vkn_tckn": "9480423762",
                "document_no": "737400210608050171",
                "gross_total": "240,00",
                "vat_total": "2,38",
                "payment_method": "kart",
                "bookkeeping_status": "uygun",
                "confidence": 0.92,
                "needs_review": False,
            },
            client_id=1,
            period="2026-06",
            source_file="fis.pdf",
        )
        self.assertEqual(item["receipt_date"], "2026-05-10")
        self.assertEqual(item["gross_total"], "240.00")
        self.assertEqual(item["vat_total"], "2.38")
        self.assertFalse(item["needs_review"])

    def test_gemini_receipt_normalization_keeps_missing_tax_id_in_review(self):
        item = normalize_gemini_receipt(
            {
                "receipt_date": "2026-05-10",
                "merchant_name": "RESTORAN",
                "gross_total": "850.00",
                "bookkeeping_status": "uygun",
                "confidence": 0.91,
                "needs_review": False,
            },
            client_id=1,
            period="2026-06",
            source_file="fis.pdf",
        )
        self.assertEqual(item["bookkeeping_status"], "eksik")
        self.assertTrue(item["needs_review"])

    def test_gemini_receipt_schema_has_no_empty_enum_values(self):
        schema = schema_for("receipt")
        payment_schema = schema["properties"]["items"]["items"]["properties"]["payment_method"]
        self.assertNotIn("", payment_schema["enum"])
        self.assertIn("belirsiz", payment_schema["enum"])

    def test_gemini_receipt_normalization_treats_unknown_payment_as_blank(self):
        item = normalize_gemini_receipt(
            {
                "receipt_date": "2026-05-10",
                "merchant_name": "A101",
                "vkn_tckn": "9480423762",
                "gross_total": "240.00",
                "payment_method": "belirsiz",
                "bookkeeping_status": "uygun",
                "confidence": 0.91,
                "needs_review": False,
            },
            client_id=1,
            period="2026-06",
            source_file="fis.pdf",
        )
        self.assertEqual(item["payment_method"], "")

    def test_gemini_z_report_normalization(self):
        item = normalize_gemini_z_report(
            {
                "report_date": "05.06.2026",
                "device_brand": "BEKO",
                "z_no": "12345",
                "gross_total": "720,00",
                "vat_lines": [{"rate": "20", "amount": "120,00"}],
                "payment_breakdown": {"cash": "200,00", "card": "520,00"},
                "confidence": 0.9,
                "needs_review": False,
            },
            client_id=1,
            period="2026-06",
            source_file="z.pdf",
        )
        self.assertEqual(item["report_date"], "2026-06-05")
        self.assertEqual(item["gross_total"], "720.00")
        self.assertFalse(item["needs_review"])

    def test_large_pdf_uses_gemini_files_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "large.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n")
            with pdf_path.open("r+b") as handle:
                handle.truncate(MAX_INLINE_BYTES + 1)
            txt_path = Path(tmp) / "large.txt"
            txt_path.write_text("x", encoding="utf-8")
            with txt_path.open("r+b") as handle:
                handle.truncate(MAX_INLINE_BYTES + 1)

            self.assertTrue(should_use_file_api(pdf_path, "application/pdf"))
            self.assertFalse(should_use_file_api(txt_path, "text/plain"))

    def test_gemini_diagnostic_uses_files_api_for_large_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "large.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n")
            with pdf_path.open("r+b") as handle:
                handle.truncate(MAX_INLINE_BYTES + 1)

            diagnostic = gemini_diagnostic(pdf_path, "receipt", "large.pdf")

            self.assertEqual(diagnostic["input_method"], "files_api")
            self.assertEqual(diagnostic["file_size"], MAX_INLINE_BYTES + 1)

    def test_oversized_pdf_returns_gemini_diagnostic_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "huge.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n")
            with pdf_path.open("r+b") as handle:
                handle.truncate(MAX_PDF_BYTES + 1)

            old_key = os.environ.get("GEMINI_API_KEY")
            os.environ["GEMINI_API_KEY"] = "test-key"
            try:
                with self.assertRaises(GeminiExtractionError) as raised:
                    extract_with_gemini(pdf_path, "receipt", client_id=1, period="2026-06", filename="huge.pdf")
            finally:
                if old_key is None:
                    os.environ.pop("GEMINI_API_KEY", None)
                else:
                    os.environ["GEMINI_API_KEY"] = old_key

            self.assertEqual(raised.exception.diagnostic["status"], "failed")
            self.assertIn("PDF Gemini sınırını aşıyor", raised.exception.diagnostic["error_message"])

    def test_gemini_file_response_unwraps_wrapped_file(self):
        wrapped = {"file": {"name": "files/test", "uri": "https://example.test/file", "state": "ACTIVE"}}
        self.assertEqual(unwrap_gemini_file_response(wrapped)["name"], "files/test")

    def test_xlsx_roundtrip_for_simple_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.xlsx"
            write_workbook(path, {"Sheet1": [{"Tarih": "01.06.2026", "Açıklama": "Test", "Tutar": "10,00"}]})
            rows = read_xlsx(path)
            self.assertEqual(rows[0]["Açıklama"], "Test")


if __name__ == "__main__":
    unittest.main()
