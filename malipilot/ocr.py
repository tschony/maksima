from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from .parsers import parse_date, parse_decimal


VISION_OCR_SCRIPT = r"""
import Foundation
import Vision
import AppKit

var output: [[String: String]] = []

for path in CommandLine.arguments.dropFirst() {
    var text = ""
    if let image = NSImage(contentsOfFile: path),
       let tiff = image.tiffRepresentation,
       let bitmap = NSBitmapImageRep(data: tiff),
       let cgImage = bitmap.cgImage {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["tr-TR", "en-US"]
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        try? handler.perform([request])
        let lines = (request.results ?? []).compactMap { observation in
            observation.topCandidates(1).first?.string
        }
        text = lines.joined(separator: "\n")
    }
    output.append(["path": path, "text": text])
}

let data = try JSONSerialization.data(withJSONObject: output)
print(String(data: data, encoding: .utf8)!)
"""


def run_ocr(path: Path) -> str:
    return "\n\n".join(page["raw_text"] for page in run_ocr_pages(path)).strip()


def run_ocr_pages(path: Path) -> list[dict[str, Any]]:
    target = path
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        targets = [target]
        if path.suffix.lower() == ".pdf":
            temp_dir = tempfile.TemporaryDirectory()
            prefix = Path(temp_dir.name) / "page"
            if not shutil.which("pdftoppm"):
                return [{"page_number": 1, "source_file": path.name, "raw_text": ""}]
            subprocess.run(["pdftoppm", "-jpeg", "-scale-to", "1800", str(path), str(prefix)], check=True, capture_output=True)
            targets = sorted(Path(temp_dir.name).glob("page-*.jpg"))
        texts = run_vision_ocr_batch(targets)
        pages = []
        for index, page_path in enumerate(targets, start=1):
            raw_text = texts.get(str(page_path), "")
            if not raw_text:
                raw_text = run_tesseract_ocr(page_path)
            pages.append({"page_number": index, "source_file": path.name, "raw_text": raw_text.strip()})
        return pages or [{"page_number": 1, "source_file": path.name, "raw_text": ""}]
    finally:
        if temp_dir:
            temp_dir.cleanup()


def run_vision_ocr_batch(paths: list[Path]) -> dict[str, str]:
    if not paths or not shutil.which("swift"):
        return {}
    try:
        result = subprocess.run(
            ["swift", "-", *[str(path) for path in paths]],
            input=VISION_OCR_SCRIPT,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(20, len(paths) * 8),
        )
    except subprocess.TimeoutExpired:
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return {item.get("path", ""): item.get("text", "") for item in parsed if isinstance(item, dict)}


