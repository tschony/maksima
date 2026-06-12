from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


DATE_KEYS = {"tarih", "islemtarihi", "islemtarih", "date", "valor", "valortarihi"}
DESC_KEYS = {"aciklama", "islem", "description", "detay", "details", "hareket", "islemaciklamasi"}
DEBIT_KEYS = {"borc", "borctl", "cikan", "cikis", "odeme", "debit", "gider", "tutarborc"}
CREDIT_KEYS = {"alacak", "alacaktl", "gelen", "giris", "tahsilat", "credit", "tutaralacak"}
AMOUNT_KEYS = {"tutar", "miktar", "amount", "islemtutari", "harekettutari"}
BALANCE_KEYS = {"bakiye", "balance", "kalan"}
CURRENCY_KEYS = {"parabirimi", "doviz", "currency"}
ACCOUNT_KEYS = {"iban", "hesapno", "hesapnumarasi", "account", "accountno"}


@dataclass
class BankParseResult:
    rows: list[dict[str, Any]]
    mapping: dict[str, str | None]
    warnings: list[str]


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text)


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("TL", "").replace("TRY", "").replace("\u00a0", " ").strip()
    text = re.sub(r"[^0-9,\.\-+]", "", text)
    if not text or text in {"-", "+", ",", "."}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    formats = [
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d.%m.%y",
        "%d/%m/%y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        try:
            return datetime(int(year), int(month), int(day)).date().isoformat()
        except ValueError:
            return None
    return None


def transaction_hash(row: dict[str, Any]) -> str:
    key = "|".join(
        str(row.get(part) or "")
        for part in ["date", "description", "debit", "credit", "balance", "account_no_or_iban"]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def guess_counterparty(description: str) -> str:
    text = re.sub(r"\s+", " ", description or "").strip()
    text = re.sub(r"\b(EFT|FAST|HAVALE|POS|KART|ODEME|ÖDEME|TAHSILAT|TAHSİLAT)\b", "", text, flags=re.I)
    text = re.sub(r"\b(TR\d{2}[0-9A-Z]{20,})\b", "", text)
    return text.strip(" -:/")[:80]


def parse_bank_file(path: Path, client_id: int, period: str, bank_name: str, rules: list[dict[str, Any]]) -> BankParseResult:
    raw_rows = read_table(path)
    warnings: list[str] = []
    if not raw_rows:
        return BankParseResult([], {}, ["Dosyada satır bulunamadı"])

    header_map = detect_columns(raw_rows[0].keys())
    missing = [name for name in ["date", "description"] if not header_map.get(name)]
    if not header_map.get("debit") and not header_map.get("credit") and not header_map.get("amount"):
        missing.append("amount/debit/credit")
    if missing:
        labels = {"date": "tarih", "description": "açıklama", "amount/debit/credit": "tutar/borç/alacak"}
        warnings.append("Eksik veya belirsiz sütunlar: " + ", ".join(labels.get(item, item) for item in missing))

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, source in enumerate(raw_rows, start=1):
        date = parse_date(source.get(header_map.get("date") or ""))
        description = str(source.get(header_map.get("description") or "") or "").strip()
        debit = parse_decimal(source.get(header_map.get("debit") or ""))
        credit = parse_decimal(source.get(header_map.get("credit") or ""))
        amount = parse_decimal(source.get(header_map.get("amount") or ""))
        if amount is not None and debit is None and credit is None:
            if amount < 0:
                debit = abs(amount)
                credit = Decimal("0")
            else:
                credit = amount
                debit = Decimal("0")
        balance = parse_decimal(source.get(header_map.get("balance") or ""))
        account = str(source.get(header_map.get("account") or "") or "").strip()
        currency = str(source.get(header_map.get("currency") or "") or "TRY").strip() or "TRY"
        counterparty = guess_counterparty(description)
        row = {
            "client_id": client_id,
            "period": date[:7] if date and len(date) >= 7 else period,
            "bank_name": bank_name or "",
            "account_no_or_iban": account,
            "date": date,
            "description": description,
            "debit": decimal_to_text(debit),
            "credit": decimal_to_text(credit),
            "balance": decimal_to_text(balance),
            "currency": currency,
            "counterparty_guess": counterparty,
            "source_row": index,
        }
        row["transaction_hash"] = transaction_hash(row)
        row["duplicate_flag"] = row["transaction_hash"] in seen
        seen.add(row["transaction_hash"])
        suggestion, confidence = suggest_account_code(description, counterparty, account, rules)
        row["suggested_account_code"] = suggestion
        row["confidence"] = confidence
        row["needs_review"] = bool(not date or not description or confidence < 0.7 or row["duplicate_flag"])
        normalized.append(row)
    return BankParseResult(normalized, header_map, warnings)


def decimal_to_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.quantize(Decimal("0.01")), "f")


def suggest_account_code(description: str, counterparty: str, account: str, rules: list[dict[str, Any]]) -> tuple[str, float]:
    haystacks = [normalize_key(description), normalize_key(counterparty), normalize_key(account)]
    for rule in rules:
        pattern = normalize_key(rule.get("pattern", ""))
        if pattern and any(pattern in item for item in haystacks):
            return str(rule.get("account_code") or ""), 0.9
    text = normalize_key(description)
    defaults = [
        ("maas", "770", 0.55),
        ("sgk", "361", 0.55),
        ("vergi", "360", 0.55),
        ("kira", "770", 0.5),
        ("pos", "108", 0.45),
        ("komisyon", "780", 0.45),
    ]
    for keyword, code, confidence in defaults:
        if keyword in text:
            return code, confidence
    return "", 0.2


def detect_columns(headers: Any) -> dict[str, str | None]:
    mapping: dict[str, str | None] = {
        "date": None,
        "description": None,
        "debit": None,
        "credit": None,
        "amount": None,
        "balance": None,
        "currency": None,
        "account": None,
    }
    for header in headers:
        key = normalize_key(header)
        if key in DATE_KEYS:
            mapping["date"] = header
        elif key in DESC_KEYS:
            mapping["description"] = header
        elif key in DEBIT_KEYS:
            mapping["debit"] = header
        elif key in CREDIT_KEYS:
            mapping["credit"] = header
        elif key in AMOUNT_KEYS:
            mapping["amount"] = header
        elif key in BALANCE_KEYS:
            mapping["balance"] = header
        elif key in CURRENCY_KEYS:
            mapping["currency"] = header
        elif key in ACCOUNT_KEYS:
            mapping["account"] = header
    return mapping


def read_table(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return read_xlsx(path)
    return read_csv_like(path)


def read_csv_like(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    for encoding in ["utf-8-sig", "utf-8", "cp1254", "iso-8859-9", "latin-1"]:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [dict(row) for row in reader if any((cell or "").strip() for cell in row.values())]


def read_xlsx(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as zf:
        shared = read_shared_strings(zf)
        sheet_name = first_sheet_name(zf)
        root = ET.fromstring(zf.read(sheet_name))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[Any]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values: list[Any] = []
        current_col = 0
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            col_idx = column_index(ref)
            while current_col < col_idx:
                values.append("")
                current_col += 1
            values.append(read_cell_value(cell, shared, ns))
            current_col += 1
        if any(str(v).strip() for v in values):
            rows.append(values)
    if not rows:
        return []
    headers = [str(h or "").strip() or f"Column{idx+1}" for idx, h in enumerate(rows[0])]
    result = []
    for row in rows[1:]:
        result.append({headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))})
    return result


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings = []
    for item in root.findall("x:si", ns):
        strings.append("".join(t.text or "" for t in item.findall(".//x:t", ns)))
    return strings


def first_sheet_name(zf: zipfile.ZipFile) -> str:
    names = [name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
    if not names:
        raise ValueError("No worksheet found")
    return sorted(names)[0]


def column_index(ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", ref.upper())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(index - 1, 0)


def read_cell_value(cell: ET.Element, shared: list[str], ns: dict[str, str]) -> Any:
    value = cell.find("x:v", ns)
    if value is None or value.text is None:
        inline = cell.find(".//x:t", ns)
        return inline.text if inline is not None else ""
    text = value.text
    if cell.attrib.get("t") == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return ""
    return text
