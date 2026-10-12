"""How a name that may contain `/`, `.` or `..` travels in a URL path segment."""

PATH_NAME_ESCAPE = "~"


def unescape_path_name(wire_name: str) -> str:
    """The stored name a path segment names: the segment without one leading `~`.

    Browsers resolve `.` and `..` path segments (even percent-encoded) before sending, so the
    dashboard prefixes `~` to a name that is `.` or `..` or that starts with `~`; stripping
    exactly one `~` here maps every wire name back to exactly one stored name.
    """
    return wire_name.removeprefix(PATH_NAME_ESCAPE)
