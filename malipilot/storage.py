from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("MALIYARDIMCI_DATA_DIR", "/tmp/maliyardimci-data" if os.environ.get("VERCEL") else str(ROOT / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "malipilot.sqlite3"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    EXPORT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists clients (
            id integer primary key autoincrement,
            name text not null,
            alias text,
            created_at text default current_timestamp
        );
        create table if not exists documents (
            id integer primary key autoincrement,
            client_id integer not null,
            period text not null,
            module text not null,
            original_name text not null,
            stored_path text not null,
            status text not null,
            warnings text default '[]',
            created_at text default current_timestamp
        );
        create table if not exists bank_transactions (
            id integer primary key autoincrement,
            document_id integer not null,
            client_id integer not null,
            period text not null,
            bank_name text,
            account_no_or_iban text,
            date text,
            description text,
            debit text,
            credit text,
            balance text,
            currency text,
            counterparty_guess text,
            transaction_hash text,
            duplicate_flag integer default 0,
            suggested_account_code text,
            confidence real,
            needs_review integer default 0,
            source_row integer,
            created_at text default current_timestamp
        );
        create table if not exists z_reports (
            id integer primary key autoincrement,
            document_id integer not null,
            client_id integer not null,
            period text not null,
            source_file text,
            report_date text,
            device_brand text,
            device_serial text,
            z_no text,
            gross_total text,
            vat_lines text,
            payment_breakdown text,
            confidence real,
            needs_review integer default 0,
            raw_text text,
            created_at text default current_timestamp
        );
        create table if not exists receipts (
            id integer primary key autoincrement,
            document_id integer not null,
            client_id integer not null,
            period text not null,
            source_file text,
            receipt_date text,
            merchant_name text,
            vkn_tckn text,
            document_no text,
            gross_total text,
            vat_total text,
            payment_method text,
            bookkeeping_status text,
            confidence real,
            needs_review integer default 0,
            raw_text text,
            created_at text default current_timestamp
        );
        create table if not exists account_code_rules (
            id integer primary key autoincrement,
            client_id integer,
            pattern text not null,
            account_code text not null,
            source text default 'manual',
            created_at text default current_timestamp
        );
        create table if not exists feedback (
            id integer primary key autoincrement,
            item_type text not null,
            item_id integer not null,
            rating text not null,
            note text,
            created_at text default current_timestamp
        );
        create table if not exists extraction_runs (
            id integer primary key autoincrement,
            document_id integer not null,
            provider text not null,
            model text,
            module text,
            file_name text,
            file_size integer,
            input_method text,
            status text not null,
            item_count integer default 0,
            duration_ms integer,
            raw_response text,
            error_message text,
            created_at text default current_timestamp
        );
        """
    )
    conn.commit()


def rows(conn: sqlite3.Connection, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, args).fetchall()]


def row(conn: sqlite3.Connection, query: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    result = conn.execute(query, args).fetchone()
    return dict(result) if result else None


def insert_document(conn: sqlite3.Connection, client_id: int, period: str, module: str, original_name: str, stored_path: str, status: str, warnings: list[str] | None = None) -> int:
    cur = conn.execute(
        "insert into documents (client_id, period, module, original_name, stored_path, status, warnings) values (?, ?, ?, ?, ?, ?, ?)",
        (client_id, period, module, original_name, stored_path, status, json.dumps(warnings or [], ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)


def account_rules(conn: sqlite3.Connection, client_id: int) -> list[dict[str, Any]]:
    return rows(
        conn,
        "select * from account_code_rules where client_id is null or client_id = ? order by id desc",
        (client_id,),
    )
