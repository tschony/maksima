from __future__ import annotations

import base64
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .ai_extractor import AI_EXTRACTION_ERRORS, ai_model, ai_provider, extract_with_ai
from .exporters import export_filename, write_workbook
from .ocr import extract_receipt, extract_z_reports, is_z_report_text, run_ocr_pages
from .parsers import parse_bank_file
from .persistence import (
    EXPORT_DIR,
    api_state_payload,
    create_client_record,
    create_direct_upload,
    create_feedback_record,
    create_rule_record,
    create_z_device_record,
    delete_document_record,
    delete_extracted_item_record,
    ensure_ready,
    export_sheets,
    get_account_rules,
    get_clients,
    get_client,
    get_document_record,
    get_review_item_record,
    insert_bank_transaction,
    insert_document_record,
    insert_extracted_item_record,
    insert_extraction_run_record,
    mark_document_done,
    mark_document_failed,
    materialize_stored_upload,
    read_document_content,
    review_needed,
    store_upload,
    update_document_module,
    update_review_item_record,
)
from .storage import ROOT


STATIC_DIR = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    server_version = "MaliPilot/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(STATIC_DIR / "index.html")
        elif parsed.path.startswith("/static/"):
            self.serve_file(STATIC_DIR / parsed.path.removeprefix("/static/"))
        elif parsed.path == "/api/state":
            self.json_response(api_state())
        elif parsed.path == "/api/clients":
            self.json_response(get_clients())
        elif parsed.path == "/api/review-item":
            self.json_response(get_review_item(parse_qs(parsed.query)))
        elif parsed.path == "/api/export":
            self.handle_export(parse_qs(parsed.query))
        elif parsed.path == "/api/document":
            self.handle_document(parse_qs(parsed.query))
        else:
            self.error_json(HTTPStatus.NOT_FOUND, "Sayfa bulunamadı")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/clients":
                self.json_response(create_client(payload))
            elif parsed.path == "/api/upload":
                self.json_response(handle_upload(payload))
            elif parsed.path == "/api/upload-url":
                self.json_response(handle_upload_url(payload))
            elif parsed.path == "/api/process-stored-upload":
                self.json_response(handle_stored_upload(payload))
            elif parsed.path == "/api/rules":
                self.json_response(create_rule(payload))
            elif parsed.path == "/api/z-devices":
                self.json_response(create_z_device(payload))
            elif parsed.path == "/api/feedback":
                self.json_response(create_feedback(payload))
            elif parsed.path == "/api/review-item":
                self.json_response(update_review_item(payload))
            elif parsed.path == "/api/delete-item":
                self.json_response(delete_item(payload))
            elif parsed.path == "/api/delete-document":
                self.json_response(delete_document(payload))
            else:
                self.error_json(HTTPStatus.NOT_FOUND, "Sayfa bulunamadı")
        except Exception as exc:
            self.error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8") or "{}")

    def json_response(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error_json(self, status: HTTPStatus, message: str) -> None:
        self.json_response({"error": message}, status)

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.error_json(HTTPStatus.NOT_FOUND, "Dosya bulunamadı")
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_export(self, query: dict[str, list[str]]) -> None:
        client_id = int((query.get("client_id") or ["0"])[0])
        period = (query.get("period") or [""])[0]
        client, sheets = export_sheets(client_id, period)
        if not client:
            self.error_json(HTTPStatus.NOT_FOUND, "Mükellef bulunamadı")
            return
        filename = export_filename(client["name"], period)
        path = EXPORT_DIR / filename
        write_workbook(path, sheets)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_document(self, query: dict[str, list[str]]) -> None:
        document_id = int((query.get("document_id") or ["0"])[0])
        client_id = int((query.get("client_id") or ["0"])[0])
        if not document_id or not client_id:
            self.error_json(HTTPStatus.BAD_REQUEST, "Belge ve mükellef gerekli")
            return
        document = get_document_record(document_id, client_id)
        if not document:
            self.error_json(HTTPStatus.NOT_FOUND, "Belge bulunamadı")
            return
        try:
            data, filename = read_document_content(document)
        except FileNotFoundError:
            self.error_json(HTTPStatus.NOT_FOUND, "Belge dosyası bulunamadı")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def api_state() -> dict:
    provider = ai_provider()
    return api_state_payload(
        {
            "provider": provider,
            "model": ai_model() if provider != "yerel" else "",
        }
    )


REVIEW_TABLES = {
    "bank": {
        "table": "bank_transactions",
        "fields": {
            "bank_name",
            "account_no_or_iban",
            "date",
            "description",
            "debit",
            "credit",
            "balance",
            "currency",
            "counterparty_guess",
            "suggested_account_code",
            "duplicate_flag",
            "needs_review",
        },
    },
    "z": {
        "table": "z_reports",
        "fields": {
            "report_date",
            "device_brand",
            "device_serial",
            "z_no",
            "gross_total",
            "vat_lines",
            "payment_breakdown",
            "cumulative_total",
            "cumulative_vat",
            "duplicate_flag",
            "validation_warnings",
            "needs_review",
        },
    },
    "receipt": {
        "table": "receipts",
        "fields": {
            "receipt_date",
            "merchant_name",
            "vkn_tckn",
            "document_no",
            "gross_total",
            "vat_total",
            "payment_method",
            "bookkeeping_status",
            "needs_review",
        },
    },
}


def review_config(item_type: str) -> dict:
    config = REVIEW_TABLES.get(item_type)
    if not config:
        raise ValueError("Bilinmeyen kontrol türü")
    return config


def get_review_item(query: dict[str, list[str]]) -> dict:
    item_type = (query.get("item_type") or [""])[0]
    item_id = int((query.get("id") or ["0"])[0])
    if not item_id:
        raise ValueError("Kontrol kaydı gerekli")
    config = review_config(item_type)
    return get_review_item_record(item_type, item_id, config)


def update_review_item(payload: dict) -> dict:
    item_type = (payload.get("item_type") or "").strip()
    item_id = int(payload.get("id") or 0)
    values = payload.get("values") or {}
    resolve = bool(payload.get("resolve"))
    rating = (payload.get("rating") or "").strip()
    note = (payload.get("note") or "").strip()
    if not item_id or not isinstance(values, dict):
        raise ValueError("Geçersiz kontrol güncellemesi")
    config = review_config(item_type)
    allowed_fields = config["fields"]
    updates = {key: value for key, value in values.items() if key in allowed_fields}
    if resolve:
        updates["needs_review"] = 0
    elif "needs_review" in updates:
        updates["needs_review"] = 1 if str(updates["needs_review"]).lower() in {"1", "true", "yes", "on"} else 0
    if not updates and not rating:
        raise ValueError("Kontrol için değişiklik girilmedi")

    if rating and rating not in {"dogru", "yanlis", "eksik", "gereksiz"}:
        raise ValueError("Geçersiz geri bildirim")
    update_review_item_record(item_type, item_id, updates, rating, note, config)
    return get_review_item({"item_type": [item_type], "id": [str(item_id)]})


def delete_item(payload: dict) -> dict:
    item_type = (payload.get("item_type") or "").strip()
    item_id = int(payload.get("id") or 0)
    client_id = int(payload.get("client_id") or 0)
    if item_type not in {"bank", "z", "receipt"} or not item_id or not client_id:
        raise ValueError("Silinecek kayıt ve mükellef gerekli")
    return delete_extracted_item_record(item_type, item_id, client_id)


def delete_document(payload: dict) -> dict:
    document_id = int(payload.get("document_id") or 0)
    client_id = int(payload.get("client_id") or 0)
    if not document_id or not client_id:
        raise ValueError("Silinecek belge ve mükellef gerekli")
    return delete_document_record(document_id, client_id)


def create_client(payload: dict) -> dict:
    name = (payload.get("name") or "").strip()
    alias = (payload.get("alias") or "").strip()
    if not name:
        raise ValueError("Mükellef adı gerekli")
    return create_client_record(name, alias)


def handle_upload(payload: dict) -> dict:
    module = payload.get("module")
    if module not in {"bank", "z", "receipt"}:
        raise ValueError("Bilinmeyen bölüm")
    client_id = int(payload.get("client_id") or 0)
    period = (payload.get("period") or "").strip()
    filename = Path(payload.get("filename") or "upload.bin").name
    if not client_id or not period:
        raise ValueError("Mükellef ve dönem gerekli")
    content = base64.b64decode(payload.get("content_base64") or "")
    if not content:
        raise ValueError("Dosya içeriği boş")
    path, stored_path = store_upload(content, client_id, period, module, filename)
    return process_uploaded_file(path, stored_path, client_id, period, module, filename, payload.get("bank_name") or "")


def handle_upload_url(payload: dict) -> dict:
    module = payload.get("module")
    if module not in {"bank", "z", "receipt"}:
        raise ValueError("Bilinmeyen bölüm")
    client_id = int(payload.get("client_id") or 0)
    period = (payload.get("period") or "").strip()
    filename = Path(payload.get("filename") or "upload.bin").name
    if not client_id or not period:
        raise ValueError("Mükellef ve dönem gerekli")
    if not get_client(client_id):
        raise ValueError("Mükellef bulunamadı")
    return create_direct_upload(client_id, period, module, filename)


def handle_stored_upload(payload: dict) -> dict:
    module = payload.get("module")
    if module not in {"bank", "z", "receipt"}:
        raise ValueError("Bilinmeyen bölüm")
    client_id = int(payload.get("client_id") or 0)
    period = (payload.get("period") or "").strip()
    filename = Path(payload.get("filename") or "upload.bin").name
    object_path = (payload.get("object_path") or "").strip()
    if not client_id or not period or not object_path:
        raise ValueError("Mükellef, dönem ve dosya yolu gerekli")
    if not get_client(client_id):
        raise ValueError("Mükellef bulunamadı")
    path, stored_path = materialize_stored_upload(object_path, client_id, period, module, filename)
    return process_uploaded_file(path, stored_path, client_id, period, module, filename, payload.get("bank_name") or "")


def process_uploaded_file(path: Path, stored_path: str, client_id: int, period: str, module: str, filename: str, bank_name: str = "") -> dict:
    doc_id = insert_document_record(client_id, period, module, filename, stored_path, "processing")
    warnings: list[str] = []
    try:
        if module == "bank":
            result = parse_bank_file(path, client_id, period, bank_name, get_account_rules(client_id))
            warnings = result.warnings
            for item in result.rows:
                insert_bank_transaction(doc_id, item)
        else:
            active_module = module
            ai_items, ai_warnings, diagnostic = [], [], {}
            try:
                ai_items, ai_warnings, diagnostic = extract_with_ai(path, module, client_id, period, filename)
                record_extraction_run(doc_id, diagnostic, warnings)
                warnings.extend(ai_warnings)
            except AI_EXTRACTION_ERRORS as exc:
                record_extraction_run(doc_id, exc.diagnostic, warnings)
                warnings.append(f"Belge okuma kullanılamadı: {friendly_upload_error(str(exc))}")
            except Exception as exc:
                warnings.append(f"Belge okuma kullanılamadı: {friendly_upload_error(str(exc))}")

            if should_reroute_receipt_to_z(module, ai_items, ai_warnings, diagnostic):
                warnings.append("Fiş olarak yüklenen belge Z raporu olarak algılandı; Z raporları bölümüne aktarıldı.")
                try:
                    z_items, z_warnings, z_diagnostic = extract_with_ai(path, "z", client_id, period, filename)
                    record_extraction_run(doc_id, z_diagnostic, warnings)
                    warnings.extend(z_warnings)
                    if z_items:
                        update_document_module(doc_id, "z")
                        active_module = "z"
                        ai_items = z_items
                    else:
                        warnings.append("Z raporu olarak tekrar okundu ama yapılandırılmış kayıt çıkarılamadı.")
                except AI_EXTRACTION_ERRORS as exc:
                    record_extraction_run(doc_id, exc.diagnostic, warnings)
                    warnings.append(f"Z raporu okuması kullanılamadı: {friendly_upload_error(str(exc))}")
                except Exception as exc:
                    warnings.append(f"Z raporu okuması kullanılamadı: {friendly_upload_error(str(exc))}")

            if ai_items:
                for item in ai_items:
                    insert_extracted_item(doc_id, active_module, item)
            else:
                if not can_use_local_ocr_fallback():
                    reason = "; ".join(dedupe_messages(warnings)) if warnings else "Belge okuma yapılandırılmış kayıt döndürmedi"
                    message = f"Belge okunamadı: {friendly_upload_error(reason)}"
                    mark_document_failed(doc_id, [message])
                    raise RuntimeError(message)
                ocr_pages = run_ocr_pages(path)
                if len(ocr_pages) > 1:
                    warnings.append(f"{len(ocr_pages)} sayfa ayrı kayıt olarak işlendi")
                if active_module == "receipt" and any(is_z_report_text(page.get("raw_text", "")) for page in ocr_pages):
                    update_document_module(doc_id, "z")
                    active_module = "z"
                    warnings.append("Fiş olarak yüklenen belge yerel OCR ile Z raporu olarak algılandı; Z raporları bölümüne aktarıldı.")
                if active_module == "z":
                    for ocr_page in ocr_pages:
                        source_name = page_source_name(filename, "Z Raporu", ocr_page["page_number"], len(ocr_pages))
                        for item in extract_z_reports(ocr_page["raw_text"], client_id, period, source_name):
                            insert_extracted_item(doc_id, active_module, item)
                else:
                    for ocr_page in ocr_pages:
                        source_name = page_source_name(filename, "Fiş", ocr_page["page_number"], len(ocr_pages))
                        item = extract_receipt(ocr_page["raw_text"], client_id, period, source_name)
                        insert_extracted_item(doc_id, active_module, item)
        mark_document_done(doc_id, warnings)
        return {"ok": True, "document_id": doc_id, "warnings": warnings}
    except Exception as exc:
        failure_warnings = warnings + [str(exc)]
        mark_document_failed(doc_id, failure_warnings)
        raise


def record_extraction_run(document_id: int, diagnostic: dict, warnings: list[str]) -> None:
    if not diagnostic:
        return
    try:
        insert_extraction_run_record(document_id, diagnostic)
    except Exception as exc:
        warnings.append(f"Belge okuma tanılama kaydı saklanamadı: {exc}")


def can_use_local_ocr_fallback() -> bool:
    return not os.environ.get("VERCEL")


def should_reroute_receipt_to_z(module: str, ai_items: list[dict], ai_warnings: list[str], diagnostic: dict) -> bool:
    if module != "receipt" or ai_items:
        return False
    evidence = " ".join(
        [
            *[str(warning) for warning in ai_warnings],
            str(diagnostic.get("raw_response", "")),
            str(diagnostic.get("error_message", "")),
        ]
    )
    return has_z_report_signal(evidence)


def has_z_report_signal(text: str) -> bool:
    normalized = (text or "").upper().replace("İ", "I").replace("Ü", "U").replace("Ç", "C")
    signals = [
        "Z GUNLUK RAPORU",
        "Z RAPORU",
        "Z SAYAC",
        "Z NO",
        "Z-RAPOR",
        "Z REPORT",
    ]
    return any(signal in normalized for signal in signals)


def friendly_upload_error(message: str) -> str:
    text = str(message or "").strip()
    if "401" in text or "invalid_api_key" in text or "OpenAI anahtarı" in text:
        return "OpenAI anahtarı geçersiz veya eksik."
    if "OpenAI" in text and ("429" in text or "rate" in text or "limit" in text):
        return "OpenAI kullanım sınırı geçici olarak doldu. Birkaç dakika sonra tekrar dene."
    if "OpenAI" in text and ("500" in text or "502" in text or "503" in text or "504" in text):
        return "OpenAI geçici olarak yanıt veremedi. Birkaç dakika sonra tekrar dene."
    if "503" in text or "UNAVAILABLE" in text or "high demand" in text:
        return "Gemini şu anda yoğun. Birkaç dakika sonra tekrar dene."
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "Gemini kullanım sınırı geçici olarak doldu. Birkaç dakika sonra tekrar dene."
    if "{\"error\"" in text:
        return "Belge okuma servisi geçici olarak yanıt veremedi. Birkaç dakika sonra tekrar dene."
    return text[:260]


def dedupe_messages(messages: list[str]) -> list[str]:
    result = []
    seen = set()
    for message in messages:
        text = friendly_upload_error(message)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def insert_extracted_item(doc_id: int, module: str, item: dict) -> None:
    insert_extracted_item_record(module, doc_id, item)


def page_source_name(filename: str, label: str, page_number: int, page_count: int) -> str:
    if page_count <= 1:
        return filename
    return f"{label} {page_number:02d} - {filename}"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for idx in range(2, 1000):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
        raise ValueError("Yükleme dosyası adı oluşturulamadı")


def create_rule(payload: dict) -> dict:
    client_id = payload.get("client_id")
    pattern = (payload.get("pattern") or "").strip()
    account_code = (payload.get("account_code") or "").strip()
    if not pattern or not account_code:
        raise ValueError("Açıklama paterni ve hesap kodu gerekli")
    return create_rule_record(client_id, pattern, account_code)


def create_z_device(payload: dict) -> dict:
    client_id = int(payload.get("client_id") or 0)
    name = (payload.get("name") or "").strip()
    brand = (payload.get("brand") or "").strip()
    serial = (payload.get("serial") or "").strip()
    if not client_id or not name:
        raise ValueError("Kasa adı ve mükellef gerekli")
    return create_z_device_record(client_id, name, brand, serial)


def create_feedback(payload: dict) -> dict:
    item_type = (payload.get("item_type") or "").strip()
    item_id = int(payload.get("item_id") or 0)
    rating = (payload.get("rating") or "").strip()
    note = (payload.get("note") or "").strip()
    if item_type not in {"bank", "z", "receipt"} or not item_id or rating not in {"dogru", "yanlis", "eksik", "gereksiz"}:
        raise ValueError("Geçersiz geri bildirim")
    return create_feedback_record(item_type, item_id, rating, note)


def main() -> None:
    ensure_ready()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MaliPilot running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