def run_tesseract_ocr(path: Path) -> str:
    if not shutil.which("tesseract"):
        return ""
    result = subprocess.run(
        ["tesseract", str(path), "stdout", "-l", "tur+eng", "--psm", "6"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def extract_z_report(raw_text: str, client_id: int, period: str, source_file: str) -> dict[str, Any]:
    text = normalize_ocr_text(raw_text)
    report_date = parse_ocr_date(text) or ""
    total = first_amount_after(text, ["GENEL TOPLAM", "TOPLAM", "TOTAL", "SATIS", "SATIŞ"])
    z_no = first_match(text, [r"\bZ\s*(?:NO|NUMARA|RAPOR)\s*[:\-]?\s*([A-Z0-9\-]+)", r"\bZ\s*([0-9]{2,})\b"])
    vat_lines = extract_vat_lines(text)
    payments = extract_payment_breakdown(text)
    confidence = 0.2
    if parse_ocr_date(text):
        confidence += 0.25
    if total:
        confidence += 0.25
    if z_no:
        confidence += 0.2
    if vat_lines or payments:
        confidence += 0.1
    return {
        "client_id": client_id,
        "period": report_date[:7] if report_date and len(report_date) >= 7 else period,
        "source_file": source_file,
        "report_date": report_date,
        "device_brand": guess_device_brand(text),
        "device_serial": first_match(text, [r"(?:SERI|SERİ|SERIAL)\s*(?:NO)?\s*[:\-]?\s*([A-Z0-9\-]+)"]) or "",
        "z_no": z_no or "",
        "gross_total": total or "",
        "vat_lines": json.dumps(vat_lines, ensure_ascii=False),
        "payment_breakdown": json.dumps(payments, ensure_ascii=False),
        "confidence": round(min(confidence, 0.95), 2),
        "needs_review": confidence < 0.75 or not z_no,
        "raw_text": raw_text,
    }


def extract_receipt(raw_text: str, client_id: int, period: str, source_file: str) -> dict[str, Any]:
    text = normalize_ocr_text(raw_text)
    receipt_date = parse_ocr_date(text) or ""
    tax_id = extract_tax_id(text)
    total = receipt_total(text)
    vat = receipt_vat(text, total)
    merchant = guess_merchant(raw_text)
    status = "uygun"
    confidence = 0.2
    if parse_ocr_date(text):
        confidence += 0.2
    if total:
        confidence += 0.25
    if merchant:
        confidence += 0.15
    if tax_id:
        confidence += 0.2
    else:
        status = "eksik"
    if not raw_text.strip():
        status = "okunamadi"
    if confidence < 0.65 and status == "uygun":
        status = "manuel_kontrol"
    return {
        "client_id": client_id,
        "period": receipt_date[:7] if receipt_date and len(receipt_date) >= 7 else period,
        "source_file": source_file,
        "receipt_date": receipt_date,
        "merchant_name": merchant,
        "vkn_tckn": tax_id,
        "document_no": extract_document_no(text),
        "gross_total": total or "",
        "vat_total": vat or "",
        "payment_method": guess_payment_method(text),
        "bookkeeping_status": status,
        "confidence": round(min(confidence, 0.95), 2),
        "needs_review": True,
        "raw_text": raw_text,
    }


def normalize_ocr_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").upper().replace("İ", "I")


def first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return None


def extract_tax_id(text: str) -> str:
    preferred = first_match(
        text,
        [
            r"\b(?:VDM|VOM|V\.D\.|VERGI\s*DAIRESI|VERGI\s*NO|VERGİ\s*NO)\s*[:\-\/]?\s*(\d{10})\b",
            r"\b[A-ZÇĞIİÖŞÜ]+\/(\d{10})\b",
        ],
    )
    if preferred:
        return preferred
    for match in re.finditer(r"\b(?:VKN|TCKN|TC\s*NO)\s*[:\-]?\s*(\d{10,11})\b", text, re.I):
        start = max(0, match.start() - 12)
        if "MUSTERI" in text[start:match.start()] or "MÜŞTERI" in text[start:match.start()]:
            continue
        return match.group(1)
    return ""


def extract_document_no(text: str) -> str:
    return first_match(
        text,
        [
            r"\bBELGE\s*NO\s*[:\-]?\s*([A-Z0-9\-]{3,})",
            r"\bFIS\s*NO\s*[:\-]?\s*([A-Z0-9\-]{3,})",
            r"\bFİŞ\s*NO\s*[:\-]?\s*([A-Z0-9\-]{3,})",
            r"\bFATURA\s*NO\s*[:\-]?\s*([A-Z0-9\-]{3,})",
        ],
    ) or ""


def first_amount_after(text: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = rf"{re.escape(label)}[^0-9\-]*([0-9][0-9\.,]*)"
        match = re.search(pattern, text, re.I)
        if match:
            amount = parse_ocr_amount(match.group(1))
            return "" if amount is None else format(amount, "f")
    amounts = re.findall(r"\b[0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})\b", text)
    if amounts:
        amount = parse_ocr_amount(amounts[-1])
        return "" if amount is None else format(amount, "f")
    return None


def all_ocr_amounts(text: str) -> list[Decimal]:
    amounts = []
    for value in re.findall(r"\b[0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})\b", text):
        parsed = parse_ocr_amount(value)
        if parsed is not None:
            amounts.append(parsed)
    return amounts


def receipt_total(text: str) -> str | None:
    labelled = first_amount_after(text, ["ÖDENECEK TUTAR", "ODENECEK TUTAR", "GENEL TOPLAM", "TOPLAM", "TOTAL", "TUTAR"])
    amounts = all_ocr_amounts(text)
    if not amounts:
        return labelled
    largest = max(amounts)
    labelled_amount = parse_ocr_amount(labelled or "")
    if labelled_amount is None or largest > labelled_amount:
        return format(largest, "f")
    return format(labelled_amount, "f")


def receipt_vat(text: str, total: str | None) -> str | None:
    labelled = first_amount_after(text, ["TOPKDV", "TOPLAM KDV", "KDV TUTARI", "KDV", "VAT"])
    total_amount = parse_ocr_amount(total or "")
    if total_amount is None:
        return labelled
    labelled_amount = parse_ocr_amount(labelled or "")
    if labelled_amount is not None and Decimal("0") < labelled_amount < total_amount * Decimal("0.5"):
        return format(labelled_amount, "f")
    kdv_index = max(text.rfind("KDV"), text.rfind("VAT"))
    candidates = all_ocr_amounts(text[kdv_index:] if kdv_index >= 0 else text)
    for candidate in reversed(candidates):
        if Decimal("0") < candidate < total_amount * Decimal("0.5"):
            return format(candidate, "f")
    return labelled


def parse_ocr_amount(value: str):
    cleaned = re.sub(r"[^0-9,\.\-+]", "", value or "")
    if cleaned and "," not in cleaned and "." not in cleaned and cleaned.lstrip("-+").isdigit() and len(cleaned.lstrip("-+")) >= 4:
        sign = "-" if cleaned.startswith("-") else ""
        digits = cleaned.lstrip("-+")
        cleaned = f"{sign}{digits[:-2]}.{digits[-2:]}"
    return parse_decimal(cleaned)


def parse_ocr_date(text: str) -> str | None:
    parsed = parse_date(text)
    if parsed:
        return parsed
    match = re.search(r"\b(\d{2})(\d{2})(20\d{2})\b", text)
    if match:
        return parse_date(".".join(match.groups()))
    return None


def extract_vat_lines(text: str) -> list[dict[str, str]]:
    lines = []
    for rate, amount in re.findall(r"(?:KDV|VAT)\s*%?\s*(\d{1,2})[^0-9]+([0-9][0-9\.,]*)", text, re.I):
        parsed = parse_ocr_amount(amount)
        lines.append({"rate": rate, "amount": "" if parsed is None else format(parsed, "f")})
    return lines


def extract_payment_breakdown(text: str) -> dict[str, str]:
    result = {}
    labels = {"NAKIT": "cash", "NAKİT": "cash", "KREDI": "card", "KREDİ": "card", "POS": "pos", "KART": "card"}
    for label, key in labels.items():
        amount = first_amount_after(text, [label])
        if amount:
            result[key] = amount
    return result


def guess_device_brand(text: str) -> str:
    for brand in ["BEKO", "INGENICO", "PROFILO", "VERIFONE", "PAX"]:
        if brand in text:
            return brand
    return ""


def guess_merchant(raw_text: str) -> str:
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    skip = re.compile(r"(E-?AR[SŞ]IV|FATURA|TARIH|SAAT|BELGE|ETTN|KDV|TOPLAM|TUTAR|KREDI|KARTI|HTTP|KASIYER)", re.I)
    preferred = re.compile(r"(A101|BIM|BİM|MIGROS|MİGROS|ŞOK|SOK|CARREFOUR|YENI MAGAZACILIK|YENİ MAĞAZACILIK|TIC|TİC|LTD|A[.\s]*Ş|A[.\s]*S)", re.I)
    for line in lines:
        if len(line) >= 3 and preferred.search(line):
            return line[:100]
    for line in lines:
        if len(line) >= 3 and not skip.search(line) and not re.search(r"\d{2}[./-]\d{2}[./-]\d{2,4}", line):
            return line[:100]
    return ""


def guess_payment_method(text: str) -> str:
    if "KREDI" in text or "KREDİ" in text or "KART" in text:
        return "kart"
    if "NAKIT" in text or "NAKİT" in text:
        return "nakit"
    return ""
