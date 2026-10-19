"""The `/api/v1` routes an API key may call, written out by hand.

Adding to this set is the decision to let keys into a route: it belongs in a review, not in a
refactor. Two tests hold it to the code: the key route inventory (`tests/keys/`) shows keys get
through exactly here, and the authorization matrix completeness test (`tests/authz/`) shows the
routes that pass `allow_api_key=True` to `require()` are exactly these.
"""

Route = tuple[str, str]  # (METHOD, "/api/v1/..." exactly as the router declares it)

# Only with `traces:read`, and only for the key's own project.
KEY_READABLE: frozenset[Route] = frozenset(
    {
        ("GET", "/api/v1/projects/{project_id}/traces"),
        ("GET", "/api/v1/projects/{project_id}/traces/{trace_id}"),
        # Phase 2.5: two traces step by step, as readable as each trace on its own.
        ("GET", "/api/v1/projects/{project_id}/traces/compare"),
        # Bet 1: the lines behind one span's cost, as readable as the trace that holds it.
        ("GET", "/api/v1/projects/{project_id}/traces/{trace_id}/spans/{span_id}/cost-explanation"),
        ("GET", "/api/v1/projects/{project_id}/sessions"),
        ("GET", "/api/v1/projects/{project_id}/filters"),
        ("GET", "/api/v1/projects/{project_id}/metrics/overview"),
        ("GET", "/api/v1/projects/{project_id}/metrics/timeseries"),
        ("GET", "/api/v1/projects/{project_id}/metrics/models"),
        # The Phase 1 spec lists it with the metrics routes (docs/api-deviations.md, "Prices").
        ("GET", "/api/v1/projects/{project_id}/unpriced-models"),
        # Phase 4: releases and end users, for a CI job that checks a release after a deploy
        # (docs/api-deviations.md, "Releases" and "Users").
        ("GET", "/api/v1/projects/{project_id}/releases"),
        ("GET", "/api/v1/projects/{project_id}/releases/compare"),
        # Bet 5: failure-category rates per release (docs/api-deviations.md, "Failure-category
        # rates").
        ("GET", "/api/v1/projects/{project_id}/releases/failure-categories"),
        ("GET", "/api/v1/projects/{project_id}/users"),
        ("GET", "/api/v1/projects/{project_id}/users/{external_user_id:path}"),
        # Bet 3: spend by metadata, trace name, user or release, for the same CI job.
        ("GET", "/api/v1/projects/{project_id}/attribution"),
    }
)
