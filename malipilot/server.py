from __future__ import annotations

import base64
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .ai_extractor import extract_with_gemini, gemini_configured, gemini_model
from .exporters import export_filename, write_workbook
from .ocr import extract_receipt, extract_z_report, run_ocr_pages
from .parsers import parse_bank_file
from .persistence import (
    EXPORT_DIR,
    api_state_payload,
    create_client_record,
    create_direct_upload,
    create_feedback_record,
    create_rule_record,
    ensure_ready,
    export_sheets,
    get_account_rules,
    get_clients,
    get_client,
    get_review_item_record,
    insert_bank_transaction,
    insert_document_record,
    insert_extracted_item_record,
    mark_document_done,
    materialize_stored_upload,
    review_needed,
    store_upload,
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
            elif parsed.path == "/api/feedback":
                self.json_response(create_feedback(payload))
            elif parsed.path == "/api/review-item":
                self.json_response(update_review_item(payload))
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


def api_state() -> dict:
    return api_state_payload(
        {
            "provider": "gemini" if gemini_configured() else "yerel",
            "model": gemini_model() if gemini_configured() else "",
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
    if module == "bank":
        result = parse_bank_file(path, client_id, period, bank_name, get_account_rules(client_id))
        warnings = result.warnings
        for item in result.rows:
            insert_bank_transaction(doc_id, item)
    else:
        ai_items, ai_warnings = [], []
        try:
            ai_items, ai_warnings = extract_with_gemini(path, module, client_id, period, filename)
            warnings.extend(ai_warnings)
        except Exception as exc:
            warnings.append(f"Gemini kullanılamadı, yerel OCR denendi: {exc}")

        if ai_items:
            for item in ai_items:
                insert_extracted_item(doc_id, module, item)
        else:
            ocr_pages = run_ocr_pages(path)
            if len(ocr_pages) > 1:
                warnings.append(f"{len(ocr_pages)} sayfa ayrı kayıt olarak işlendi")
            if module == "z":
                for ocr_page in ocr_pages:
                    source_name = page_source_name(filename, "Z Raporu", ocr_page["page_number"], len(ocr_pages))
                    item = extract_z_report(ocr_page["raw_text"], client_id, period, source_name)
                    insert_extracted_item(doc_id, module, item)
            else:
                for ocr_page in ocr_pages:
                    source_name = page_source_name(filename, "Fiş", ocr_page["page_number"], len(ocr_pages))
                    item = extract_receipt(ocr_page["raw_text"], client_id, period, source_name)
                    insert_extracted_item(doc_id, module, item)
    mark_document_done(doc_id, warnings)
    return {"ok": True, "document_id": doc_id, "warnings": warnings}


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
