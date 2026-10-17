"""What the budget service raises. The API maps each to a problem response
(`api/v1/budgets.py`). A channel outside the organization raises the rules' own
`app.alerts.rule_errors.UnknownChannelError`."""


class BudgetNotFoundError(Exception):
    """No budget with this id in the project."""


class BudgetLimitError(Exception):
    """The project already has the most budgets it may have."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"a project can have at most {limit} budgets")
        self.limit = limit


class BudgetNameTakenError(Exception):
    """Another budget of the project has this name."""


class UnknownScopeError(Exception):
    """The budget's `scope_id` names nothing its scope can measure: a `gateway_key` budget
    names something that is not a gateway key of the project, a `metadata` budget is not
    `key=value` with a valid key and a value of 1 to 256 characters or names a value the ingest
    redactor rewrites (a stored trace never carries it), or a `trace_name` budget is blank once
    trimmed. The message is shown to the caller as the field's error."""

    GATEWAY_KEY = "Not a gateway key of this project"
    METADATA = "A metadata budget's scope is `key=value` with a valid key"
    REDACTED = "This value is redacted at ingest, so a budget on it can never match"
    TRACE_NAME = "A trace name budget's scope is the trace's name"

    def __init__(self, message: str = GATEWAY_KEY) -> None:
        super().__init__(message)
        self.message = message
