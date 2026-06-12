from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import env as _env
from .parsers import parse_date, parse_decimal


ROOT = Path(__file__).resolve().parents[1]
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
MAX_INLINE_BYTES = 18 * 1024 * 1024


def gemini_configured() -> bool:
    return bool(gemini_api_key())


def gemini_model() -> str:
    return os.environ.get("MALIYARDIMCI_GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def extract_with_gemini(path: Path, module: str, client_id: int, period: str, filename: str) -> tuple[list[dict[str, Any]], list[str]]:
    if module not in {"receipt", "z"}:
        return [], []
    if not gemini_configured():
        return [], []
    if path.stat().st_size > MAX_INLINE_BYTES:
        return [], [f"Dosya Gemini doğrudan okuma sınırını aştı: {path.stat().st_size} bayt"]

    result = call_gemini(path, module)
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        return [], ["Gemini yapılandırılmış kayıt döndürmedi"]

    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        source_name = numbered_source(filename, "Fiş" if module == "receipt" else "Z Raporu", index, len(items))
        if module == "receipt":
            normalized.append(normalize_gemini_receipt(item, client_id, period, source_name))
        else:
            normalized.append(normalize_gemini_z_report(item, client_id, period, source_name))
    warnings = [f"Gemini {gemini_model()} ile işlendi"]
    notes = result.get("document_notes") if isinstance(result, dict) else ""
    if notes:
        warnings.append(str(notes)[:300])
    return normalized, warnings


def call_gemini(path: Path, module: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt_for(module)},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": schema_for(module),
        },
    }
    payload = json.dumps(request_body).encode("utf-8")
    request = urllib.request.Request(
        GEMINI_ENDPOINT.format(model=gemini_model()),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": gemini_api_key(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=55) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini isteği başarısız oldu: {exc.code} {detail[:400]}") from exc

    text = response_text(response_body)
    if not text:
        raise RuntimeError("Gemini boş yanıt döndürdü")
    return json.loads(text)


def response_text(response_body: dict[str, Any]) -> str:
    for candidate in response_body.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"]).strip()
    return ""


def prompt_for(module: str) -> str:
    if module == "z":
        return (
            "Türkçe mali müşavir asistanısın. Yüklenen belge bir Z raporu olabilir. "
            "Belgedeki her Z raporunu ayrı kayıt olarak çıkar. Emin olmadığın alanı boş bırak. "
            "Tutarları 1234.56 formatında döndür. Tarihleri YYYY-MM-DD formatında döndür. "
            "Tahmin uydurma; eksik veya okunamayan alan varsa needs_review=true yap."
        )
    return (
        "Türkçe mali müşavir asistanısın. Yüklenen belge fiş, e-arşiv fatura veya gider belgesi olabilir. "
        "Belgedeki her ayrı fişi ayrı kayıt olarak çıkar. VKN/TCKN satıcıya ait değilse boş bırak. "
        "Tutarları 1234.56 formatında döndür. Tarihleri YYYY-MM-DD formatında döndür. "
        "Muhasebe kararı verme; sadece belgeyi hazırla. Emin olmadığın alanı boş bırak ve needs_review=true yap."
    )


def schema_for(module: str) -> dict[str, Any]:
    if module == "z":
        item_properties = {
            "report_date": {"type": "STRING"},
            "device_brand": {"type": "STRING"},
            "device_serial": {"type": "STRING"},
            "z_no": {"type": "STRING"},
            "gross_total": {"type": "STRING"},
            "vat_lines": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"rate": {"type": "STRING"}, "amount": {"type": "STRING"}},
                },
            },
            "payment_breakdown": {
                "type": "OBJECT",
                "properties": {"cash": {"type": "STRING"}, "card": {"type": "STRING"}, "pos": {"type": "STRING"}},
            },
            "confidence": {"type": "NUMBER"},
            "needs_review": {"type": "BOOLEAN"},
            "raw_text": {"type": "STRING"},
            "notes": {"type": "STRING"},
        }
    else:
        item_properties = {
            "receipt_date": {"type": "STRING"},
            "merchant_name": {"type": "STRING"},
            "vkn_tckn": {"type": "STRING"},
            "document_no": {"type": "STRING"},
            "gross_total": {"type": "STRING"},
            "vat_total": {"type": "STRING"},
            "payment_method": {"type": "STRING", "enum": ["", "nakit", "kart", "havale", "diger"]},
            "bookkeeping_status": {"type": "STRING", "enum": ["uygun", "eksik", "okunamadi", "manuel_kontrol", "islenmez"]},
            "confidence": {"type": "NUMBER"},
            "needs_review": {"type": "BOOLEAN"},
            "raw_text": {"type": "STRING"},
            "notes": {"type": "STRING"},
        }
    return {
        "type": "OBJECT",
        "properties": {
            "items": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": item_properties,
                },
            },
            "document_notes": {"type": "STRING"},
        },
        "required": ["items"],
    }


