from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import env as _env
from .parsers import parse_date, parse_decimal


ROOT = Path(__file__).resolve().parents[1]
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_UPLOAD_ENDPOINT = "https://generativelanguage.googleapis.com/upload/v1beta/files"
GEMINI_FILE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/{name}"
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
MAX_INLINE_BYTES = 18 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
GEMINI_FILE_WAIT_SECONDS = 22
GEMINI_RETRY_DELAYS = (2, 4)
TRANSIENT_GEMINI_STATUS_CODES = {429, 500, 502, 503, 504}
OPENAI_RETRY_DELAYS = (2, 4)
TRANSIENT_OPENAI_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiExtractionError(RuntimeError):
    def __init__(self, message: str, diagnostic: dict[str, Any]):
        super().__init__(message)
        self.diagnostic = diagnostic


class OpenAIExtractionError(RuntimeError):
    def __init__(self, message: str, diagnostic: dict[str, Any]):
        super().__init__(message)
        self.diagnostic = diagnostic


AI_EXTRACTION_ERRORS = (GeminiExtractionError, OpenAIExtractionError)


def ai_provider() -> str:
    requested = clean_string(os.environ.get("MALIYARDIMCI_AI_PROVIDER") or os.environ.get("AI_PROVIDER")).lower()
    if requested == "openai":
        return "openai" if openai_configured() else "yerel"
    if requested == "gemini":
        return "gemini" if gemini_configured() else "yerel"
    if openai_configured():
        return "openai"
    return "yerel"


def ai_model() -> str:
    provider = ai_provider()
    if provider == "openai":
        return openai_model()
    if provider == "gemini":
        return gemini_model()
    return ""


