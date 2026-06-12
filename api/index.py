from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from malipilot.exporters import export_filename, write_workbook
from malipilot.server import (
    STATIC_DIR,
    api_state,
    create_client,
    create_feedback,
    create_rule,
    delete_document,
    delete_item,
    get_review_item,
    handle_stored_upload,
    handle_upload,
    handle_upload_url,
    update_review_item,
)
from malipilot.persistence import export_sheets, get_clients, get_document_record, read_document_content
from malipilot.storage import EXPORT_DIR


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query = parse_qs(environ.get("QUERY_STRING", ""))

    try:
        if method == "GET":
            status, headers, body = handle_get(path, query)
        elif method == "POST":
            payload = read_json(environ)
            status, headers, body = handle_post(path, payload)
        else:
            status, headers, body = json_payload({"error": "Yöntem desteklenmiyor"}, HTTPStatus.METHOD_NOT_ALLOWED)
    except Exception as exc:
        status, headers, body = json_payload({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    start_response(status_line(status), headers)
    return [body]


def handle_get(path: str, query: dict[str, list[str]]):
    if path == "/":
        return file_payload(STATIC_DIR / "index.html")
    if path.startswith("/static/"):
        return file_payload(STATIC_DIR / path.removeprefix("/static/"))
    if path == "/api/state":
        return json_payload(api_state())
    if path == "/api/clients":
        return json_payload(get_clients())
    if path == "/api/review-item":
        return json_payload(get_review_item(query))
    if path == "/api/export":
        return export_payload(query)
    if path == "/api/document":
        return document_payload(query)
    return json_payload({"error": "Sayfa bulunamadı"}, HTTPStatus.NOT_FOUND)


def handle_post(path: str, payload: dict):
    if path == "/api/clients":
        return json_payload(create_client(payload))
    if path == "/api/upload":
        return json_payload(handle_upload(payload))
    if path == "/api/upload-url":
        return json_payload(handle_upload_url(payload))
    if path == "/api/process-stored-upload":
        return json_payload(handle_stored_upload(payload))
    if path == "/api/rules":
        return json_payload(create_rule(payload))
    if path == "/api/feedback":
        return json_payload(create_feedback(payload))
    if path == "/api/review-item":
        return json_payload(update_review_item(payload))
    if path == "/api/delete-item":
        return json_payload(delete_item(payload))
    if path == "/api/delete-document":
        return json_payload(delete_document(payload))
    return json_payload({"error": "Sayfa bulunamadı"}, HTTPStatus.NOT_FOUND)


def read_json(environ) -> dict:
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ["wsgi.input"].read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def json_payload(payload: object, status: HTTPStatus = HTTPStatus.OK):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body)))], body


def file_payload(path: Path):
    if not path.exists() or not path.is_file():
        return json_payload({"error": "Dosya bulunamadı"}, HTTPStatus.NOT_FOUND)
    body = path.read_bytes()
    return (
        HTTPStatus.OK,
        [
            ("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
            ("Content-Length", str(len(body))),
        ],
        body,
    )


def export_payload(query: dict[str, list[str]]):
    client_id = int((query.get("client_id") or ["0"])[0])
    period = (query.get("period") or [""])[0]
    client, sheets = export_sheets(client_id, period)
    if not client:
        return json_payload({"error": "Mükellef bulunamadı"}, HTTPStatus.NOT_FOUND)
    filename = export_filename(client["name"], period)
    path = EXPORT_DIR / filename
    write_workbook(path, sheets)
    body = path.read_bytes()
    return (
        HTTPStatus.OK,
        [
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", f'attachment; filename="{filename}"'),
            ("Content-Length", str(len(body))),
        ],
        body,
    )


def document_payload(query: dict[str, list[str]]):
    document_id = int((query.get("document_id") or ["0"])[0])
    client_id = int((query.get("client_id") or ["0"])[0])
    if not document_id or not client_id:
        return json_payload({"error": "Belge ve mükellef gerekli"}, HTTPStatus.BAD_REQUEST)
    document = get_document_record(document_id, client_id)
    if not document:
        return json_payload({"error": "Belge bulunamadı"}, HTTPStatus.NOT_FOUND)
    try:
        body, filename = read_document_content(document)
    except FileNotFoundError:
        return json_payload({"error": "Belge dosyası bulunamadı"}, HTTPStatus.NOT_FOUND)
    return (
        HTTPStatus.OK,
        [
            ("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream"),
            ("Content-Disposition", f'inline; filename="{filename}"'),
            ("Content-Length", str(len(body))),
        ],
        body,
    )


def status_line(status: HTTPStatus) -> str:
    return f"{status.value} {status.phrase}"
