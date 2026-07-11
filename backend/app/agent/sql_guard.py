"""
SQL safety guard for generated queries. Uses a real parser instead of a
regex denylist so it can check column names, not just block keywords.

validate(sql, allowed_columns) -> (is_valid, error_message):
- parse with sqlglot (postgres dialect); parse failure = reject
- exactly one statement, and it must be a SELECT
- every table reference must be `orders` (CTEs/subqueries included)
- every column reference must be in the live introspected schema passed in
  by the caller (see db.get_orders_schema) — catches a hallucinated column,
  not just injection
"""

import sqlglot
from sqlglot import exp


def validate(sql: str, allowed_columns: set[str]) -> tuple[bool, str | None]:
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as e:
        return False, f"SQL failed to parse: {e}"

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return False, f"Expected exactly one statement, got {len(statements)}."

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        return False, f"Only SELECT statements are allowed, got {stmt.key.upper()}."

    # CTE names would let a query alias arbitrary relations past the table
    # check; the guard's scope is one fixed table, so reject them outright.
    if stmt.find(exp.With):
        return False, "CTEs (WITH ...) are not allowed; query the orders table directly."

    for table in stmt.find_all(exp.Table):
        if table.name.lower() != "orders":
            return False, f"Table '{table.name}' is not allowed; only 'orders' exists."

    allowed = {c.lower() for c in allowed_columns}
    for column in stmt.find_all(exp.Column):
        if column.name.lower() not in allowed:
            return (
                False,
                f"Column '{column.name}' does not exist in orders. "
                f"Available columns: {', '.join(sorted(allowed))}.",
            )

    return True, None
