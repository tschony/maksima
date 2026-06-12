from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from calendar import monthrange
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import env as _env
from .parsers import parse_decimal
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

    def delete(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        result = self.request_json("DELETE", f"/rest/v1/{table}", params=filters, prefer="return=representation")
        return result if isinstance(result, list) else []

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

    def delete_object(self, object_path: str) -> None:
        quoted_path = "/".join(urllib.parse.quote(part) for part in object_path.split("/"))
        self.request("DELETE", f"/storage/v1/object/{SUPABASE_BUCKET}/{quoted_path}").read()

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


def delete_document_record(document_id: int, client_id: int) -> dict[str, Any]:
    document = get_document_record(document_id, client_id)
    if not document:
        raise ValueError("Belge bulunamadı")
    related = related_item_ids(document_id)
    if using_supabase():
        supabase = client()
        for item_type, item_ids in related.items():
            for item_id in item_ids:
                supabase.delete("feedback", {"item_type": f"eq.{item_type}", "item_id": f"eq.{item_id}"})
        for table_name in ("bank_transactions", "z_reports", "receipts", "extraction_runs"):
            supabase.delete(table_name, {"document_id": f"eq.{document_id}"})
        deleted_documents = supabase.delete("documents", {"id": f"eq.{document_id}", "client_id": f"eq.{client_id}"})
        if not deleted_documents:
            raise RuntimeError("Belge Supabase üzerinde silinemedi")
    else:
        with connect() as conn:
            for item_type, item_ids in related.items():
                for item_id in item_ids:
                    conn.execute("delete from feedback where item_type = ? and item_id = ?", (item_type, item_id))
            for table_name in ("bank_transactions", "z_reports", "receipts", "extraction_runs"):
                conn.execute(f"delete from {table_name} where document_id = ?", (document_id,))
            conn.execute("delete from documents where id = ? and client_id = ?", (document_id, client_id))
            conn.commit()
    storage_warning = delete_stored_document(document)
    return {"ok": True, "deleted": "document", "document_id": document_id, "storage_warning": storage_warning}


def delete_extracted_item_record(item_type: str, item_id: int, client_id: int) -> dict[str, Any]:
    table_name = item_table_name(item_type)
    if using_supabase():
        supabase = client()
        item = supabase.single(table_name, {"select": "*", "id": f"eq.{item_id}", "client_id": f"eq.{client_id}"})
        if not item:
            raise ValueError("Kayıt bulunamadı")
        supabase.delete("feedback", {"item_type": f"eq.{item_type}", "item_id": f"eq.{item_id}"})
        deleted_items = supabase.delete(table_name, {"id": f"eq.{item_id}", "client_id": f"eq.{client_id}"})
        if not deleted_items:
            raise RuntimeError("Kayıt Supabase üzerinde silinemedi")
    else:
        with connect() as conn:
            item = row(conn, f"select * from {table_name} where id = ? and client_id = ?", (item_id, client_id))
            if not item:
                raise ValueError("Kayıt bulunamadı")
            conn.execute("delete from feedback where item_type = ? and item_id = ?", (item_type, item_id))
            conn.execute(f"delete from {table_name} where id = ? and client_id = ?", (item_id, client_id))
            conn.commit()
    return {"ok": True, "deleted": item_type, "id": item_id, "document_id": item.get("document_id")}


def item_table_name(item_type: str) -> str:
    tables = {"bank": "bank_transactions", "z": "z_reports", "receipt": "receipts"}
    table_name = tables.get(item_type)
    if not table_name:
        raise ValueError("Bilinmeyen kayıt türü")
    return table_name


def related_item_ids(document_id: int) -> dict[str, list[int]]:
    if using_supabase():
        supabase = client()
        return {
            "bank": [int(item["id"]) for item in supabase.select("bank_transactions", {"select": "id", "document_id": f"eq.{document_id}"})],
            "z": [int(item["id"]) for item in supabase.select("z_reports", {"select": "id", "document_id": f"eq.{document_id}"})],
            "receipt": [int(item["id"]) for item in supabase.select("receipts", {"select": "id", "document_id": f"eq.{document_id}"})],
        }
    with connect() as conn:
        return {
            "bank": [int(item["id"]) for item in rows(conn, "select id from bank_transactions where document_id = ?", (document_id,))],
            "z": [int(item["id"]) for item in rows(conn, "select id from z_reports where document_id = ?", (document_id,))],
            "receipt": [int(item["id"]) for item in rows(conn, "select id from receipts where document_id = ?", (document_id,))],
        }


def delete_stored_document(document: dict[str, Any]) -> str:
    stored_path = str(document.get("stored_path") or "")
    try:
        if stored_path.startswith("supabase://"):
            object_path = stored_path.removeprefix("supabase://").split("/", 1)[-1]
            client().delete_object(object_path)
            return ""
        if stored_path:
            Path(stored_path).unlink(missing_ok=True)
    except Exception as exc:
        return f"Dosya kaydı silindi, ancak depodaki dosya silinemedi: {exc}"
    return ""


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


def list_z_devices(client_id: int | None = None) -> list[dict[str, Any]]:
    if using_supabase():
        params = {"select": "*", "order": "name.asc,id.asc"}
        if client_id:
            params["client_id"] = f"eq.{client_id}"
        try:
            return client().select("z_devices", params)
        except RuntimeError:
            return []
    query = "select * from z_devices"
    args: tuple[Any, ...] = ()
    if client_id:
        query += " where client_id = ?"
        args = (client_id,)
    query += " order by name, id"
    with connect() as conn:
        return rows(conn, query, args)


def create_z_device_record(client_id: int, name: str, brand: str = "", serial: str = "") -> dict[str, Any]:
    payload = {
        "client_id": client_id,
        "name": name.strip(),
        "brand": brand.strip(),
        "serial": serial.strip(),
        "active": True,
    }
    if not payload["client_id"] or not payload["name"]:
        raise ValueError("Kasa adı ve mükellef gerekli")
    if using_supabase():
        return client().insert("z_devices", payload)
    with connect() as conn:
        cur = conn.execute(
            "insert into z_devices (client_id, name, brand, serial, active) values (?, ?, ?, ?, 1)",
            (payload["client_id"], payload["name"], payload["brand"], payload["serial"]),
        )
        conn.commit()
        return row(conn, "select * from z_devices where id = ?", (cur.lastrowid,))


def find_or_create_z_device(client_id: int, brand: str, serial: str) -> dict[str, Any]:
    brand = clean_text(brand)
    serial = clean_text(serial)
    if using_supabase():
        supabase = client()
        if serial:
            existing = supabase.single("z_devices", {"select": "*", "client_id": f"eq.{client_id}", "serial": f"eq.{serial}"})
            if existing:
                return existing
        if not serial and brand:
            existing = supabase.single("z_devices", {"select": "*", "client_id": f"eq.{client_id}", "brand": f"eq.{brand}", "serial": "eq."})
            if existing:
                return existing
    else:
        with connect() as conn:
            if serial:
                existing = row(conn, "select * from z_devices where client_id = ? and serial = ? order by id limit 1", (client_id, serial))
                if existing:
                    return existing
            if not serial and brand:
                existing = row(conn, "select * from z_devices where client_id = ? and brand = ? and coalesce(serial, '') = '' order by id limit 1", (client_id, brand))
                if existing:
                    return existing

    label = " ".join(part for part in [brand or "Kasa", serial] if part).strip()
    if not label:
        label = "Belirsiz kasa"
    return create_z_device_record(client_id, label, brand, serial)


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


def update_document_module(document_id: int, module: str) -> None:
    payload = {"module": module}
    if using_supabase():
        client().patch("documents", {"id": f"eq.{document_id}"}, payload)
        return
    with connect() as conn:
        conn.execute("update documents set module = ? where id = ?", (module, document_id))
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


def prepare_z_report_item(document_id: int, item: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(item)
    client_id = int(prepared["client_id"])
    brand = clean_text(prepared.get("device_brand"))
    serial = clean_text(prepared.get("device_serial"))
    device = find_or_create_z_device(client_id, brand, serial)
    prepared["device_id"] = int(device["id"])
    prepared["device_brand"] = brand or clean_text(device.get("brand"))
    prepared["device_serial"] = serial or clean_text(device.get("serial"))

    document = get_document_record(document_id)
    selected_period = str(document.get("period") or prepared.get("period") or "") if document else str(prepared.get("period") or "")
    warnings = z_validation_warnings(prepared, selected_period)
    duplicate = z_report_duplicate_exists(prepared)
    if duplicate:
        warnings.append("Aynı Z raporu daha önce kaydedilmiş olabilir.")
    prepared["duplicate_flag"] = duplicate
    prepared["validation_warnings"] = json.dumps(unique_texts(warnings), ensure_ascii=False)
    prepared["needs_review"] = bool(prepared.get("needs_review")) or duplicate or bool(warnings)
    return prepared


def z_report_duplicate_exists(item: dict[str, Any]) -> bool:
    client_id = int(item.get("client_id") or 0)
    report_date = clean_text(item.get("report_date"))
    z_no = clean_text(item.get("z_no"))
    gross_total = clean_text(item.get("gross_total"))
    device_id = item.get("device_id")
    if not client_id or not report_date or not z_no:
        return False
    if using_supabase():
        params = {
            "select": "id",
            "client_id": f"eq.{client_id}",
            "report_date": f"eq.{report_date}",
            "z_no": f"eq.{z_no}",
            "gross_total": f"eq.{gross_total}",
            "limit": "1",
        }
        if device_id:
            params["device_id"] = f"eq.{device_id}"
        return bool(client().select("z_reports", params))
    with connect() as conn:
        if device_id:
            found = row(
                conn,
                "select id from z_reports where client_id = ? and device_id = ? and report_date = ? and z_no = ? and gross_total = ? limit 1",
                (client_id, device_id, report_date, z_no, gross_total),
            )
        else:
            found = row(
                conn,
                "select id from z_reports where client_id = ? and report_date = ? and z_no = ? and gross_total = ? limit 1",
                (client_id, report_date, z_no, gross_total),
            )
    return bool(found)


def z_validation_warnings(item: dict[str, Any], selected_period: str) -> list[str]:
    warnings: list[str] = []
    report_date = clean_text(item.get("report_date"))
    z_no = clean_text(item.get("z_no"))
    gross_total = clean_text(item.get("gross_total"))
    vat_lines = clean_text(item.get("vat_lines"))
    payment_breakdown = clean_text(item.get("payment_breakdown"))
    if not report_date:
        warnings.append("Z tarihi eksik.")
    if not z_no:
        warnings.append("Z no eksik.")
    if not gross_total:
        warnings.append("Günlük toplam tutar eksik.")
    if not vat_lines or vat_lines in {"[]", "{}"}:
        warnings.append("KDV satırları eksik.")
    gross = decimal_or_none(gross_total)
    vat_sum = vat_total_from_json(vat_lines, gross_total)
    if gross is not None and vat_sum is not None and vat_sum > gross:
        warnings.append("KDV toplamı satış toplamından büyük görünüyor.")
    vat_rates = vat_rates_from_json(vat_lines)
    if gross is not None and vat_sum is not None and vat_rates == {"20"}:
        expected_20 = (gross * Decimal("20") / Decimal("120")).quantize(Decimal("0.01"))
        if abs(vat_sum - expected_20) > Decimal("0.10"):
            warnings.append("KDV toplamı %20 dahil brüt tutarla uyuşmuyor; kontrol gerekli.")

    payment_sum = payment_total_from_json(payment_breakdown)
    if gross is not None and payment_sum is not None and abs(payment_sum - gross) > Decimal("0.10"):
        warnings.append("Ödeme kırılımı toplam satış tutarıyla uyuşmuyor.")
    if not clean_text(item.get("device_serial")):
        warnings.append("Cihaz seri no okunamadı; kasa eşleşmesi kontrol edilmeli.")
    return unique_texts(warnings)


def vat_total_from_json(value: str, gross_total: Any = None) -> Decimal | None:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return None
    total = sum_vat_amounts(parsed, decimal_or_none(gross_total))
    return total if total != Decimal("0") else None


def sum_vat_amounts(node: Any, gross_total: Decimal | None = None) -> Decimal:
    candidates: list[Decimal] = []
    collect_vat_amounts(node, candidates)
    if gross_total is not None and len(candidates) > 1:
        filtered = [amount for amount in candidates if amount == Decimal("0") or amount <= gross_total * Decimal("0.5")]
        if filtered:
            candidates = filtered
    return sum(candidates, Decimal("0"))


def collect_vat_amounts(node: Any, candidates: list[Decimal]) -> None:
    if isinstance(node, list):
        for item in node:
            collect_vat_amounts(item, candidates)
        return
    if not isinstance(node, dict):
        return

    explicit_keys = ("kdv", "kdv_amount", "vat", "vat_amount", "tax", "tax_amount")
    explicit_values = [decimal_or_none(node.get(key)) for key in explicit_keys if key in node]
    explicit_values = [value for value in explicit_values if value is not None]
    if explicit_values:
        candidates.extend(explicit_values)
    elif "amount" in node:
        amount = decimal_or_none(node.get("amount"))
        if amount is not None:
            candidates.append(amount)

    for value in node.values():
        if isinstance(value, (dict, list)):
            collect_vat_amounts(value, candidates)


def vat_rates_from_json(value: str) -> set[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return set()
    rates: set[str] = set()
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                rate = clean_text(item.get("rate")).replace("%", "")
                if rate:
                    rates.add(rate)
    return rates


def payment_total_from_json(value: str) -> Decimal | None:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return None
    total = sum_decimal_node(parsed, keys={"cash", "card", "pos", "nakit", "kart"})
    return total if total != Decimal("0") else None


def sum_decimal_node(node: Any, keys: set[str]) -> Decimal:
    total = Decimal("0")
    if isinstance(node, list):
        for item in node:
            total += sum_decimal_node(item, keys)
    elif isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if any(token in lowered for token in keys):
                total += decimal_or_none(value) or Decimal("0")
            elif isinstance(value, (dict, list)):
                total += sum_decimal_node(value, keys)
    return total


def decimal_or_none(value: Any) -> Decimal | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return parse_decimal(text)
    except (InvalidOperation, ValueError):
        return None


def day_from_date(value: Any) -> int | None:
    text = clean_text(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return int(text[8:10])
        except ValueError:
            return None
    return None


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).lower() in {"1", "true", "yes", "on"}


def json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def money_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def unique_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def insert_extracted_item_record(module: str, document_id: int, item: dict[str, Any]) -> None:
    if module == "z":
        item = prepare_z_report_item(document_id, item)
        payload = {
            "document_id": document_id,
            "client_id": item["client_id"],
            "device_id": item.get("device_id"),
            "period": item["period"],
            "source_file": item["source_file"],
            "report_date": item["report_date"],
            "device_brand": item["device_brand"],
            "device_serial": item["device_serial"],
            "z_no": item["z_no"],
            "gross_total": item["gross_total"],
            "vat_lines": item["vat_lines"],
            "payment_breakdown": item["payment_breakdown"],
            "cumulative_total": item.get("cumulative_total", ""),
            "cumulative_vat": item.get("cumulative_vat", ""),
            "duplicate_flag": bool(item.get("duplicate_flag")),
            "validation_warnings": item.get("validation_warnings", "[]"),
            "confidence": item["confidence"],
            "needs_review": bool(item["needs_review"]),
            "raw_text": item["raw_text"],
        }
        table = "z_reports"
        sqlite_sql = """
            insert into z_reports
            (document_id, client_id, device_id, period, source_file, report_date, device_brand, device_serial, z_no, gross_total,
             vat_lines, payment_breakdown, cumulative_total, cumulative_vat, duplicate_flag, validation_warnings,
             confidence, needs_review, raw_text)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        sqlite_args = (
            document_id,
            payload["client_id"],
            payload["device_id"],
            payload["period"],
            payload["source_file"],
            payload["report_date"],
            payload["device_brand"],
            payload["device_serial"],
            payload["z_no"],
            payload["gross_total"],
            payload["vat_lines"],
            payload["payment_breakdown"],
            payload["cumulative_total"],
            payload["cumulative_vat"],
            int(payload["duplicate_flag"]),
            payload["validation_warnings"],
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
        "z_devices": list_z_devices(),
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


def z_month_overview(client_id: int, period: str) -> dict[str, Any]:
    devices = list_z_devices(client_id)
    reports = z_reports_for_period(client_id, period)
    days = days_in_period(period)
    device_map: dict[str, dict[str, Any]] = {}
    for device in devices:
        key = str(device["id"])
        device_map[key] = {
            "device": device,
            "rows": [],
            "days": [],
            "missing_days": [],
            "duplicate_count": 0,
            "review_count": 0,
            "warning_count": 0,
            "gross_total": Decimal("0"),
            "vat_total": Decimal("0"),
        }
    for report in reports:
        key = str(report.get("device_id") or f"unassigned:{report.get('device_serial') or report.get('device_brand') or 'unknown'}")
        if key not in device_map:
            device_map[key] = {
                "device": {
                    "id": report.get("device_id") or "",
                    "client_id": client_id,
                    "name": report.get("device_serial") or report.get("device_brand") or "Belirsiz kasa",
                    "brand": report.get("device_brand") or "",
                    "serial": report.get("device_serial") or "",
                    "active": True,
                },
                "rows": [],
                "days": [],
                "missing_days": [],
                "duplicate_count": 0,
                "review_count": 0,
                "warning_count": 0,
                "gross_total": Decimal("0"),
                "vat_total": Decimal("0"),
            }
        bucket = device_map[key]
        bucket["rows"].append(report)
        bucket["gross_total"] += decimal_or_none(report.get("gross_total")) or Decimal("0")
        bucket["vat_total"] += vat_total_from_json(report.get("vat_lines") or "", report.get("gross_total")) or Decimal("0")
        if truthy(report.get("duplicate_flag")):
            bucket["duplicate_count"] += 1
        if truthy(report.get("needs_review")):
            bucket["review_count"] += 1
        if json_list(report.get("validation_warnings")):
            bucket["warning_count"] += 1

    expected_total = 0
    received_total = 0
    missing_total = 0
    duplicate_total = 0
    review_total = 0
    gross_total = Decimal("0")
    vat_total = Decimal("0")
    device_summaries: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    for bucket in device_map.values():
        by_day: dict[int, list[dict[str, Any]]] = {}
        for report in bucket["rows"]:
            day = day_from_date(report.get("report_date"))
            if day:
                by_day.setdefault(day, []).append(report)
        day_rows = []
        for day in range(1, days + 1):
            day_reports = by_day.get(day, [])
            if not day_reports:
                status = "eksik"
                bucket["missing_days"].append(day)
                missing_rows.append({"device": bucket["device"]["name"], "date": f"{period}-{day:02d}", "status": status})
            elif len(day_reports) > 1:
                status = "mukerrer"
            elif any(truthy(report.get("needs_review")) for report in day_reports):
                status = "kontrol"
            else:
                status = "tamam"
            day_rows.append(
                {
                    "day": day,
                    "date": f"{period}-{day:02d}",
                    "status": status,
                    "count": len(day_reports),
                    "z_nos": ", ".join(clean_text(report.get("z_no")) for report in day_reports if clean_text(report.get("z_no"))),
                    "gross_total": money_text(sum((decimal_or_none(report.get("gross_total")) or Decimal("0") for report in day_reports), Decimal("0"))),
                    "vat_total": money_text(sum((vat_total_from_json(report.get("vat_lines") or "", report.get("gross_total")) or Decimal("0") for report in day_reports), Decimal("0"))),
                }
            )
        expected_total += days
        received_total += len({day for day in by_day if day})
        missing_total += len(bucket["missing_days"])
        duplicate_total += int(bucket["duplicate_count"])
        review_total += int(bucket["review_count"])
        gross_total += bucket["gross_total"]
        vat_total += bucket["vat_total"]
        device_summaries.append(
            {
                "device": bucket["device"],
                "expected_days": days,
                "received_days": len({day for day in by_day if day}),
                "missing_days": bucket["missing_days"],
                "duplicate_count": bucket["duplicate_count"],
                "review_count": bucket["review_count"],
                "warning_count": bucket["warning_count"],
                "gross_total": money_text(bucket["gross_total"]),
                "vat_total": money_text(bucket["vat_total"]),
                "days": day_rows,
            }
        )
    return {
        "client_id": client_id,
        "period": period,
        "expected_reports": expected_total,
        "received_days": received_total,
        "missing_days": missing_total,
        "duplicate_count": duplicate_total,
        "review_count": review_total,
        "gross_total": money_text(gross_total),
        "vat_total": money_text(vat_total),
        "devices": device_summaries,
        "missing_rows": missing_rows,
    }


def z_reports_for_period(client_id: int, period: str) -> list[dict[str, Any]]:
    if using_supabase():
        return client().select("z_reports", {"select": "*", "client_id": f"eq.{client_id}", "period": f"eq.{period}", "order": "report_date.asc,id.asc"})
    with connect() as conn:
        return rows(conn, "select * from z_reports where client_id = ? and period = ? order by report_date, id", (client_id, period))


def days_in_period(period: str) -> int:
    try:
        year, month = [int(part) for part in period.split("-", 1)]
        return monthrange(year, month)[1]
    except Exception:
        return 31


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
    z_overview = z_month_overview(client_id, period)
    if using_supabase():
        supabase = client()
        filters = {"client_id": f"eq.{client_id}", "period": f"eq.{period}"}
        sheets = {
            "Banka_Hareketleri": supabase.select("bank_transactions", {"select": "*", **filters, "order": "date.asc,id.asc"}),
            "Z_Raporlari": supabase.select("z_reports", {"select": "*", **filters, "order": "report_date.asc,id.asc"}),
            "Z_Aylik_Ozet": z_overview_rows(z_overview),
            "Z_Eksik_Gunler": z_overview["missing_rows"],
            "Z_Cihazlar": z_device_export_rows(z_overview),
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
            "Z_Aylik_Ozet": z_overview_rows(z_overview),
            "Z_Eksik_Gunler": z_overview["missing_rows"],
            "Z_Cihazlar": z_device_export_rows(z_overview),
            "Fisler": rows(conn, f"select * from receipts where {where} order by receipt_date, id", args),
            "Kontrol_Gerekenler": review_needed(client_id, period),
            "Ogrenilen_Kurallar": rows(conn, "select * from account_code_rules where client_id is null or client_id = ? order by id", (client_id,)),
        }
        return client_row, sheets


def z_overview_rows(overview: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "period": overview["period"],
            "expected_reports": overview["expected_reports"],
            "received_days": overview["received_days"],
            "missing_days": overview["missing_days"],
            "duplicate_count": overview["duplicate_count"],
            "review_count": overview["review_count"],
            "gross_total": overview["gross_total"],
            "vat_total": overview["vat_total"],
        }
    ]


def z_device_export_rows(overview: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in overview["devices"]:
        device = entry["device"]
        result.append(
            {
                "device_id": device.get("id", ""),
                "name": device.get("name", ""),
                "brand": device.get("brand", ""),
                "serial": device.get("serial", ""),
                "expected_days": entry["expected_days"],
                "received_days": entry["received_days"],
                "missing_days": ", ".join(str(day) for day in entry["missing_days"]),
                "duplicate_count": entry["duplicate_count"],
                "review_count": entry["review_count"],
                "warning_count": entry["warning_count"],
                "gross_total": entry["gross_total"],
                "vat_total": entry["vat_total"],
            }
        )
    return result


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for idx in range(2, 1000):
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("Yükleme dosyası adı oluşturulamadı")
