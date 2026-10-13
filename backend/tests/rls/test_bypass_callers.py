"""Only worker, job and CLI code may set the row-level security bypass flag.

`app.db.rls.bypass_rls` makes every project's rows visible for the rest of a transaction, so a
request handler that set it would lose the tenancy check the database gives every other request.
This test reads the application's source:

* every module that sets the flag (calls `bypass_rls`, or holds a string naming it other than a
  docstring, such as a `set_config` or `SET LOCAL` statement) must be on the allow-list below,
  a list of modules that only the worker, its jobs and the CLI import;
* no module on that list may be imported, directly or through other modules, from the API
  (`app/main.py` and `app/api/`). Imports inside functions count too.

So a new caller fails here until it lives in a job-only module, and a request path that starts
importing such a module fails too. A request that needs a figure across projects uses a narrow
database function instead (for example `explain_month_to_date_cost`, migration 0507).
"""

import ast
from collections.abc import Iterator
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
FLAG = "bypass_rls"
HELPER = "app.db.rls"  # defines `bypass_rls`; the request path imports it for `bind_project`

ALLOWED = {
    HELPER,
    "app.alerts.cli_support",  # the dry-run CLI clears the flag at the start of each session
    "app.alerts.digest_queries",
    "app.alerts.job_queries",
    "app.end_users.job_queries",
    "app.evals.jobs",
    "app.exports.expiry",
    "app.gateway.jobs",
    "app.insights.context_queries",  # the detectors' context, built by the run_detectors job
    "app.insights.job_queries",
    "app.jobs.tasks.retention",
    "app.rollups.jobs",
    "app.scores.repair",
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(APP.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


MODULES = {_module_name(path): path for path in sorted(APP.rglob("*.py"))}
TREES = {name: ast.parse(path.read_text(encoding="utf-8")) for name, path in MODULES.items()}


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of the docstring nodes of the module, its classes and its functions."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _sets_flag(tree: ast.Module) -> bool:
    """Whether the module refers to the setter at all: imports `bypass_rls` (under any name),
    names it (`bypass_rls(...)`, `rls.bypass_rls`), or holds a non-docstring string naming the
    flag (a `set_config` or `SET LOCAL` statement)."""
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == FLAG for a in node.names):
            return True
        if (isinstance(node, ast.Name) and node.id == FLAG) or (
            isinstance(node, ast.Attribute) and node.attr == FLAG
        ):
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and FLAG in node.value
            and id(node) not in docstrings
        ):
            return True
    return False


def _with_packages(name: str) -> Iterator[str]:
    """`name` and its parent packages: importing `a.b.c` runs `a/__init__.py` and
    `a/b/__init__.py` first, and a package's `__init__` may import more (`app.jobs.tasks` loads
    every job module)."""
    parts = name.split(".")
    for end in range(1, len(parts) + 1):
        candidate = ".".join(parts[:end])
        if candidate in MODULES:
            yield candidate


def _direct_imports(name: str) -> Iterator[str]:
    """The application modules `name` imports by name, at the top or inside a function."""
    package = name if MODULES[name].name == "__init__.py" else name.rpartition(".")[0]
    for node in ast.walk(TREES[name]):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names if alias.name in MODULES)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            if base in MODULES:
                yield base
            yield from (f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" in MODULES)


def _imports(name: str) -> Iterator[str]:
    """Every application module that importing `name` loads directly, packages included."""
    for imported in _direct_imports(name):
        yield from _with_packages(imported)


def _api_import_chains() -> dict[str, list[str]]:
    """For each module the API reaches, one import chain from an API module down to it."""
    roots = [name for name in MODULES if name == "app.main" or name.startswith("app.api")]
    roots = list(dict.fromkeys(package for root in roots for package in _with_packages(root)))
    parent: dict[str, str | None] = dict.fromkeys(roots)
    stack = list(roots)
    while stack:
        current = stack.pop()
        for imported in _imports(current):
            if imported not in parent:
                parent[imported] = current
                stack.append(imported)
    chains: dict[str, list[str]] = {}
    for name, first in parent.items():
        chain, step = [name], first
        while step is not None:
            chain.append(step)
            step = parent[step]
        chains[name] = chain[::-1]
    return chains


def test_only_job_only_modules_set_the_bypass_flag() -> None:
    setters = {name for name, tree in TREES.items() if _sets_flag(tree)}

    # Explain runs on a request: its organization-wide sum is a database function instead.
    assert {name for name in setters if name.startswith("app.insights.explain")} == set()
    assert setters == ALLOWED


def test_the_api_never_imports_a_module_that_sets_the_bypass_flag() -> None:
    chains = _api_import_chains()

    reached = {name: " -> ".join(chains[name]) for name in ALLOWED - {HELPER} if name in chains}

    assert reached == {}
    assert HELPER in chains  # the walk does reach the API's real imports
