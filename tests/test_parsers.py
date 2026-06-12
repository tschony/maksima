import json
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
    friendly_openai_error,
    gemini_diagnostic,
    normalize_gemini_receipt,
    normalize_gemini_z_report,
    openai_request_body,
    openai_response_text,
    openai_schema_for,
    prompt_for,
    schema_for,
    should_use_file_api,
    unwrap_gemini_file_response,
)
from malipilot.ocr import extract_receipt, extract_z_report
from malipilot.parsers import parse_bank_file, parse_decimal, read_xlsx
from malipilot import persistence, storage
from malipilot.persistence import (
    delete_document_record,
    delete_extracted_item_record,
    get_document_record,
    insert_document_record,
    insert_extracted_item_record,
)
from malipilot.server import has_z_report_signal, should_reroute_receipt_to_z


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

    def test_z_report_extraction_ignores_cumulative_totals(self):
        raw = (
            "Z GÜNLÜK RAPORU\n"
            "TARIH: 04/05/26\n"
            "Z SAYAC 673\n"
            "MALI VERI\n"
            "TOP *0,00\n"
            "KDV *0,00\n"
            "KÜM TOP *4.670.639,68\n"
            "KÜM KDV *368.516,78\n"
            "Z NO:0673\n"
            "VERGI DOKUMU\n"
            "%20 TOPLAM *3.255,00\n"
            "KDV *542,50\n"
            "MALI VERI\n"
            "TOP *3.255,00\n"
            "KDV *542,50\n"
            "KÜM TOP *4.673.894,68\n"
            "KÜM KDV *369.059,28\n"
            "Z NO:0674\n"
        )
        item = extract_z_report(raw, client_id=1, period="2026-06", source_file="z.jpg")
        vat_lines = json.loads(item["vat_lines"])

        self.assertEqual(item["report_date"], "2026-05-04")
        self.assertEqual(item["z_no"], "0674")
        self.assertEqual(item["gross_total"], "3255.00")
        self.assertEqual(vat_lines[-1], {"rate": "20", "amount": "542.50"})

    def test_receipt_extraction_marks_z_report_as_not_customer_receipt(self):
        raw = "Z GÜNLÜK RAPORU\nTARIH: 04/05/26\nZ SAYAC 674\nTOP *3.255,00\nKDV *542,50\n"
        item = extract_receipt(raw, client_id=1, period="2026-06", source_file="z-as-fis.jpg")

        self.assertEqual(item["bookkeeping_status"], "islenmez")
        self.assertEqual(item["document_no"], "674")
        self.assertEqual(item["gross_total"], "")
        self.assertIn("Z raporu", item["raw_text"])

    def test_receipt_upload_with_z_note_is_rerouted_to_z(self):
        diagnostic = {"raw_response": '{"items":[],"document_notes":"Bu belge Z GÜNLÜK RAPORU gibi görünüyor."}'}

        self.assertTrue(should_reroute_receipt_to_z("receipt", [], ["ChatGPT yapılandırılmış kayıt döndürmedi"], diagnostic))
        self.assertFalse(should_reroute_receipt_to_z("receipt", [{"gross_total": "10.00"}], [], diagnostic))
        self.assertFalse(should_reroute_receipt_to_z("z", [], [], diagnostic))

    def test_z_report_signal_accepts_turkish_and_english_notes(self):
        self.assertTrue(has_z_report_signal("Z SAYAÇ 0674"))
        self.assertTrue(has_z_report_signal("looks like a z report"))
        self.assertFalse(has_z_report_signal("normal market receipt"))

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
        self.assertEqual(item["period"], "2026-05")
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
        self.assertEqual(item["period"], "2026-05")
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

    def test_openai_receipt_schema_is_strict_ready(self):
        schema = openai_schema_for("receipt")
        item_schema = schema["properties"]["items"]["items"]
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(item_schema["additionalProperties"])
        self.assertEqual(set(item_schema["required"]), set(item_schema["properties"].keys()))
        self.assertIn("belirsiz", item_schema["properties"]["payment_method"]["enum"])

    def test_z_prompt_warns_against_cumulative_totals(self):
        prompt = prompt_for("z")
        self.assertIn("KÜM TOP", prompt)
        self.assertIn("kümülatif", prompt)
        self.assertIn("günlük", prompt)

    def test_openai_image_request_uses_responses_input_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "fis.jpeg"
            image_path.write_bytes(b"fake-image")

            old_model = os.environ.get("MALIYARDIMCI_OPENAI_MODEL")
            os.environ["MALIYARDIMCI_OPENAI_MODEL"] = "test-model"
            try:
                body = openai_request_body(image_path, "receipt", "image/jpeg")
            finally:
                if old_model is None:
                    os.environ.pop("MALIYARDIMCI_OPENAI_MODEL", None)
                else:
                    os.environ["MALIYARDIMCI_OPENAI_MODEL"] = old_model

            content = body["input"][0]["content"]
            self.assertEqual(body["model"], "test-model")
            self.assertEqual(content[1]["type"], "input_image")
            self.assertTrue(content[1]["image_url"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(body["text"]["format"]["type"], "json_schema")

    def test_openai_pdf_request_uses_input_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "z.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n")

            body = openai_request_body(pdf_path, "z", "application/pdf")
            content = body["input"][0]["content"]

            self.assertEqual(content[1]["type"], "input_file")
            self.assertEqual(content[1]["filename"], "z.pdf")
            self.assertTrue(content[1]["file_data"].startswith("data:application/pdf;base64,"))

    def test_openai_response_text_extracts_output_text(self):
        response = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": '{"items": [], "document_notes": ""}'},
                    ]
                }
            ]
        }
        self.assertEqual(openai_response_text(response), '{"items": [], "document_notes": ""}')

    def test_openai_error_message_is_friendly(self):
        detail = '{"error":{"code":"rate_limit_exceeded","message":"Too many requests","type":"rate_limit_error"}}'
        self.assertEqual(friendly_openai_error(429, detail), "OpenAI kullanım sınırı geçici olarak doldu. Birkaç dakika sonra tekrar dene.")

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


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_values = {
            "storage_DATA_DIR": storage.DATA_DIR,
            "storage_UPLOAD_DIR": storage.UPLOAD_DIR,
            "storage_EXPORT_DIR": storage.EXPORT_DIR,
            "storage_DB_PATH": storage.DB_PATH,
            "persistence_SUPABASE_URL": persistence.SUPABASE_URL,
            "persistence_SUPABASE_SERVICE_ROLE_KEY": persistence.SUPABASE_SERVICE_ROLE_KEY,
        }
        root = Path(self.tmp.name)
        storage.DATA_DIR = root
        storage.UPLOAD_DIR = root / "uploads"
        storage.EXPORT_DIR = root / "exports"
        storage.DB_PATH = root / "malipilot.sqlite3"
        persistence.SUPABASE_URL = ""
        persistence.SUPABASE_SERVICE_ROLE_KEY = ""

    def tearDown(self):
        storage.DATA_DIR = self.old_values["storage_DATA_DIR"]
        storage.UPLOAD_DIR = self.old_values["storage_UPLOAD_DIR"]
        storage.EXPORT_DIR = self.old_values["storage_EXPORT_DIR"]
        storage.DB_PATH = self.old_values["storage_DB_PATH"]
        persistence.SUPABASE_URL = self.old_values["persistence_SUPABASE_URL"]
        persistence.SUPABASE_SERVICE_ROLE_KEY = self.old_values["persistence_SUPABASE_SERVICE_ROLE_KEY"]
        self.tmp.cleanup()

    def test_delete_extracted_item_keeps_original_document(self):
        doc_id = insert_document_record(1, "2026-06", "receipt", "fis.pdf", str(Path(self.tmp.name) / "fis.pdf"), "done")
        insert_extracted_item_record("receipt", doc_id, receipt_item(doc_id=doc_id))
        with storage.connect() as conn:
            item_id = conn.execute("select id from receipts").fetchone()[0]
            conn.execute("insert into feedback (item_type, item_id, rating) values ('receipt', ?, 'yanlis')", (item_id,))
            conn.commit()

        result = delete_extracted_item_record("receipt", item_id, 1)

        with storage.connect() as conn:
            self.assertEqual(conn.execute("select count(*) from receipts").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from feedback").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from documents").fetchone()[0], 1)
        self.assertEqual(result["deleted"], "receipt")

    def test_delete_document_removes_related_rows_feedback_and_file(self):
        file_path = Path(self.tmp.name) / "z.jpg"
        file_path.write_bytes(b"test")
        doc_id = insert_document_record(1, "2026-06", "z", "z.jpg", str(file_path), "done")
        insert_extracted_item_record("z", doc_id, z_item(doc_id=doc_id))
        with storage.connect() as conn:
            item_id = conn.execute("select id from z_reports").fetchone()[0]
            conn.execute("insert into feedback (item_type, item_id, rating) values ('z', ?, 'yanlis')", (item_id,))
            conn.execute("insert into extraction_runs (document_id, provider, status) values (?, 'test', 'ok')", (doc_id,))
            conn.commit()

        result = delete_document_record(doc_id, 1)

        with storage.connect() as conn:
            self.assertEqual(conn.execute("select count(*) from documents").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from z_reports").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from feedback").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from extraction_runs").fetchone()[0], 0)
        self.assertIsNone(get_document_record(doc_id, 1))
        self.assertFalse(file_path.exists())
        self.assertEqual(result["deleted"], "document")


def receipt_item(doc_id: int = 1) -> dict:
    return {
        "client_id": 1,
        "period": "2026-06",
        "source_file": "fis.pdf",
        "receipt_date": "2026-06-01",
        "merchant_name": "MARKET",
        "vkn_tckn": "1234567890",
        "document_no": "1",
        "gross_total": "100.00",
        "vat_total": "20.00",
        "payment_method": "kart",
        "bookkeeping_status": "uygun",
        "confidence": 0.95,
        "needs_review": False,
        "raw_text": "",
    }


def z_item(doc_id: int = 1) -> dict:
    return {
        "client_id": 1,
        "period": "2026-06",
        "source_file": "z.jpg",
        "report_date": "2026-06-01",
        "device_brand": "",
        "device_serial": "",
        "z_no": "1",
        "gross_total": "100.00",
        "vat_lines": "[]",
        "payment_breakdown": "{}",
        "confidence": 0.95,
        "needs_review": False,
        "raw_text": "",
    }


if __name__ == "__main__":
    unittest.main()
