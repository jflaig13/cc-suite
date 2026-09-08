# SPDX-License-Identifier: MPL-2.0
"""Install the exported schema into an explicitly supplied empty database."""
from pathlib import Path
import re
from .configuration import company_id, scribe_deployment_id, deployment_binding

DDL_ROOT = Path(__file__).resolve().parent / 'ddl'

def rendered_schema(path: Path) -> str:
    identity = {'COMPANY_ID': company_id(), 'SCRIBE_DEPLOYMENT_ID': scribe_deployment_id()}
    def substitute(match):
        name = match.group(1)
        value = identity.get(name) or deployment_binding(name)
        # Unconfigured authority is an explicit non-matching sentinel. Schema
        # installation therefore grants no authority to launch any worker.
        value = value or ('UNCONFIGURED_' + name)
        if not re.fullmatch(r'[A-Za-z0-9._:/ -]+', value):
            raise ValueError(f'Unsupported characters in schema deployment binding: {name}')
        return value
    return re.sub(r'__CC_SUITE_([A-Z0-9_]+)__', substitute, path.read_text())

def install_schema(conn) -> None:
    """Caller owns the dedicated database. This never selects a database URL."""
    from psycopg.rows import tuple_row
    with conn.transaction():
        with conn.cursor(row_factory=tuple_row) as cursor:
            if cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public')").fetchone()[0]:
                raise ValueError('Schema installer requires an empty dedicated database')
            for path in sorted(DDL_ROOT.glob('*.sql')):
                try:
                    cursor.execute(rendered_schema(path))
                except Exception as exc:
                    raise RuntimeError(f'Failed schema file: {path.name}') from exc
