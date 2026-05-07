from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .models import DocumentMetadataEntry, ReconcileTransactionsRequest, ReconcileTransactionsResult, SqlQueryResult


class DatabaseManager:
    def __init__(self, database_path: Path, schema_path: Path) -> None:
        self.database_path = database_path
        self.schema_path = schema_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        schema = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)

    def upsert_document_metadata(self, items: list[DocumentMetadataEntry]) -> None:
        now = datetime.now(timezone.utc).isoformat()

        with self.connect() as connection:
            for item in items:
                top_match = item.matched_files[0] if item.matched_files else None
                connection.execute(
                    """
                    INSERT INTO documents (
                      id,
                      category,
                      document_name,
                      subject_matter,
                      preferred_format,
                      priority,
                      source_hint,
                      why_needed,
                      obtained,
                      local_path,
                      discovered_at
                    ) VALUES (?, ?, ?, '', '', ?, '', '', ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      category = excluded.category,
                      document_name = excluded.document_name,
                      priority = excluded.priority,
                      obtained = excluded.obtained,
                      local_path = excluded.local_path,
                      discovered_at = excluded.discovered_at
                    """,
                    (
                        item.id,
                        item.category,
                        item.document,
                        item.priority,
                        1 if item.status == "obtained" else 0,
                        top_match.relative_path if top_match else None,
                        now,
                    ),
                )

    def reconcile_transactions(self, request: ReconcileTransactionsRequest) -> ReconcileTransactionsResult:
        batch_id = str(uuid4())
        inserted = 0
        updated = 0
        account_keys = sorted({transaction.account.source_key for transaction in request.transactions})

        with self.connect() as connection:
            cursor = connection.cursor()

            if not request.dry_run:
                cursor.execute(
                    "INSERT INTO import_batches (id, source_name, parser_version, imported_at, raw_payload) VALUES (?, ?, ?, ?, ?)",
                    (
                        batch_id,
                        request.source_name,
                        request.parser_version,
                        datetime.now(timezone.utc).isoformat(),
                        request.model_dump_json(),
                    ),
                )

            for transaction in request.transactions:
                cursor.execute(
                    """
                    INSERT INTO accounts (id, institution, account_name, account_type, owner, currency)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      institution = excluded.institution,
                      account_name = excluded.account_name,
                      account_type = excluded.account_type,
                      owner = excluded.owner,
                      currency = excluded.currency
                    """,
                    (
                        transaction.account.source_key,
                        transaction.account.institution,
                        transaction.account.account_name,
                        transaction.account.account_type,
                        transaction.account.owner,
                        transaction.account.currency,
                    ),
                )

                amount = self._normalize_amount(transaction.amount, transaction.direction)
                transaction_id = self._find_existing_transaction_id(
                    cursor,
                    transaction.source_document_name,
                    transaction.source_record_key,
                )

                payload = (
                    transaction.account.source_key,
                    batch_id,
                    transaction.source_record_key,
                    transaction.source_document_name,
                    transaction.occurred_on.isoformat(),
                    transaction.posted_on.isoformat() if transaction.posted_on else None,
                    transaction.description_raw,
                    transaction.merchant_normalized,
                    amount,
                    transaction.direction,
                    transaction.account.currency,
                    transaction.primary_category,
                    transaction.subcategory,
                    transaction.household_role,
                    transaction.lifecycle,
                    transaction.transfer_group_key,
                    transaction.statement_period,
                    json.dumps(transaction.metadata),
                )

                if transaction_id:
                    updated += 1
                    if not request.dry_run:
                        cursor.execute(
                            """
                            UPDATE transactions
                            SET account_id = ?,
                                import_batch_id = ?,
                                source_record_key = ?,
                                source_document_name = ?,
                                occurred_on = ?,
                                posted_on = ?,
                                description_raw = ?,
                                merchant_normalized = ?,
                                amount = ?,
                                direction = ?,
                                currency = ?,
                                primary_category = ?,
                                subcategory = ?,
                                household_role = ?,
                                lifecycle = ?,
                                transfer_group_key = ?,
                                statement_period = ?,
                                metadata_json = ?
                            WHERE id = ?
                            """,
                            (*payload, transaction_id),
                        )
                else:
                    inserted += 1
                    if not request.dry_run:
                        cursor.execute(
                            """
                            INSERT INTO transactions (
                              id,
                              account_id,
                              import_batch_id,
                              source_record_key,
                              source_document_name,
                              occurred_on,
                              posted_on,
                              description_raw,
                              merchant_normalized,
                              amount,
                              direction,
                              currency,
                              primary_category,
                              subcategory,
                              household_role,
                              lifecycle,
                              transfer_group_key,
                              statement_period,
                              metadata_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (str(uuid4()), *payload),
                        )

            if request.dry_run:
                connection.rollback()
            else:
                connection.commit()

        return ReconcileTransactionsResult(
            source_name=request.source_name,
            parser_version=request.parser_version,
            transaction_count=len(request.transactions),
            inserted=inserted,
            updated=updated,
            dry_run=request.dry_run,
            account_keys=account_keys,
        )

    def query(self, sql: str, params: list[str | int | float]) -> SqlQueryResult:
        lowered = sql.lstrip().lower()
        if not (lowered.startswith("select") or lowered.startswith("with")):
            raise ValueError("Only read-only SELECT and WITH queries are allowed.")

        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, params).fetchall()]

        return SqlQueryResult(row_count=len(rows), rows=rows)

    @staticmethod
    def _find_existing_transaction_id(
        cursor: sqlite3.Cursor,
        source_document_name: str,
        source_record_key: str,
    ) -> str | None:
        row = cursor.execute(
            "SELECT id FROM transactions WHERE source_document_name = ? AND source_record_key = ?",
            (source_document_name, source_record_key),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _normalize_amount(amount: Decimal, direction: str) -> float:
        numeric = float(amount)
        if direction == "outflow":
            return -abs(numeric)
        if direction == "inflow":
            return abs(numeric)
        return numeric
