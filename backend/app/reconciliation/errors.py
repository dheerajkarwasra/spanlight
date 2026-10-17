"""What the billing source service refuses, as exceptions the router turns into problems.

No message here ever quotes an admin key: the request rules name the field and say what is
wrong with it, never the value.
"""

import uuid

from app.reconciliation.csv_import import CsvError


class BillingSourceNotFound(Exception):  # noqa: N818 - reads as the outcome, like the crypto errors
    """No source with this id in the organization (or the organization is gone)."""


class NameTaken(Exception):  # noqa: N818
    """Another source of the organization already has this name."""


class ProjectTaken(Exception):  # noqa: N818
    """A project is already compared with another source of the same provider."""

    def __init__(self, other_source_name: str, project_ids: list[uuid.UUID]) -> None:
        super().__init__(f"already mapped to {other_source_name}")
        self.other_source_name = other_source_name
        self.project_ids = project_ids


class ProjectNotInOrg(Exception):  # noqa: N818
    """A project id that names no project of the organization."""

    def __init__(self, project_ids: list[uuid.UUID]) -> None:
        super().__init__("not projects of this organization")
        self.project_ids = project_ids


class NotApiKind(Exception):  # noqa: N818
    """A key operation on a CSV source, which holds no key."""


class InvalidSourceField(Exception):  # noqa: N818
    """A create or rotate body that breaks a rule a schema alone cannot check (the kind's
    provider, the admin key's shape). `message` names no value."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class NotCsvKind(Exception):  # noqa: N818
    """A CSV upload to a source that fetches from a provider."""


class SourceDisabled(Exception):  # noqa: N818
    """A manual refresh of a source that is turned off."""


class ProviderCallsOff(Exception):  # noqa: N818
    """A manual refresh while `BILLING_SOURCES_ENABLED` is off: no provider may be called."""


class CsvRejected(Exception):  # noqa: N818
    """An upload the CSV parser refused; `errors` are its reasons (at most 20), naming rows and
    columns, never a cell."""

    def __init__(self, errors: list[CsvError]) -> None:
        super().__init__(f"{len(errors)} errors in the uploaded file")
        self.errors = errors