def extract_with_ai(path: Path, module: str, client_id: int, period: str, filename: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    provider = ai_provider()
    if provider == "openai":
        return extract_with_openai(path, module, client_id, period, filename)
    if provider == "gemini":
        return extract_with_gemini(path, module, client_id, period, filename)
    return [], [], {}


def openai_configured() -> bool:
    return bool(openai_api_key())


def openai_model() -> str:
    return os.environ.get("MALIYARDIMCI_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL


def openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY") or ""


def gemini_configured() -> bool:
    return bool(gemini_api_key())


def gemini_model() -> str:
    return os.environ.get("MALIYARDIMCI_GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def extract_with_openai(path: Path, module: str, client_id: int, period: str, filename: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    if module not in {"receipt", "z"}:
        return [], [], {}
    if not openai_configured():
        return [], [], {}

    started = time.monotonic()
    diagnostic = openai_diagnostic(path, module, filename)
    try:
        if path.stat().st_size > MAX_PDF_BYTES:
            raise RuntimeError(f"PDF sınırı aşılıyor: {path.stat().st_size} bayt")
        result = call_openai(path, module)
    except Exception as exc:
        diagnostic.update(
            {
                "status": "failed",
                "duration_ms": elapsed_ms(started),
                "error_message": str(exc)[:2000],
            }
        )
        raise OpenAIExtractionError(str(exc), diagnostic) from exc

    items = result.get("items") if isinstance(result, dict) else None
    diagnostic["raw_response"] = truncate_json(result)
    diagnostic["duration_ms"] = elapsed_ms(started)
    notes = result.get("document_notes") if isinstance(result, dict) else ""
    if not isinstance(items, list) or not items:
        diagnostic["status"] = "failed"
        diagnostic["error_message"] = "ChatGPT yapılandırılmış kayıt döndürmedi"
        warnings = ["ChatGPT yapılandırılmış kayıt döndürmedi"]
        if notes:
            warnings.append(str(notes)[:300])
        return [], warnings, diagnostic

    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        source_name = numbered_source(filename, "Fiş" if module == "receipt" else "Z Raporu", index, len(items))
        if module == "receipt":
            normalized.append(normalize_gemini_receipt(item, client_id, period, source_name))
        else:
            normalized.append(normalize_gemini_z_report(item, client_id, period, source_name))
    diagnostic["status"] = "ok"
    diagnostic["item_count"] = len(normalized)
    warnings = [f"ChatGPT {openai_model()} ile işlendi"]
    if notes:
        warnings.append(str(notes)[:300])
    return normalized, warnings, diagnostic


def openai_diagnostic(path: Path, module: str, filename: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "provider": "openai",
        "model": openai_model(),
        "module": module,
        "file_name": filename,
        "file_size": path.stat().st_size,
        "input_method": "input_file" if mime_type == "application/pdf" else "input_image",
        "status": "started",
        "item_count": 0,
        "duration_ms": 0,
        "raw_response": "",
        "error_message": "",
    }


def extract_with_gemini(path: Path, module: str, client_id: int, period: str, filename: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    if module not in {"receipt", "z"}:
        return [], [], {}
    if not gemini_configured():
        return [], [], {}

    started = time.monotonic()
    diagnostic = gemini_diagnostic(path, module, filename)
    try:
        if diagnostic["input_method"] == "files_api" and path.stat().st_size > MAX_PDF_BYTES:
            raise RuntimeError(f"PDF Gemini sınırını aşıyor: {path.stat().st_size} bayt")
        result = call_gemini(path, module)
    except Exception as exc:
        diagnostic.update(
            {
                "status": "failed",
                "duration_ms": elapsed_ms(started),
                "error_message": str(exc)[:2000],
            }
        )
        raise GeminiExtractionError(str(exc), diagnostic) from exc

    items = result.get("items") if isinstance(result, dict) else None
    diagnostic["raw_response"] = truncate_json(result)
    diagnostic["duration_ms"] = elapsed_ms(started)
    if not isinstance(items, list) or not items:
        diagnostic["status"] = "failed"
        diagnostic["error_message"] = "Gemini yapılandırılmış kayıt döndürmedi"
        return [], ["Gemini yapılandırılmış kayıt döndürmedi"], diagnostic

    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        source_name = numbered_source(filename, "Fiş" if module == "receipt" else "Z Raporu", index, len(items))
        if module == "receipt":
            normalized.append(normalize_gemini_receipt(item, client_id, period, source_name))
        else:
            normalized.append(normalize_gemini_z_report(item, client_id, period, source_name))
    diagnostic["status"] = "ok"
    diagnostic["item_count"] = len(normalized)
    warnings = [f"Gemini {gemini_model()} ile işlendi"]
    notes = result.get("document_notes") if isinstance(result, dict) else ""
    if notes:
        warnings.append(str(notes)[:300])
    return normalized, warnings, diagnostic


def gemini_diagnostic(path: Path, module: str, filename: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "provider": "gemini",
        "model": gemini_model(),
        "module": module,
        "file_name": filename,
        "file_size": path.stat().st_size,
        "input_method": "files_api" if should_use_file_api(path, mime_type) else "inline_data",
        "status": "started",
        "item_count": 0,
        "duration_ms": 0,
        "raw_response": "",
        "error_message": "",
    }


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def truncate_json(value: Any, max_chars: int = 80000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:max_chars]


def call_gemini(path: Path, module: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if should_use_file_api(path, mime_type):
        file_info = upload_gemini_file(path, mime_type)
        return generate_with_file(file_info, module)
    return generate_with_inline_data(path, module, mime_type)


def call_openai(path: Path, module: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not openai_supported_input(mime_type):
        raise RuntimeError("Bu belge türü ChatGPT okuması için desteklenmiyor")
    response_body = post_openai_response(openai_request_body(path, module, mime_type))
    text = openai_response_text(response_body)
    if not text:
        raise RuntimeError("ChatGPT boş yanıt döndürdü")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ChatGPT yapılandırılmış JSON döndürmedi") from exc


def openai_supported_input(mime_type: str) -> bool:
    return mime_type == "application/pdf" or mime_type.startswith("image/")


def openai_request_body(path: Path, module: str, mime_type: str) -> dict[str, Any]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    if mime_type == "application/pdf":
        file_part = {
            "type": "input_file",
            "filename": path.name,
            "file_data": f"data:{mime_type};base64,{encoded}",
        }
    else:
        file_part = {
            "type": "input_image",
            "image_url": f"data:{mime_type};base64,{encoded}",
            "detail": os.environ.get("MALIYARDIMCI_OPENAI_IMAGE_DETAIL", "high"),
        }
    return {
        "model": openai_model(),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_for(module)},
                    file_part,
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": f"maliyardimci_{module}_extraction",
                "strict": True,
                "schema": openai_schema_for(module),
            }
        },
        "store": False,
    }


def should_use_file_api(path: Path, mime_type: str) -> bool:
    return mime_type == "application/pdf" and path.stat().st_size > MAX_INLINE_BYTES


def generate_with_inline_data(path: Path, module: str, mime_type: str) -> dict[str, Any]:
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
    return post_generate_content(request_body)


def upload_gemini_file(path: Path, mime_type: str) -> dict[str, Any]:
    size = path.stat().st_size
    start_request = urllib.request.Request(
        f"{GEMINI_UPLOAD_ENDPOINT}?key={gemini_api_key()}",
        data=json.dumps({"file": {"display_name": path.name}}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(start_request, timeout=20) as response:
            upload_url = response.headers.get("X-Goog-Upload-URL") or response.headers.get("x-goog-upload-url")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini dosya yükleme başlatılamadı: {exc.code} {detail[:400]}") from exc

    if not upload_url:
        raise RuntimeError("Gemini dosya yükleme adresi alınamadı")

    upload_request = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        headers={
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(upload_request, timeout=55) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini dosya yükleme başarısız oldu: {exc.code} {detail[:400]}") from exc

    file_info = unwrap_gemini_file_response(body)
    if not file_info:
        raise RuntimeError("Gemini dosya yükleme yanıtı okunamadı")
    return wait_for_gemini_file(file_info)


def unwrap_gemini_file_response(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("file"), dict):
        return value["file"]
    return value if isinstance(value, dict) else {}


def wait_for_gemini_file(file_info: dict[str, Any]) -> dict[str, Any]:
    name = clean_string(file_info.get("name"))
    if not name:
        return file_info

    deadline = time.monotonic() + GEMINI_FILE_WAIT_SECONDS
    current = file_info
    while clean_string(current.get("state")).upper() == "PROCESSING" and time.monotonic() < deadline:
        time.sleep(2)
        request = urllib.request.Request(
            GEMINI_FILE_ENDPOINT.format(name=name),
            headers={"x-goog-api-key": gemini_api_key()},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                current = unwrap_gemini_file_response(json.loads(response.read().decode("utf-8") or "{}"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini dosya durumu alınamadı: {exc.code} {detail[:400]}") from exc

    state = clean_string(current.get("state")).upper()
    if state == "FAILED":
        raise RuntimeError("Gemini dosya işleme başarısız oldu")
    if state == "PROCESSING":
        raise RuntimeError("Gemini dosya işleme zaman aşımına uğradı")
    return current


def generate_with_file(file_info: dict[str, Any], module: str) -> dict[str, Any]:
    file_uri = clean_string(file_info.get("uri"))
    mime_type = clean_string(file_info.get("mimeType") or file_info.get("mime_type")) or "application/pdf"
    if not file_uri:
        raise RuntimeError("Gemini dosya adresi alınamadı")

    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt_for(module)},
                    {
                        "file_data": {
                            "mime_type": mime_type,
                            "file_uri": file_uri,
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
    return post_generate_content(request_body)


def post_generate_content(request_body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(request_body).encode("utf-8")
    response_body = post_gemini_json(payload)

    text = response_text(response_body)
    if not text:
        raise RuntimeError("Gemini boş yanıt döndürdü")
    return json.loads(text)


def post_gemini_json(payload: bytes) -> dict[str, Any]:
    last_message = "Gemini isteği başarısız oldu"
    for attempt in range(len(GEMINI_RETRY_DELAYS) + 1):
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
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_message = friendly_gemini_error(exc.code, detail)
            if is_transient_gemini_error(exc.code, detail) and attempt < len(GEMINI_RETRY_DELAYS):
                time.sleep(GEMINI_RETRY_DELAYS[attempt])
                continue
            raise RuntimeError(last_message) from exc
    raise RuntimeError(last_message)


def post_openai_response(request_body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(request_body).encode("utf-8")
    last_message = "ChatGPT isteği başarısız oldu"
    for attempt in range(len(OPENAI_RETRY_DELAYS) + 1):
        request = urllib.request.Request(
            OPENAI_RESPONSES_ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {openai_api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_message = friendly_openai_error(exc.code, detail)
            if exc.code in TRANSIENT_OPENAI_STATUS_CODES and attempt < len(OPENAI_RETRY_DELAYS):
                time.sleep(OPENAI_RETRY_DELAYS[attempt])
                continue
            raise RuntimeError(last_message) from exc
    raise RuntimeError(last_message)


def friendly_openai_error(status_code: int, detail: str) -> str:
    parsed = parsed_openai_error(detail)
    code = parsed.get("code", "")
    message = parsed.get("message", "")
    if status_code == 401:
        return "OpenAI anahtarı geçersiz veya eksik."
    if status_code == 429:
        return "OpenAI kullanım sınırı geçici olarak doldu. Birkaç dakika sonra tekrar dene."
    if status_code in {500, 502, 503, 504}:
        return "OpenAI geçici olarak yanıt veremedi. Birkaç dakika sonra tekrar dene."
    if status_code == 400:
        return f"OpenAI isteği geçersiz oldu: {message[:220] or code or 'ayarlar kontrol edilmeli'}"
    return f"OpenAI isteği başarısız oldu: HTTP {status_code}"


def parsed_openai_error(detail: str) -> dict[str, str]:
    try:
        payload = json.loads(detail or "{}")
    except json.JSONDecodeError:
        return {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return {}
    return {
        "code": clean_string(error.get("code")),
        "message": clean_string(error.get("message")),
        "type": clean_string(error.get("type")),
    }


def is_transient_gemini_error(status_code: int, detail: str) -> bool:
    status = parsed_gemini_error(detail).get("status", "")
    return status_code in TRANSIENT_GEMINI_STATUS_CODES or status in {"UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED"}


def friendly_gemini_error(status_code: int, detail: str) -> str:
    parsed = parsed_gemini_error(detail)
    status = parsed.get("status", "")
    message = parsed.get("message", "")
    if status_code in {429, 503} or status in {"UNAVAILABLE", "RESOURCE_EXHAUSTED"}:
        return "Gemini şu anda yoğun. Birkaç dakika sonra tekrar dene."
    if status_code in {500, 502, 504} or status in {"INTERNAL", "DEADLINE_EXCEEDED"}:
        return "Gemini geçici olarak yanıt veremedi. Birkaç dakika sonra tekrar dene."
    if status_code == 400:
        return f"Gemini isteği geçersiz oldu: {message[:220] or 'ayarlar kontrol edilmeli'}"
    return f"Gemini isteği başarısız oldu: HTTP {status_code}"


def parsed_gemini_error(detail: str) -> dict[str, str]:
    try:
        payload = json.loads(detail or "{}")
    except json.JSONDecodeError:
        return {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return {}
    return {
        "status": clean_string(error.get("status")),
        "message": clean_string(error.get("message")),
    }


def response_text(response_body: dict[str, Any]) -> str:
    for candidate in response_body.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"]).strip()
    return ""


def openai_response_text(response_body: dict[str, Any]) -> str:
    output_text = response_body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    for output in response_body.get("output", []):
        if not isinstance(output, dict):
            continue
        for part in output.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                return str(part["text"]).strip()
    return ""


def prompt_for(module: str) -> str:
    if module == "z":
        return (
            "Türkçe mali müşavir asistanısın. Yüklenen belge bir Z raporu olabilir. "
            "Z GÜNLÜK RAPORU, Z RAPORU, Z SAYAÇ veya Z NO görünen belge müşteri fişi değil, günlük Z raporudur. "
            "Fotoğrafta aynı uzun kâğıt üzerinde birden fazla Z raporu varsa her Z raporunu ayrı kayıt olarak çıkar; "
            "arka plandaki başka kâğıtları ayrı belge gibi uydurma. Emin olmadığın alanı boş bırak. "
            "gross_total alanına yalnızca günlük satış toplamını yaz: TOP, TOPLAM, %20 TOPLAM veya Mali Veri bölümündeki günlük tutar. "
            "vat_lines içindeki amount alanına yalnızca KDV tutarını yaz; %20 TOPLAM veya brüt satış tutarını KDV amount olarak yazma. "
            "KÜM TOP ve KÜM KDV kümülatif kasa sayaçlarıdır; bunları asla günlük gross_total veya KDV tutarı olarak yazma. "
            "Eğer günlük TOP/KDV okunamıyor ama ardışık KÜM TOP/KÜM KDV değerleri görünüyorsa sadece farktan emin olduğunda günlük değer olarak kullan. "
            "Tutarları 1234.56 formatında döndür. Tarihleri YYYY-MM-DD formatında döndür. "
            "Tahmin uydurma; eksik veya okunamayan alan varsa needs_review=true yap. "
            "Şemadaki bütün alanları döndür; bilinmeyen metin alanları boş string olsun."
        )
    return (
        "Türkçe mali müşavir asistanısın. Yüklenen belge fiş, e-arşiv fatura veya gider belgesi olabilir. "
        "Z GÜNLÜK RAPORU, Z RAPORU, Z SAYAÇ veya Z NO içeren günlük kasa kapanışını müşteri fişi gibi işleme; "
        "böyle bir belge görürsen items=[] döndür ve document_notes içinde bunun Z raporu olduğunu belirt. "
        "Belgedeki her ayrı fişi ayrı kayıt olarak çıkar. VKN/TCKN satıcıya ait değilse boş bırak. "
        "Tutarları 1234.56 formatında döndür. Tarihleri YYYY-MM-DD formatında döndür. "
        "Muhasebe kararı verme; sadece belgeyi hazırla. Emin olmadığın alanı boş bırak ve needs_review=true yap. "
        "Şemadaki bütün alanları döndür; bilinmeyen metin alanları boş string olsun."
    )


def openai_schema_for(module: str) -> dict[str, Any]:
    if module == "z":
        item_properties = {
            "report_date": {"type": "string"},
            "device_brand": {"type": "string"},
            "device_serial": {"type": "string"},
            "z_no": {"type": "string"},
            "gross_total": {"type": "string"},
            "cumulative_total": {"type": "string"},
            "cumulative_vat": {"type": "string"},
            "vat_lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"rate": {"type": "string"}, "amount": {"type": "string"}},
                    "required": ["rate", "amount"],
                    "additionalProperties": False,
                },
            },
            "payment_breakdown": {
                "type": "object",
                "properties": {"cash": {"type": "string"}, "card": {"type": "string"}, "pos": {"type": "string"}},
                "required": ["cash", "card", "pos"],
                "additionalProperties": False,
            },
            "confidence": {"type": "number"},
            "needs_review": {"type": "boolean"},
            "raw_text": {"type": "string"},
            "notes": {"type": "string"},
        }
    else:
        item_properties = {
            "receipt_date": {"type": "string"},
            "merchant_name": {"type": "string"},
            "vkn_tckn": {"type": "string"},
            "document_no": {"type": "string"},
            "gross_total": {"type": "string"},
            "vat_total": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["belirsiz", "nakit", "kart", "havale", "diger"]},
            "bookkeeping_status": {"type": "string", "enum": ["uygun", "eksik", "okunamadi", "manuel_kontrol", "islenmez"]},
            "confidence": {"type": "number"},
            "needs_review": {"type": "boolean"},
            "raw_text": {"type": "string"},
            "notes": {"type": "string"},
        }
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": item_properties,
                    "required": list(item_properties.keys()),
                    "additionalProperties": False,
                },
            },
            "document_notes": {"type": "string"},
        },
        "required": ["items", "document_notes"],
        "additionalProperties": False,
    }


def schema_for(module: str) -> dict[str, Any]:
    if module == "z":
        item_properties = {
            "report_date": {"type": "STRING"},
            "device_brand": {"type": "STRING"},
            "device_serial": {"type": "STRING"},
            "z_no": {"type": "STRING"},
            "gross_total": {"type": "STRING"},
            "cumulative_total": {"type": "STRING"},
            "cumulative_vat": {"type": "STRING"},
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
            "payment_method": {"type": "STRING", "enum": ["belirsiz", "nakit", "kart", "havale", "diger"]},
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
        "period": receipt_date[:7] if receipt_date and len(receipt_date) >= 7 else period,
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
        "period": report_date[:7] if report_date and len(report_date) >= 7 else period,
        "source_file": source_file,
        "report_date": report_date,
        "device_brand": clean_string(item.get("device_brand")),
        "device_serial": clean_string(item.get("device_serial")),
        "z_no": z_no,
        "gross_total": gross_total,
        "cumulative_total": normalize_amount(item.get("cumulative_total")),
        "cumulative_vat": normalize_amount(item.get("cumulative_vat")),
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
    if payment == "belirsiz":
        return ""
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
