from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import env as _env
from .storage import EXPORT_DIR, UPLOAD_DIR, account_rules as sqlite_account_rules, connect, insert_document as sqlite_insert_document, row, rows


SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")


def using_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def backend_name() -> str:
    return "supabase" if using_supabase() else "sqlite"


def client() -> "SupabaseRest":
    return SupabaseRest(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


class SupabaseRest:
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key

    def select(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.request_json("GET", f"/rest/v1/{table}", params=params or {})

    def single(self, table: str, params: dict[str, Any]) -> dict[str, Any] | None:
        result = self.select(table, {**params, "limit": "1"})
        return result[0] if result else None

    def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.request_json("POST", f"/rest/v1/{table}", payload=payload, prefer="return=representation")
        if not result:
            raise RuntimeError(f"{table} kaydı oluşturulamadı")
        return result[0]

    def patch(self, table: str, filters: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        result = self.request_json("PATCH", f"/rest/v1/{table}", params=filters, payload=payload, prefer="return=representation")
        return result[0] if result else None

    def count(self, table: str) -> int:
        request = self.request("GET", f"/rest/v1/{table}", params={"select": "id"}, headers={"Prefer": "count=exact", "Range": "0-0"})
        content_range = request.headers.get("Content-Range", "")
        if "/" in content_range:
            return int(content_range.rsplit("/", 1)[-1])
        return len(json.loads(request.read().decode("utf-8") or "[]"))

    def upload_object(self, object_path: str, content: bytes, mime_type: str) -> str:
        quoted_path = "/".join(urllib.parse.quote(part) for part in object_path.split("/"))
        request = urllib.request.Request(
            f"{self.url}/storage/v1/object/{SUPABASE_BUCKET}/{quoted_path}",
            data=content,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": mime_type,
                "x-upsert": "false",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=55).read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase dosya yükleme başarısız oldu: {exc.code} {detail[:400]}") from exc
        return f"supabase://{SUPABASE_BUCKET}/{object_path}"

    def create_signed_upload_url(self, object_path: str) -> str:
        quoted_path = "/".join(urllib.parse.quote(part) for part in object_path.split("/"))
        result = self.request_json(
            "POST",
            f"/storage/v1/object/upload/sign/{SUPABASE_BUCKET}/{quoted_path}",
            payload={"upsert": False},
        )
        signed_path = result.get("url") or result.get("signedURL") or result.get("signedUrl")
        if not signed_path:
            raise RuntimeError("Supabase doğrudan yükleme adresi oluşturulamadı")
        if str(signed_path).startswith("http"):
            return str(signed_path)
        return f"{self.url}/storage/v1{signed_path}"

    def download_object(self, object_path: str) -> bytes:
        quoted_path = "/".join(urllib.parse.quote(part) for part in object_path.split("/"))
        response = self.request("GET", f"/storage/v1/object/{SUPABASE_BUCKET}/{quoted_path}")
        return response.read()

    def request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> Any:
        response = self.request(method, path, params=params, payload=payload, headers={"Prefer": prefer} if prefer else None)
        data = response.read().decode("utf-8")
        return json.loads(data or "[]")

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.url}{path}{'?' + query if query else ''}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        request_headers.update({key: value for key, value in (headers or {}).items() if value})
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            return urllib.request.urlopen(request, timeout=55)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase isteği başarısız oldu: {exc.code} {detail[:400]}") from exc


def ensure_ready() -> None:
    if not using_supabase():
        connect().close()


def get_clients() -> list[dict[str, Any]]:
    if using_supabase():
        return client().select("clients", {"select": "*", "order": "name.asc"})
    with connect() as conn:
        return rows(conn, "select * from clients order by name")


def get_client(client_id: int) -> dict[str, Any] | None:
    if using_supabase():
        return client().single("clients", {"select": "*", "id": f"eq.{client_id}"})
    with connect() as conn:
        return row(conn, "select * from clients where id = ?", (client_id,))


def get_document_record(document_id: int, client_id: int | None = None) -> dict[str, Any] | None:
    if using_supabase():
        params: dict[str, Any] = {"select": "*", "id": f"eq.{document_id}"}
        if client_id:
            params["client_id"] = f"eq.{client_id}"
        return client().single("documents", params)
    with connect() as conn:
        if client_id:
            return row(conn, "select * from documents where id = ? and client_id = ?", (document_id, client_id))
        return row(conn, "select * from documents where id = ?", (document_id,))


def read_document_content(document: dict[str, Any]) -> tuple[bytes, str]:
    filename = Path(document.get("original_name") or "belge").name
    stored_path = str(document.get("stored_path") or "")
    if stored_path.startswith("supabase://"):
        object_path = stored_path.removeprefix("supabase://").split("/", 1)[-1]
        return client().download_object(object_path), filename
    path = Path(stored_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Belge dosyası bulunamadı")
    return path.read_bytes(), filename


def create_client_record(name: str, alias: str) -> dict[str, Any]:
    if using_supabase():
        return client().insert("clients", {"name": name, "alias": alias})
    with connect() as conn:
        cur = conn.execute("insert into clients (name, alias) values (?, ?)", (name, alias))
        conn.commit()
        return row(conn, "select * from clients where id = ?", (cur.lastrowid,))


def get_account_rules(client_id: int) -> list[dict[str, Any]]:
    if using_supabase():
        return client().select(
            "account_code_rules",
            {"select": "*", "or": f"(client_id.is.null,client_id.eq.{client_id})", "order": "id.desc"},
        )
    with connect() as conn:
        return sqlite_account_rules(conn, client_id)


def create_rule_record(client_id: int | None, pattern: str, account_code: str) -> dict[str, Any]:
    payload = {"client_id": client_id, "pattern": pattern, "account_code": account_code, "source": "manual"}
    if using_supabase():
        return client().insert("account_code_rules", payload)
    with connect() as conn:
        cur = conn.execute(
            "insert into account_code_rules (client_id, pattern, account_code, source) values (?, ?, ?, 'manual')",
            (client_id, pattern, account_code),
        )
        conn.commit()
        return row(conn, "select * from account_code_rules where id = ?", (cur.lastrowid,))


def create_feedback_record(item_type: str, item_id: int, rating: str, note: str) -> dict[str, Any]:
    payload = {"item_type": item_type, "item_id": item_id, "rating": rating, "note": note}
    if using_supabase():
        return client().insert("feedback", payload)
    with connect() as conn:
        cur = conn.execute(
            "insert into feedback (item_type, item_id, rating, note) values (?, ?, ?, ?)",
            (item_type, item_id, rating, note),
        )
        conn.commit()
        return row(conn, "select * from feedback where id = ?", (cur.lastrowid,))


def store_upload(content: bytes, client_id: int, period: str, module: str, filename: str) -> tuple[Path, str]:
    folder = UPLOAD_DIR / str(client_id) / period / module
    folder.mkdir(parents=True, exist_ok=True)
    local_path = unique_path(folder / filename)
    local_path.write_bytes(content)
    if not using_supabase():
        return local_path, str(local_path)
    object_path = f"{client_id}/{period}/{module}/{uuid.uuid4().hex}-{filename}"
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    stored_path = client().upload_object(object_path, content, mime_type)
    return local_path, stored_path


def create_direct_upload(client_id: int, period: str, module: str, filename: str) -> dict[str, str]:
    if not using_supabase():
        raise RuntimeError("Doğrudan yükleme için Supabase bağlantısı gerekli")
    safe_name = Path(filename or "upload.bin").name
    object_path = f"{client_id}/{period}/{module}/{uuid.uuid4().hex}-{safe_name}"
    return {
        "object_path": object_path,
        "stored_path": f"supabase://{SUPABASE_BUCKET}/{object_path}",
        "upload_url": client().create_signed_upload_url(object_path),
    }


def materialize_stored_upload(object_path: str, client_id: int, period: str, module: str, filename: str) -> tuple[Path, str]:
    if not using_supabase():
        raise RuntimeError("Kaydedilmiş dosya işleme için Supabase bağlantısı gerekli")
    content = client().download_object(object_path)
    folder = UPLOAD_DIR / str(client_id) / period / module
    folder.mkdir(parents=True, exist_ok=True)
    local_path = unique_path(folder / Path(filename or "upload.bin").name)
    local_path.write_bytes(content)
    return local_path, f"supabase://{SUPABASE_BUCKET}/{object_path}"


def insert_document_record(client_id: int, period: str, module: str, filename: str, stored_path: str, status: str) -> int:
    if using_supabase():
        created = client().insert(
            "documents",
            {
                "client_id": client_id,
                "period": period,
                "module": module,
                "original_name": filename,
                "stored_path": stored_path,
                "status": status,
                "warnings": "[]",
            },
        )
        return int(created["id"])
    with connect() as conn:
        return sqlite_insert_document(conn, client_id, period, module, filename, stored_path, status)


def mark_document_done(document_id: int, warnings: list[str]) -> None:
    payload = {"status": "done", "warnings": json.dumps(warnings, ensure_ascii=False)}
    if using_supabase():
        client().patch("documents", {"id": f"eq.{document_id}"}, payload)
        return
    with connect() as conn:
        conn.execute("update documents set status = ?, warnings = ? where id = ?", ("done", payload["warnings"], document_id))
        conn.commit()


def mark_document_failed(document_id: int, warnings: list[str]) -> None:
    payload = {"status": "failed", "warnings": json.dumps(warnings, ensure_ascii=False)}
    if using_supabase():
        client().patch("documents", {"id": f"eq.{document_id}"}, payload)
        return
    with connect() as conn:
        conn.execute("update documents set status = ?, warnings = ? where id = ?", ("failed", payload["warnings"], document_id))
        conn.commit()


def insert_extraction_run_record(document_id: int, diagnostic: dict[str, Any]) -> None:
    payload = {
        "document_id": document_id,
        "provider": diagnostic.get("provider") or "unknown",
        "model": diagnostic.get("model") or "",
        "module": diagnostic.get("module") or "",
        "file_name": diagnostic.get("file_name") or "",
        "file_size": int(diagnostic.get("file_size") or 0),
        "input_method": diagnostic.get("input_method") or "",
        "status": diagnostic.get("status") or "unknown",
        "item_count": int(diagnostic.get("item_count") or 0),
        "duration_ms": int(diagnostic.get("duration_ms") or 0),
        "raw_response": diagnostic.get("raw_response") or "",
        "error_message": diagnostic.get("error_message") or "",
    }
    if using_supabase():
        client().insert("extraction_runs", payload)
        return
    with connect() as conn:
        conn.execute(
            """
            insert into extraction_runs
            (document_id, provider, model, module, file_name, file_size, input_method, status, item_count, duration_ms,
             raw_response, error_message)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["document_id"],
                payload["provider"],
                payload["model"],
                payload["module"],
                payload["file_name"],
                payload["file_size"],
                payload["input_method"],
                payload["status"],
                payload["item_count"],
                payload["duration_ms"],
                payload["raw_response"],
                payload["error_message"],
            ),
        )
        conn.commit()


def insert_bank_transaction(document_id: int, item: dict[str, Any]) -> None:
    payload = {
        "document_id": document_id,
        "client_id": item["client_id"],
        "period": item["period"],
        "bank_name": item["bank_name"],
        "account_no_or_iban": item["account_no_or_iban"],
        "date": item["date"],
        "description": item["description"],
        "debit": item["debit"],
        "credit": item["credit"],
        "balance": item["balance"],
        "currency": item["currency"],
        "counterparty_guess": item["counterparty_guess"],
        "transaction_hash": item["transaction_hash"],
        "duplicate_flag": bool(item["duplicate_flag"]),
        "suggested_account_code": item["suggested_account_code"],
        "confidence": item["confidence"],
        "needs_review": bool(item["needs_review"]),
        "source_row": item["source_row"],
    }
    if using_supabase():
        client().insert("bank_transactions", payload)
        return
    with connect() as conn:
        conn.execute(
            """
            insert into bank_transactions
            (document_id, client_id, period, bank_name, account_no_or_iban, date, description, debit, credit, balance, currency,
             counterparty_guess, transaction_hash, duplicate_flag, suggested_account_code, confidence, needs_review, source_row)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                payload["client_id"],
                payload["period"],
                payload["bank_name"],
                payload["account_no_or_iban"],
                payload["date"],
                payload["description"],
                payload["debit"],
                payload["credit"],
                payload["balance"],
                payload["currency"],
                payload["counterparty_guess"],
                payload["transaction_hash"],
                int(payload["duplicate_flag"]),
                payload["suggested_account_code"],
                payload["confidence"],
                int(payload["needs_review"]),
                payload["source_row"],
            ),
        )
        conn.commit()


def insert_extracted_item_record(module: str, document_id: int, item: dict[str, Any]) -> None:
    if module == "z":
        payload = {
            "document_id": document_id,
            "client_id": item["client_id"],
            "period": item["period"],
            "source_file": item["source_file"],
            "report_date": item["report_date"],
            "device_brand": item["device_brand"],
            "device_serial": item["device_serial"],
            "z_no": item["z_no"],
            "gross_total": item["gross_total"],
            "vat_lines": item["vat_lines"],
            "payment_breakdown": item["payment_breakdown"],
            "confidence": item["confidence"],
            "needs_review": bool(item["needs_review"]),
            "raw_text": item["raw_text"],
        }
        table = "z_reports"
        sqlite_sql = """
            insert into z_reports
            (document_id, client_id, period, source_file, report_date, device_brand, device_serial, z_no, gross_total,
             vat_lines, payment_breakdown, confidence, needs_review, raw_text)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        sqlite_args = (
            document_id,
            payload["client_id"],
            payload["period"],
            payload["source_file"],
            payload["report_date"],
            payload["device_brand"],
            payload["device_serial"],
            payload["z_no"],
            payload["gross_total"],
            payload["vat_lines"],
            payload["payment_breakdown"],
            payload["confidence"],
            int(payload["needs_review"]),
            payload["raw_text"],
        )
    else:
        payload = {
            "document_id": document_id,
            "client_id": item["client_id"],
            "period": item["period"],
            "source_file": item["source_file"],
            "receipt_date": item["receipt_date"],
            "merchant_name": item["merchant_name"],
            "vkn_tckn": item["vkn_tckn"],
            "document_no": item["document_no"],
            "gross_total": item["gross_total"],
            "vat_total": item["vat_total"],
            "payment_method": item["payment_method"],
            "bookkeeping_status": item["bookkeeping_status"],
            "confidence": item["confidence"],
            "needs_review": bool(item["needs_review"]),
            "raw_text": item["raw_text"],
        }
        table = "receipts"
        sqlite_sql = """
            insert into receipts
            (document_id, client_id, period, source_file, receipt_date, merchant_name, vkn_tckn, document_no, gross_total,
             vat_total, payment_method, bookkeeping_status, confidence, needs_review, raw_text)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        sqlite_args = (
            document_id,
            payload["client_id"],
            payload["period"],
            payload["source_file"],
            payload["receipt_date"],
            payload["merchant_name"],
            payload["vkn_tckn"],
            payload["document_no"],
            payload["gross_total"],
            payload["vat_total"],
            payload["payment_method"],
            payload["bookkeeping_status"],
            payload["confidence"],
            int(payload["needs_review"]),
            payload["raw_text"],
        )
    if using_supabase():
        client().insert(table, payload)
        return
    with connect() as conn:
        conn.execute(sqlite_sql, sqlite_args)
        conn.commit()


def api_state_payload(ai_info: dict[str, str]) -> dict[str, Any]:
    return {
        "clients": get_clients(),
        "counts": {
            "clients": count_table("clients"),
            "documents": count_table("documents"),
            "bank": count_table("bank_transactions"),
            "z_reports": count_table("z_reports"),
            "receipts": count_table("receipts"),
            "review": len(review_needed()),
        },
        "recent_documents": recent_documents(),
        "bank_rows": list_rows("bank_transactions", "id.desc", 500),
        "z_reports": list_rows("z_reports", "id.desc", 300),
        "receipts": list_rows("receipts", "id.asc", 500),
        "review_items": review_needed(),
        "ai": ai_info,
        "storage": {"provider": backend_name()},
    }


def count_table(table: str) -> int:
    if using_supabase():
        return client().count(table)
    with connect() as conn:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


def recent_documents() -> list[dict[str, Any]]:
    if using_supabase():
        return client().select("documents", {"select": "*", "order": "id.desc", "limit": "100"})
    with connect() as conn:
        return rows(conn, "select * from documents order by id desc limit 100")


def list_rows(table: str, order: str, limit: int) -> list[dict[str, Any]]:
    if using_supabase():
        return client().select(table, {"select": "*", "order": order, "limit": str(limit)})
    with connect() as conn:
        return rows(conn, f"select * from {table} order by {order.replace('.', ' ')} limit ?", (limit,))


def get_review_item_record(item_type: str, item_id: int, config: dict[str, Any]) -> dict[str, Any]:
    table_name = config["table"]
    if using_supabase():
        supabase = client()
        item = supabase.single(table_name, {"select": "*", "id": f"eq.{item_id}"})
        if not item:
            raise ValueError("Kontrol kaydı bulunamadı")
        document = supabase.single("documents", {"select": "*", "id": f"eq.{item['document_id']}"})
        client_row = supabase.single("clients", {"select": "*", "id": f"eq.{item['client_id']}"})
        feedback = supabase.select("feedback", {"select": "*", "item_type": f"eq.{item_type}", "item_id": f"eq.{item_id}", "order": "id.desc", "limit": "10"})
    else:
        with connect() as conn:
            item = row(conn, f"select * from {table_name} where id = ?", (item_id,))
            if not item:
                raise ValueError("Kontrol kaydı bulunamadı")
            document = row(conn, "select * from documents where id = ?", (item["document_id"],))
            client_row = row(conn, "select * from clients where id = ?", (item["client_id"],))
            feedback = rows(conn, "select * from feedback where item_type = ? and item_id = ? order by id desc limit 10", (item_type, item_id))
    return {
        "item_type": item_type,
        "item": item,
        "document": document,
        "client": client_row,
        "feedback": feedback,
        "editable_fields": sorted(config["fields"] - {"needs_review"}),
    }


def update_review_item_record(item_type: str, item_id: int, updates: dict[str, Any], rating: str, note: str, config: dict[str, Any]) -> None:
    table_name = config["table"]
    if using_supabase():
        current = client().single(table_name, {"select": "*", "id": f"eq.{item_id}"})
        if not current:
            raise ValueError("Kontrol kaydı bulunamadı")
        if updates:
            client().patch(table_name, {"id": f"eq.{item_id}"}, updates)
        if rating:
            create_feedback_record(item_type, item_id, rating, note)
        return
    with connect() as conn:
        current = row(conn, f"select * from {table_name} where id = ?", (item_id,))
        if not current:
            raise ValueError("Kontrol kaydı bulunamadı")
        if updates:
            set_clause = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(f"update {table_name} set {set_clause} where id = ?", (*updates.values(), item_id))
        if rating:
            conn.execute(
                "insert into feedback (item_type, item_id, rating, note) values (?, ?, ?, ?)",
                (item_type, item_id, rating, note),
            )
        conn.commit()


def review_needed(client_id: int | None = None, period: str | None = None) -> list[dict[str, Any]]:
    if using_supabase():
        result: list[dict[str, Any]] = []
        for table, item_type, title_field, detail_field in (
            ("bank_transactions", "bank", "description", "suggested_account_code"),
            ("z_reports", "z", "source_file", "gross_total"),
            ("receipts", "receipt", "source_file", "bookkeeping_status"),
        ):
            params = {"select": f"id,client_id,period,confidence,{title_field},{detail_field}", "needs_review": "eq.true", "order": "id.asc"}
            if client_id:
                params["client_id"] = f"eq.{client_id}"
            if period:
                params["period"] = f"eq.{period}"
            for item in client().select(table, params):
                result.append(
                    {
                        "item_type": item_type,
                        "id": item["id"],
                        "client_id": item["client_id"],
                        "period": item["period"],
                        "title": item.get(title_field, ""),
                        "confidence": item.get("confidence", ""),
                        "detail": item.get(detail_field, ""),
                    }
                )
        return result
    clauses = ["needs_review = 1"]
    args: list[Any] = []
    if client_id:
        clauses.append("client_id = ?")
        args.append(client_id)
    if period:
        clauses.append("period = ?")
        args.append(period)
    where = " and ".join(clauses)
    with connect() as conn:
        bank = rows(conn, f"select 'bank' as item_type, id, client_id, period, description as title, confidence, suggested_account_code as detail from bank_transactions where {where} order by id asc", tuple(args))
        z = rows(conn, f"select 'z' as item_type, id, client_id, period, source_file as title, confidence, gross_total as detail from z_reports where {where} order by id asc", tuple(args))
        receipt = rows(conn, f"select 'receipt' as item_type, id, client_id, period, source_file as title, confidence, bookkeeping_status as detail from receipts where {where} order by id asc", tuple(args))
    return bank + z + receipt


def export_sheets(client_id: int, period: str) -> tuple[dict[str, Any] | None, dict[str, list[dict[str, Any]]]]:
    client_row = get_client(client_id)
    if not client_row:
        return None, {}
    if using_supabase():
        supabase = client()
        filters = {"client_id": f"eq.{client_id}", "period": f"eq.{period}"}
        sheets = {
            "Banka_Hareketleri": supabase.select("bank_transactions", {"select": "*", **filters, "order": "date.asc,id.asc"}),
            "Z_Raporlari": supabase.select("z_reports", {"select": "*", **filters, "order": "report_date.asc,id.asc"}),
            "Fisler": supabase.select("receipts", {"select": "*", **filters, "order": "receipt_date.asc,id.asc"}),
            "Kontrol_Gerekenler": review_needed(client_id, period),
            "Ogrenilen_Kurallar": supabase.select("account_code_rules", {"select": "*", "or": f"(client_id.is.null,client_id.eq.{client_id})", "order": "id.asc"}),
        }
        return client_row, sheets
    with connect() as conn:
        where = "client_id = ? and period = ?"
        args = (client_id, period)
        sheets = {
            "Banka_Hareketleri": rows(conn, f"select * from bank_transactions where {where} order by date, id", args),
            "Z_Raporlari": rows(conn, f"select * from z_reports where {where} order by report_date, id", args),
            "Fisler": rows(conn, f"select * from receipts where {where} order by receipt_date, id", args),
            "Kontrol_Gerekenler": review_needed(client_id, period),
            "Ogrenilen_Kurallar": rows(conn, "select * from account_code_rules where client_id is null or client_id = ? order by id", (client_id,)),
        }
    return client_row, sheets


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for idx in range(2, 1000):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("Yükleme dosyası adı oluşturulamadı")
