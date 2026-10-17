"""`runner.py --setup`: the projects, ingest keys, routes, gateway keys and fault profiles every
scenario needs, created through the dashboard API. Idempotent: a rerun creates what is missing
and corrects what drifted (a key's environment, route, rate limit or fault profile)."""

from typing import Any

from accuracy.dashboard import Api, SetupError, key_is_listed
from accuracy.doctor.common import INCIDENT_ORG_NAME, OrgScopedState, doctor_state, main_state
from accuracy.doctor.scenario_types import Fault, Scenario
from accuracy.doctor.scenarios import RUN_SUFFIX
from accuracy.setup import State, ensure_project, session

ROUTE_NAME = "doctor"
KEY_NAME = "doctor"


def ensure_route(api: Api, project_id: str, credential_id: str) -> str:
    base = f"/projects/{project_id}/gateway/routes"
    for route in api.expect(api.request("GET", base), 200):
        targets = route["config"]["targets"]
        if route["name"] == ROUTE_NAME and any(
            t["credential_id"] == credential_id for t in targets
        ):
            return str(route["id"])
    config = {"targets": [{"credential_id": credential_id}], "retry": {}, "fallback": {}}
    body = {"name": ROUTE_NAME, "config": config}
    return str(api.expect(api.request("POST", base, json=body), 201)["id"])


def _find_key(listed: list[dict[str, Any]], secret: str | None) -> dict[str, Any] | None:
    if not secret or not key_is_listed(listed, KEY_NAME, secret):
        return None
    return next(k for k in listed if k["name"] == KEY_NAME and secret.startswith(k["prefix"] + "_"))


def ensure_key(
    api: Api, project_id: str, route_id: str, scenario: Scenario, entry: dict[str, Any]
) -> None:
    """The scenario's gateway key, with its environment, route and rate limit as wanted."""
    base = f"/projects/{project_id}/gateway/keys"
    key = _find_key(api.expect(api.request("GET", base), 200), entry.get("gateway_key"))
    if key is None:
        body = {
            "name": KEY_NAME,
            "route_id": route_id,
            "environment": scenario.environment,
            "rpm_limit": scenario.key_rpm_limit,
        }
        key = api.expect(api.request("POST", base, json=body), 201)
        entry["gateway_key"] = key["secret"]
    entry["gateway_key_id"] = str(key["id"])
    changes: dict[str, Any] = {}
    if key["environment"] != scenario.environment:
        changes["environment"] = scenario.environment
    if str(key.get("route_id")) != route_id:
        changes["route_id"] = route_id
    if key.get("rpm_limit") != scenario.key_rpm_limit:
        changes["rpm_limit"] = scenario.key_rpm_limit
    if changes:
        api.expect(api.request("PATCH", f"{base}/{key['id']}", json=changes), 200)
    entry["key_fault_profile_id"] = key.get("fault_profile_id")


def _profile_is_stale(profile: dict[str, Any], fault: Fault) -> bool:
    return (
        profile["scenario"] != fault.scenario
        or any(profile["params"].get(k) != v for k, v in fault.params.items())
        or float(profile["probability"]) != float(fault.probability)
        or not profile["enabled"]
        or profile.get("expires_at") is not None
    )


def ensure_profile(api: Api, project_id: str, name: str, fault: Fault) -> str:
    base = f"/projects/{project_id}/gateway/fault-profiles"
    wanted = {
        "name": name,
        "scenario": fault.scenario,
        "params": fault.params,
        "probability": fault.probability,
        "enabled": True,
        "expires_at": None,
    }
    for profile in api.expect(api.request("GET", base), 200):
        if profile["name"] != name:
            continue
        if _profile_is_stale(profile, fault):
            patch = {k: v for k, v in wanted.items() if k != "name"}
            api.expect(api.request("PATCH", f"{base}/{profile['id']}", json=patch), 200)
        return str(profile["id"])
    return str(api.expect(api.request("POST", base, json=wanted), 201)["id"])


def attach_profile(
    api: Api, project_id: str, entry: dict[str, Any], profile_id: str | None
) -> None:
    current = entry.get("key_fault_profile_id")
    if (str(current) if current else None) == profile_id:
        return
    path = f"/projects/{project_id}/gateway/keys/{entry['gateway_key_id']}"
    api.expect(api.request("PATCH", path, json={"fault_profile_id": profile_id}), 200)
    entry["key_fault_profile_id"] = profile_id


def incident_org_name(org: str) -> str:
    # One organization per provider_incident case: the detector aggregates a whole org.
    return f"{INCIDENT_ORG_NAME}-{org.removeprefix('incident-')}{RUN_SUFFIX}"


def ensure_incident_org(api: Api, state: State, org: str) -> str:
    name = incident_org_name(org)
    me = api.expect(api.request("GET", "/auth/me"), 200)
    for membership in me["memberships"]:
        if membership["org"]["name"] == name:
            return str(state.set(f"{org}_org_id", membership["org"]["id"]))
    created = api.expect(api.request("POST", "/orgs", json={"name": name}), 201)
    return str(state.set(f"{org}_org_id", created["id"]))


def scoped(state: State, org: str) -> OrgScopedState:
    if org.startswith("incident"):
        return OrgScopedState(state, f"{org}_org_id", f"{org}_projects")
    return OrgScopedState(state, "org_id", "projects")


def _ensure_gateway(api: Api, state: State, scenario: Scenario, entry: dict[str, Any]) -> None:
    project_id = entry["project_id"]
    entry["route_id"] = ensure_route(api, project_id, state["credential_id"])
    ensure_key(api, project_id, entry["route_id"], scenario, entry)
    profile_id = None
    if scenario.fault is not None:
        profile_id = ensure_profile(api, project_id, f"doctor-{scenario.case}", scenario.fault)
    entry["fault_profile_id"] = profile_id
    attach_profile(api, project_id, entry, profile_id)


def setup(scenarios: list[Scenario]) -> None:
    main = main_state()
    for key in ("org_id", "credential_id", "pat", "password"):
        if not main.get(key):
            raise SetupError(f"pilot state has no {key}: run `python -m accuracy.setup` first")
    state = doctor_state()
    for key in ("org_id", "credential_id", "pat"):
        state.set(key, main[key])
    api = session(main)
    for org in sorted({s.org for s in scenarios if s.org.startswith("incident")}):
        ensure_incident_org(api, state, org)
    for scenario in scenarios:
        view = scoped(state, scenario.org)
        for name in scenario.project_names:
            entry = ensure_project(api, view, name)
            entry["environment"] = scenario.environment
            if scenario.gateway:
                _ensure_gateway(api, state, scenario, entry)
            projects = view.get("projects", {})
            projects[name] = entry
            view.set("projects", projects)
        print(f"ok  {scenario.id:48s} {', '.join(scenario.project_names)}")
    print(f"setup done: {len(scenarios)} scenarios, state in {state.path}")
