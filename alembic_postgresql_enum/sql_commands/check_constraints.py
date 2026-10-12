from typing import TYPE_CHECKING, List, NamedTuple, Tuple

import sqlalchemy

from alembic_postgresql_enum.sql_commands.indexes import transform_index_definition_for_renamed_values

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


class TableCheckConstraint(NamedTuple):
    """A CHECK constraint whose expression uses the enum type and has to be recreated around its change."""

    table: str
    """Quoted, schema-qualified table name."""
    name: str
    definition: str
    """As pg_get_constraintdef prints it: CHECK (...) [NOT VALID]."""


def get_dependent_check_constraints(
    connection: "Connection", enum_schema: str, enum_name: str
) -> List[TableCheckConstraint]:
    """
    CHECK constraints that depend on the enum type: a literal or a cast of this type in the expression.

    While the columns are moved to the new type, such a constraint is bound to the temporary comparison
    operator between the new and the old type, and dropping that operator fails (issue #134). The constraint
    is dropped beforehand and created again from its definition, which then refers to the new type.
    """
    result = connection.execute(
        sqlalchemy.text(
            """
            SELECT DISTINCT
                format('%I.%I', table_ns.nspname, table_class.relname) AS table_name,
                con.conname AS constraint_name,
                pg_get_constraintdef(con.oid) AS constraint_def
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class table_class ON table_class.oid = con.conrelid
            JOIN pg_catalog.pg_namespace table_ns ON table_ns.oid = table_class.relnamespace
            JOIN pg_catalog.pg_depend dep
                ON dep.classid = 'pg_constraint'::regclass AND dep.objid = con.oid
                AND dep.refclassid = 'pg_type'::regclass
            JOIN pg_catalog.pg_type enum_type ON enum_type.oid = dep.refobjid
            JOIN pg_catalog.pg_namespace enum_ns ON enum_ns.oid = enum_type.typnamespace
            WHERE con.contype = 'c' AND enum_ns.nspname = :schema AND enum_type.typname = :name
            ORDER BY table_name, constraint_name
            """
        ),
        {"schema": enum_schema, "name": enum_name},
    )
    return [
        TableCheckConstraint(table=row.table_name, name=row.constraint_name, definition=row.constraint_def)
        for row in result
    ]


def drop_check_constraints(connection: "Connection", constraints: List[TableCheckConstraint]):
    for constraint in constraints:
        connection.execute(sqlalchemy.text(f'ALTER TABLE {constraint.table} DROP CONSTRAINT "{constraint.name}"'))


def recreate_check_constraints(connection: "Connection", constraints: List[TableCheckConstraint]):
    for constraint in constraints:
        connection.execute(
            sqlalchemy.text(
                f'ALTER TABLE {constraint.table} ADD CONSTRAINT "{constraint.name}" {constraint.definition}'
            )
        )


def transform_check_constraints_for_renamed_values(
    constraints: List[TableCheckConstraint],
    enum_name: str,
    enum_values_to_rename: List[Tuple[str, str]],
    enum_schema: str,
) -> List[TableCheckConstraint]:
    """The definitions with renamed enum values: 'old'::enum becomes 'new'::enum."""
    return [
        constraint._replace(
            definition=transform_index_definition_for_renamed_values(
                constraint.definition, enum_name, enum_values_to_rename, enum_schema
            )
        )
        for constraint in constraints
    ]
