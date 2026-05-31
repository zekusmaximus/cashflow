from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .models import (
    DocumentMetadataEntry,
    ReconcileTransactionsRequest,
    ReconcileTransactionsResult,
    SqlQueryResult,
    UpsertTransactionOverrideRequest,
    UpsertTransactionOverrideResult,
)


OVERRIDABLE_TRANSACTION_FIELDS = (
    "merchant_normalized",
    "primary_category",
    "subcategory",
    "household_role",
    "lifecycle",
)


def normalize_override_description(description_raw: str) -> str:
    return " ".join(description_raw.split()).lower()


def transaction_override_match_key(
    account_id: str, occurred_on: str, amount: float, description_raw: str
) -> str:
    normalized_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    payload = (
        f"{account_id}|{occurred_on}|{normalized_amount}|"
        f"{normalize_override_description(description_raw)}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def merge_manual_override_metadata(metadata: dict[str, Any], note: str) -> dict[str, Any]:
    merged = dict(metadata)
    merged["manual_override_applied"] = True
    if note:
        merged["manual_override_note"] = note
    return merged


class DatabaseManager:
    def __init__(self, database_path: Path, schema_path: Path) -> None:
        self.database_path = database_path
        self.schema_path = schema_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = f"file:{self.database_path}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = 1")
            return connection

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
        if request.dry_run:
            return self._reconcile_dry_run(request)
        return self._reconcile_commit(request)

    def upsert_transaction_override(
        self, request: UpsertTransactionOverrideRequest
    ) -> UpsertTransactionOverrideResult:
        connection = self.connect()
        try:
            cursor = connection.cursor()
            transaction = cursor.execute(
                "SELECT id, account_id, occurred_on, amount, description_raw "
                "FROM transactions WHERE id = ?",
                (request.transaction_id,),
            ).fetchone()
            if transaction is None:
                raise ValueError(f"Transaction {request.transaction_id!r} not found.")

            match_key = transaction_override_match_key(
                transaction["account_id"],
                transaction["occurred_on"],
                float(transaction["amount"]),
                transaction["description_raw"],
            )

            cursor.execute(
                """
                INSERT INTO transaction_overrides (
                  id,
                  match_key,
                  account_id,
                  occurred_on,
                  amount,
                  description_raw,
                  merchant_normalized,
                  primary_category,
                  subcategory,
                  household_role,
                  lifecycle,
                  note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_key) DO UPDATE SET
                  merchant_normalized = COALESCE(excluded.merchant_normalized, transaction_overrides.merchant_normalized),
                  primary_category = COALESCE(excluded.primary_category, transaction_overrides.primary_category),
                  subcategory = COALESCE(excluded.subcategory, transaction_overrides.subcategory),
                  household_role = COALESCE(excluded.household_role, transaction_overrides.household_role),
                  lifecycle = COALESCE(excluded.lifecycle, transaction_overrides.lifecycle),
                  note = CASE
                    WHEN excluded.note <> '' THEN excluded.note
                    ELSE transaction_overrides.note
                  END,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(uuid4()),
                    match_key,
                    transaction["account_id"],
                    transaction["occurred_on"],
                    float(transaction["amount"]),
                    transaction["description_raw"],
                    request.merchant_normalized,
                    request.primary_category,
                    request.subcategory,
                    request.household_role,
                    request.lifecycle,
                    request.note,
                ),
            )

            override = self._find_transaction_override(cursor, match_key)
            if override is None:
                raise RuntimeError("Failed to read back stored transaction override.")

            updated_transactions = self._apply_override_to_existing_transactions(
                cursor,
                account_id=transaction["account_id"],
                occurred_on=transaction["occurred_on"],
                amount=float(transaction["amount"]),
                match_key=match_key,
                override=override,
            )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return UpsertTransactionOverrideResult(
            transaction_id=request.transaction_id,
            match_key=match_key,
            updated_transactions=updated_transactions,
            merchant_normalized=override["merchant_normalized"],
            primary_category=override["primary_category"],
            subcategory=override["subcategory"],
            household_role=override["household_role"],
            lifecycle=override["lifecycle"],
            note=override["note"],
        )

    def _reconcile_commit(self, request: ReconcileTransactionsRequest) -> ReconcileTransactionsResult:
        batch_id = str(uuid4())
        inserted = 0
        updated = 0
        account_keys = sorted({transaction.account.source_key for transaction in request.transactions})

        connection = self.connect()
        try:
            cursor = connection.cursor()
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
                transaction = self._apply_stored_transaction_override(cursor, transaction)
                self._upsert_account(cursor, transaction)

                amount = self._normalize_amount(transaction.amount, transaction.direction)
                transaction_id = self._find_existing_transaction_id(
                    cursor,
                    transaction.account.source_key,
                    transaction.source_record_key,
                )
                payload = self._transaction_payload(transaction, batch_id, amount)

                if transaction_id:
                    updated += 1
                    cursor.execute(self._update_transaction_sql(), (*payload, transaction_id))
                else:
                    inserted += 1
                    cursor.execute(self._insert_transaction_sql(), (str(uuid4()), *payload))

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return ReconcileTransactionsResult(
            source_name=request.source_name,
            parser_version=request.parser_version,
            transaction_count=len(request.transactions),
            inserted=inserted,
            updated=updated,
            would_insert=0,
            would_update=0,
            dry_run=False,
            account_keys=account_keys,
        )

    def _reconcile_dry_run(self, request: ReconcileTransactionsRequest) -> ReconcileTransactionsResult:
        would_insert = 0
        would_update = 0
        account_keys = sorted({transaction.account.source_key for transaction in request.transactions})

        connection = self.connect(read_only=True)
        try:
            cursor = connection.cursor()
            for transaction in request.transactions:
                transaction_id = self._find_existing_transaction_id(
                    cursor,
                    transaction.account.source_key,
                    transaction.source_record_key,
                )
                if transaction_id:
                    would_update += 1
                else:
                    would_insert += 1
        finally:
            connection.close()

        return ReconcileTransactionsResult(
            source_name=request.source_name,
            parser_version=request.parser_version,
            transaction_count=len(request.transactions),
            inserted=0,
            updated=0,
            would_insert=would_insert,
            would_update=would_update,
            dry_run=True,
            account_keys=account_keys,
        )

    @staticmethod
    def _upsert_account(cursor: sqlite3.Cursor, transaction: Any) -> None:
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

    @staticmethod
    def _transaction_payload(transaction: Any, batch_id: str, amount: float) -> tuple:
        return (
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

    @staticmethod
    def _update_transaction_sql() -> str:
        return """
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
            """

    @staticmethod
    def _insert_transaction_sql() -> str:
        return """
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
            """

    def query(self, sql: str, params: list[str | int | float]) -> SqlQueryResult:
        lowered = sql.lstrip().lower()
        if not (lowered.startswith("select") or lowered.startswith("with")):
            raise ValueError("Only read-only SELECT and WITH queries are allowed.")

        connection = self.connect(read_only=True)
        try:
            rows = [dict(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()

        return SqlQueryResult(row_count=len(rows), rows=rows)

    @staticmethod
    def _find_existing_transaction_id(
        cursor: sqlite3.Cursor,
        account_id: str,
        source_record_key: str,
    ) -> str | None:
        # Dedup is scoped to the ACCOUNT, not the source filename. The
        # parsers derive ``source_record_key`` from stable, file-independent
        # transaction content (date + amount + normalized description, plus a
        # deterministic occurrence ordinal), so the same transaction resolves
        # to the same key whether it arrives in a YTD export or a per-month
        # file. Matching on ``account_id`` therefore makes an overlapping
        # re-import idempotent: a transaction already loaded from one document
        # updates in place instead of inserting a duplicate from another.
        row = cursor.execute(
            "SELECT id FROM transactions WHERE account_id = ? AND source_record_key = ?",
            (account_id, source_record_key),
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

    def _apply_stored_transaction_override(
        self, cursor: sqlite3.Cursor, transaction: Any
    ) -> Any:
        match_key = transaction_override_match_key(
            transaction.account.source_key,
            transaction.occurred_on.isoformat(),
            self._normalize_amount(transaction.amount, transaction.direction),
            transaction.description_raw,
        )
        override = self._find_transaction_override(cursor, match_key)
        if override is None:
            return transaction

        updates: dict[str, Any] = {}
        for field in OVERRIDABLE_TRANSACTION_FIELDS:
            if override[field] is not None:
                updates[field] = override[field]
        updates["metadata"] = merge_manual_override_metadata(
            transaction.metadata, override["note"]
        )
        return transaction.model_copy(update=updates)

    @staticmethod
    def _find_transaction_override(
        cursor: sqlite3.Cursor, match_key: str
    ) -> sqlite3.Row | None:
        return cursor.execute(
            "SELECT match_key, merchant_normalized, primary_category, subcategory, "
            "household_role, lifecycle, note FROM transaction_overrides WHERE match_key = ?",
            (match_key,),
        ).fetchone()

    def _apply_override_to_existing_transactions(
        self,
        cursor: sqlite3.Cursor,
        *,
        account_id: str,
        occurred_on: str,
        amount: float,
        match_key: str,
        override: sqlite3.Row,
    ) -> int:
        candidate_rows = cursor.execute(
            "SELECT id, account_id, occurred_on, amount, description_raw, merchant_normalized, "
            "primary_category, subcategory, household_role, lifecycle, metadata_json "
            "FROM transactions WHERE account_id = ? AND occurred_on = ? AND amount = ?",
            (account_id, occurred_on, amount),
        ).fetchall()

        updated = 0
        for row in candidate_rows:
            if (
                transaction_override_match_key(
                    row["account_id"],
                    row["occurred_on"],
                    float(row["amount"]),
                    row["description_raw"],
                )
                != match_key
            ):
                continue

            metadata = self._parse_transaction_metadata(row["metadata_json"])
            merged_metadata = merge_manual_override_metadata(metadata, override["note"])
            cursor.execute(
                """
                UPDATE transactions
                SET merchant_normalized = ?,
                    primary_category = ?,
                    subcategory = ?,
                    household_role = ?,
                    lifecycle = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    override["merchant_normalized"]
                    if override["merchant_normalized"] is not None
                    else row["merchant_normalized"],
                    override["primary_category"]
                    if override["primary_category"] is not None
                    else row["primary_category"],
                    override["subcategory"]
                    if override["subcategory"] is not None
                    else row["subcategory"],
                    override["household_role"]
                    if override["household_role"] is not None
                    else row["household_role"],
                    override["lifecycle"]
                    if override["lifecycle"] is not None
                    else row["lifecycle"],
                    json.dumps(merged_metadata),
                    row["id"],
                ),
            )
            updated += 1

        return updated

    @staticmethod
    def _parse_transaction_metadata(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