def normalize_gemini_receipt(item: dict[str, Any], client_id: int, period: str, source_file: str) -> dict[str, Any]:
    receipt_date = normalize_date(item.get("receipt_date"))
    merchant = clean_string(item.get("merchant_name"))
    tax_id = clean_digits(item.get("vkn_tckn"), {10, 11})
    total = normalize_amount(item.get("gross_total"))
    vat = normalize_amount(item.get("vat_total"))
    status = clean_status(item.get("bookkeeping_status"))
    confidence = clean_confidence(item.get("confidence"))
    needs_review = bool(item.get("needs_review")) or confidence < 0.85 or not receipt_date or not merchant or not total or not tax_id
    if not tax_id and status == "uygun":
        status = "eksik"
    if not total and status == "uygun":
        status = "manuel_kontrol"
    return {
        "client_id": client_id,
        "period": period,
        "source_file": source_file,
        "receipt_date": receipt_date,
        "merchant_name": merchant,
        "vkn_tckn": tax_id,
        "document_no": clean_string(item.get("document_no")),
        "gross_total": total,
        "vat_total": vat,
        "payment_method": clean_payment(item.get("payment_method")),
        "bookkeeping_status": status,
        "confidence": confidence,
        "needs_review": needs_review,
        "raw_text": clean_string(item.get("raw_text") or item.get("notes")),
    }


def normalize_gemini_z_report(item: dict[str, Any], client_id: int, period: str, source_file: str) -> dict[str, Any]:
    report_date = normalize_date(item.get("report_date"))
    gross_total = normalize_amount(item.get("gross_total"))
    confidence = clean_confidence(item.get("confidence"))
    z_no = clean_string(item.get("z_no"))
    needs_review = bool(item.get("needs_review")) or confidence < 0.85 or not report_date or not gross_total or not z_no
    return {
        "client_id": client_id,
        "period": period,
        "source_file": source_file,
        "report_date": report_date,
        "device_brand": clean_string(item.get("device_brand")),
        "device_serial": clean_string(item.get("device_serial")),
        "z_no": z_no,
        "gross_total": gross_total,
        "vat_lines": json.dumps(normalize_vat_lines(item.get("vat_lines")), ensure_ascii=False),
        "payment_breakdown": json.dumps(normalize_payment_breakdown(item.get("payment_breakdown")), ensure_ascii=False),
        "confidence": confidence,
        "needs_review": needs_review,
        "raw_text": clean_string(item.get("raw_text") or item.get("notes")),
    }


def normalize_date(value: Any) -> str:
    return parse_date(clean_string(value)) or ""


def normalize_amount(value: Any) -> str:
    parsed = parse_decimal(clean_string(value))
    return "" if parsed is None else format(parsed, "f")


def clean_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.35
    if confidence > 1:
        confidence = confidence / 100
    return round(max(0.0, min(confidence, 0.99)), 2)


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()[:4000]


def clean_digits(value: Any, lengths: set[int]) -> str:
    digits = "".join(char for char in clean_string(value) if char.isdigit())
    return digits if len(digits) in lengths else ""


def clean_status(value: Any) -> str:
    status = clean_string(value)
    return status if status in {"uygun", "eksik", "okunamadi", "manuel_kontrol", "islenmez"} else "manuel_kontrol"


def clean_payment(value: Any) -> str:
    payment = clean_string(value)
    return payment if payment in {"", "nakit", "kart", "havale", "diger"} else "diger"


def normalize_vat_lines(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    lines = []
    for item in value:
        if not isinstance(item, dict):
            continue
        amount = normalize_amount(item.get("amount"))
        rate = clean_string(item.get("rate"))
        if amount or rate:
            lines.append({"rate": rate, "amount": amount})
    return lines


def normalize_payment_breakdown(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("cash", "card", "pos"):
        amount = normalize_amount(value.get(key))
        if amount:
            result[key] = amount
    return result


def numbered_source(filename: str, label: str, index: int, count: int) -> str:
    if count <= 1:
        return filename
    return f"{label} {index:02d} - {filename}"
