"""Database URL to libpq settings, for the `pg_dump`, `pg_restore` and `psql` children.

The database URL carries a password, so it never reaches a command line (visible in `ps` and in
process-accounting logs) or a log line: it is parsed here and handed to the child process as
libpq environment variables (`PGHOST`, `PGPASSWORD`, ...). Error messages name the offending
parameter, never its value.
"""

import os
from collections.abc import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# URL query parameters (and the URL parts) that map to a libpq setting, and its variable.
_LIBPQ_ENVIRONMENT = {
    "host": "PGHOST",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
}
# The child gets these from our environment and nothing else: no S3 keys, no SECRET_KEY, and no
# stray PG* variable that would override the URL.
_INHERITED_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE")


def connection_params(url: str) -> dict[str, str]:
    """Split a `postgresql[+driver]://` URL into libpq settings.

    Raises ValueError with a message that never quotes `url`.
    """
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        # A malformed port raises a ValueError that quotes it, and without a host the password
        # lands there.
        raise ValueError("the database URL is not a valid URL") from None
    if not parsed.drivername.startswith("postgres"):
        raise ValueError("the database URL must start with postgresql://")

    params = {
        "host": parsed.host,
        "port": None if parsed.port is None else str(parsed.port),
        "user": parsed.username,
        "password": parsed.password,
        "dbname": parsed.database,
    }
    for name, value in parsed.query.items():
        if name not in _LIBPQ_ENVIRONMENT or not isinstance(value, str):
            raise ValueError(f"the database URL has an unsupported parameter {name!r}")
        params[name] = value
    return {name: value for name, value in params.items() if value is not None}


def process_environment(params: Mapping[str, str]) -> dict[str, str]:
    environment = {name: os.environ[name] for name in _INHERITED_ENVIRONMENT if name in os.environ}
    environment.update({_LIBPQ_ENVIRONMENT[name]: value for name, value in params.items()})
    return environment
