from __future__ import annotations

import sqlite3

import pytest

from liquidity_gate_mcp.database import DatabaseManager


def insert_row(database: DatabaseManager) -> None:
    connection = database.connect()
    try:
        connection.execute(
            """
            INSERT INTO accounts (id, institution, account_name, account_type, owner, currency)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("acct-1", "Chase", "Checking", "checking", "joint", "USD"),
        )
        connection.commit()
    finally:
        connection.close()


def count_accounts(database: DatabaseManager) -> int:
    connection = database.connect()
    try:
        row = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()
        return int(row[0])
    finally:
        connection.close()


def test_query_rejects_non_select_prefix(database: DatabaseManager) -> None:
    insert_row(database)
    with pytest.raises(ValueError):
        database.query("DELETE FROM accounts", [])


def test_query_blocks_direct_delete_via_read_only_connection(database: DatabaseManager) -> None:
    """Even if the prefix check were bypassed, the read-only connection would refuse."""
    insert_row(database)
    # Bypass the prefix check by calling connect(read_only=True) directly.
    connection = database.connect(read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM accounts")
    finally:
        connection.close()
    assert count_accounts(database) == 1


def test_query_blocks_with_cte_delete(database: DatabaseManager) -> None:
    """CTEs that try to mutate (the original bypass) must be blocked.

    The connection is opened in read-only mode, so even a `WITH ... DELETE`
    statement that passes the prefix check fails at execute time.
    """
    insert_row(database)
    sql = (
        "WITH targets AS (SELECT id FROM accounts) "
        "DELETE FROM accounts WHERE id IN (SELECT id FROM targets)"
    )
    with pytest.raises(sqlite3.OperationalError):
        database.query(sql, [])
    assert count_accounts(database) == 1


def test_query_blocks_with_cte_update(database: DatabaseManager) -> None:
    insert_row(database)
    sql = (
        "WITH targets AS (SELECT id FROM accounts) "
        "UPDATE accounts SET institution = 'Hijacked' WHERE id IN (SELECT id FROM targets)"
    )
    with pytest.raises(sqlite3.OperationalError):
        database.query(sql, [])
    connection = database.connect()
    try:
        row = connection.execute("SELECT institution FROM accounts WHERE id = 'acct-1'").fetchone()
    finally:
        connection.close()
    assert row[0] == "Chase"


def test_query_blocks_with_cte_insert(database: DatabaseManager) -> None:
    insert_row(database)
    sql = (
        "WITH new_row AS (SELECT 'acct-2' AS id) "
        "INSERT INTO accounts (id, institution, account_name, account_type, owner, currency) "
        "SELECT id, 'X', 'X', 'checking', 'joint', 'USD' FROM new_row"
    )
    with pytest.raises(sqlite3.OperationalError):
        database.query(sql, [])
    assert count_accounts(database) == 1


def test_query_allows_legitimate_select(database: DatabaseManager) -> None:
    insert_row(database)
    result = database.query("SELECT id, institution FROM accounts", [])
    assert result.row_count == 1
    assert result.rows[0]["id"] == "acct-1"


def test_query_allows_with_select(database: DatabaseManager) -> None:
    insert_row(database)
    result = database.query(
        "WITH a AS (SELECT id FROM accounts) SELECT COUNT(*) AS n FROM a", []
    )
    assert result.row_count == 1
    assert result.rows[0]["n"] == 1
