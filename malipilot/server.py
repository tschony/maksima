from __future__ import annotations

import base64
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .exporters import export_filename, write_workbook
from .ocr import extract_receipt, extract_z_report, run_ocr_pages
from .parsers import parse_bank_file
from .storage import EXPORT_DIR, ROOT, UPLOAD_DIR, account_rules, connect, insert_document, row, rows


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
            with connect() as conn:
                self.json_response(rows(conn, "select * from clients order by name"))
        elif parsed.path == "/api/review-item":
            self.json_response(get_review_item(parse_qs(parsed.query)))
        elif parsed.path == "/api/export":
            self.handle_export(parse_qs(parsed.query))
        else:
            self.error_json(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/clients":
                self.json_response(create_client(payload))
            elif parsed.path == "/api/upload":
                self.json_response(handle_upload(payload))
            elif parsed.path == "/api/rules":
                self.json_response(create_rule(payload))
            elif parsed.path == "/api/feedback":
                self.json_response(create_feedback(payload))
            elif parsed.path == "/api/review-item":
                self.json_response(update_review_item(payload))
            else:
                self.error_json(HTTPStatus.NOT_FOUND, "Not found")
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
            self.error_json(HTTPStatus.NOT_FOUND, "File not found")
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
        with connect() as conn:
            client = row(conn, "select * from clients where id = ?", (client_id,))
            if not client:
                self.error_json(HTTPStatus.NOT_FOUND, "Client not found")
                return
            where = "client_id = ? and period = ?"
            args = (client_id, period)
            sheets = {
                "Banka_Hareketleri": rows(conn, f"select * from bank_transactions where {where} order by date, id", args),
                "Z_Raporlari": rows(conn, f"select * from z_reports where {where} order by report_date, id", args),
                "Fisler": rows(conn, f"select * from receipts where {where} order by receipt_date, id", args),
                "Kontrol_Gerekenler": review_needed(conn, client_id, period),
                "Ogrenilen_Kurallar": rows(conn, "select * from account_code_rules where client_id is null or client_id = ? order by id", (client_id,)),
            }
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
    with connect() as conn:
        clients = rows(conn, "select * from clients order by name")
        return {
            "clients": clients,
            "counts": {
                "clients": scalar(conn, "select count(*) from clients"),
                "documents": scalar(conn, "select count(*) from documents"),
                "bank": scalar(conn, "select count(*) from bank_transactions"),
                "z_reports": scalar(conn, "select count(*) from z_reports"),
                "receipts": scalar(conn, "select count(*) from receipts"),
                "review": len(review_needed(conn)),
            },
            "recent_documents": rows(conn, "select * from documents order by id desc limit 10"),
            "bank_rows": rows(conn, "select * from bank_transactions order by id desc limit 50"),
            "z_reports": rows(conn, "select * from z_reports order by id desc limit 30"),
            "receipts": rows(conn, "select * from receipts order by id asc limit 100"),
            "review_items": review_needed(conn),
        }


def scalar(conn, query: str, args: tuple = ()) -> int:
    return int(conn.execute(query, args).fetchone()[0])


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
        raise ValueError("Unknown review item type")
    return config


def get_review_item(query: dict[str, list[str]]) -> dict:
    item_type = (query.get("item_type") or [""])[0]
    item_id = int((query.get("id") or ["0"])[0])
    if not item_id:
        raise ValueError("Kontrol kaydı gerekli")
    config = review_config(item_type)
    table_name = config["table"]
    with connect() as conn:
        item = row(conn, f"select * from {table_name} where id = ?", (item_id,))
        if not item:
            raise ValueError("Kontrol kaydı bulunamadı")
        document = row(conn, "select * from documents where id = ?", (item["document_id"],))
        client = row(conn, "select * from clients where id = ?", (item["client_id"],))
        feedback = rows(conn, "select * from feedback where item_type = ? and item_id = ? order by id desc limit 10", (item_type, item_id))
    return {
        "item_type": item_type,
        "item": item,
        "document": document,
        "client": client,
        "feedback": feedback,
        "editable_fields": sorted(config["fields"] - {"needs_review"}),
    }


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

    with connect() as conn:
        current = row(conn, f"select * from {config['table']} where id = ?", (item_id,))
        if not current:
            raise ValueError("Kontrol kaydı bulunamadı")
        if updates:
            set_clause = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"update {config['table']} set {set_clause} where id = ?",
                (*updates.values(), item_id),
            )
        if rating:
            if rating not in {"dogru", "yanlis", "eksik", "gereksiz"}:
                raise ValueError("Geçersiz geri bildirim")
            conn.execute(
                "insert into feedback (item_type, item_id, rating, note) values (?, ?, ?, ?)",
                (item_type, item_id, rating, note),
            )
        conn.commit()
    return get_review_item({"item_type": [item_type], "id": [str(item_id)]})


