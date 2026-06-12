from __future__ import annotations

import html
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring


def write_workbook(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types(len(sheets)))
        zf.writestr("_rels/.rels", package_rels())
        zf.writestr("xl/workbook.xml", workbook_xml(list(sheets)))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels(len(sheets)))
        for idx, rows in enumerate(sheets.values(), start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", sheet_xml(rows))


def content_types(count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{overrides}</Types>"
    )


def package_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def workbook_rels(count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def workbook_xml(names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{html.escape(name[:31])}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def sheet_xml(rows: list[dict[str, Any]]) -> str:
    headers = sorted({key for row in rows for key in row.keys()}) if rows else ["empty"]
    root = Element("worksheet", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main")
    data = SubElement(root, "sheetData")
    write_row(data, 1, headers)
    for row_idx, row in enumerate(rows, start=2):
        write_row(data, row_idx, [row.get(header, "") for header in headers])
    return b'<?xml version="1.0" encoding="UTF-8"?>' + tostring(root, encoding="utf-8")


def write_row(parent: Element, index: int, values: list[Any]) -> None:
    row = SubElement(parent, "row", r=str(index))
    for col_idx, value in enumerate(values, start=1):
        cell = SubElement(row, "c", r=f"{column_name(col_idx)}{index}", t="inlineStr")
        inline = SubElement(cell, "is")
        text = SubElement(inline, "t")
        text.text = "" if value is None else str(value)


def column_name(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def export_filename(client_name: str, period: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{client_name}_{period}")
    return f"MaliYardimci_Cikti_{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