def create_client(payload: dict) -> dict:
    name = (payload.get("name") or "").strip()
    alias = (payload.get("alias") or "").strip()
    if not name:
        raise ValueError("Mükellef adı gerekli")
    with connect() as conn:
        cur = conn.execute("insert into clients (name, alias) values (?, ?)", (name, alias))
        conn.commit()
        return row(conn, "select * from clients where id = ?", (cur.lastrowid,))


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
    folder = UPLOAD_DIR / str(client_id) / period / module
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder / filename)
    path.write_bytes(content)

    with connect() as conn:
        if not row(conn, "select * from clients where id = ?", (client_id,)):
            raise ValueError("Mükellef bulunamadı")
        doc_id = insert_document(conn, client_id, period, module, filename, str(path), "processing")
        warnings: list[str] = []
        if module == "bank":
            result = parse_bank_file(path, client_id, period, payload.get("bank_name") or "", account_rules(conn, client_id))
            warnings = result.warnings
            for item in result.rows:
                conn.execute(
                    """
                    insert into bank_transactions
                    (document_id, client_id, period, bank_name, account_no_or_iban, date, description, debit, credit, balance, currency,
                     counterparty_guess, transaction_hash, duplicate_flag, suggested_account_code, confidence, needs_review, source_row)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        item["client_id"],
                        item["period"],
                        item["bank_name"],
                        item["account_no_or_iban"],
                        item["date"],
                        item["description"],
                        item["debit"],
                        item["credit"],
                        item["balance"],
                        item["currency"],
                        item["counterparty_guess"],
                        item["transaction_hash"],
                        int(item["duplicate_flag"]),
                        item["suggested_account_code"],
                        item["confidence"],
                        int(item["needs_review"]),
                        item["source_row"],
                    ),
                )
        else:
            ocr_pages = run_ocr_pages(path)
            if len(ocr_pages) > 1:
                warnings.append(f"{len(ocr_pages)} sayfa ayrı kayıt olarak işlendi")
            if module == "z":
                for ocr_page in ocr_pages:
                    source_name = page_source_name(filename, "Z Raporu", ocr_page["page_number"], len(ocr_pages))
                    item = extract_z_report(ocr_page["raw_text"], client_id, period, source_name)
                    conn.execute(
                        """
                        insert into z_reports
                        (document_id, client_id, period, source_file, report_date, device_brand, device_serial, z_no, gross_total,
                         vat_lines, payment_breakdown, confidence, needs_review, raw_text)
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (doc_id, item["client_id"], item["period"], item["source_file"], item["report_date"], item["device_brand"], item["device_serial"], item["z_no"], item["gross_total"], item["vat_lines"], item["payment_breakdown"], item["confidence"], int(item["needs_review"]), item["raw_text"]),
                    )
            else:
                for ocr_page in ocr_pages:
                    source_name = page_source_name(filename, "Fiş", ocr_page["page_number"], len(ocr_pages))
                    item = extract_receipt(ocr_page["raw_text"], client_id, period, source_name)
                    conn.execute(
                        """
                        insert into receipts
                        (document_id, client_id, period, source_file, receipt_date, merchant_name, vkn_tckn, document_no, gross_total,
                         vat_total, payment_method, bookkeeping_status, confidence, needs_review, raw_text)
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (doc_id, item["client_id"], item["period"], item["source_file"], item["receipt_date"], item["merchant_name"], item["vkn_tckn"], item["document_no"], item["gross_total"], item["vat_total"], item["payment_method"], item["bookkeeping_status"], item["confidence"], int(item["needs_review"]), item["raw_text"]),
                    )
        conn.execute("update documents set status = ?, warnings = ? where id = ?", ("done", json.dumps(warnings, ensure_ascii=False), doc_id))
        conn.commit()
    return {"ok": True, "document_id": doc_id, "warnings": warnings}


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
    with connect() as conn:
        cur = conn.execute(
            "insert into account_code_rules (client_id, pattern, account_code, source) values (?, ?, ?, 'manual')",
            (client_id, pattern, account_code),
        )
        conn.commit()
        return row(conn, "select * from account_code_rules where id = ?", (cur.lastrowid,))


def create_feedback(payload: dict) -> dict:
    item_type = (payload.get("item_type") or "").strip()
    item_id = int(payload.get("item_id") or 0)
    rating = (payload.get("rating") or "").strip()
    note = (payload.get("note") or "").strip()
    if item_type not in {"bank", "z", "receipt"} or not item_id or rating not in {"dogru", "yanlis", "eksik", "gereksiz"}:
        raise ValueError("Geçersiz geri bildirim")
    with connect() as conn:
        cur = conn.execute(
            "insert into feedback (item_type, item_id, rating, note) values (?, ?, ?, ?)",
            (item_type, item_id, rating, note),
        )
        conn.commit()
        return row(conn, "select * from feedback where id = ?", (cur.lastrowid,))


def review_needed(conn, client_id: int | None = None, period: str | None = None) -> list[dict]:
    clauses = ["needs_review = 1"]
    args: list = []
    if client_id:
        clauses.append("client_id = ?")
        args.append(client_id)
    if period:
        clauses.append("period = ?")
        args.append(period)
    where = " and ".join(clauses)
    bank = rows(conn, f"select 'bank' as item_type, id, client_id, period, description as title, confidence, suggested_account_code as detail from bank_transactions where {where} order by id asc", tuple(args))
    z = rows(conn, f"select 'z' as item_type, id, client_id, period, source_file as title, confidence, gross_total as detail from z_reports where {where} order by id asc", tuple(args))
    receipt = rows(conn, f"select 'receipt' as item_type, id, client_id, period, source_file as title, confidence, bookkeeping_status as detail from receipts where {where} order by id asc", tuple(args))
    return bank + z + receipt


def main() -> None:
    connect().close()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MaliPilot running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
