#!/usr/bin/env python3
"""Pin the release workflow's tag routing, permissions, action pins and publish order.

A `v*` tag must only ever release the server images, an `sdk-python-v*` tag must only ever
publish the Python SDK and an `sdk-typescript-v*` tag must only ever publish the TypeScript SDK.
This validates tag routing, reusable CI dependencies, exact-SHA guards and candidate identity.
The focused contract tests execute the aggregate script against controlled dependency results;
GitHub's hosted scheduling and registry publication still need an authorized RC rehearsal.

Every external `uses:` must name a full 40-character commit SHA with a `# vX.Y.Z` comment.
The local CI workflow reference resolves at the caller's commit; its actions are checked too. These
jobs hold `id-token: write`, `packages: write` and `contents: write`, and a tag such as `@v3`
can be moved to different code after review; a commit SHA cannot. The comment says which release
the SHA is, so a reviewer (and Dependabot) can read the pin.

Usage: check-release-workflow.py [path/to/release.yml] [path/to/ci.yml]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

DEFAULT_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github/workflows/release.yml"
)

TAG_PATTERNS = {"v*", "sdk-python-v*", "sdk-typescript-v*"}
# job -> the ref prefix its `if:` must test
GUARDS = {
    "verify": "refs/tags/v",
    "server": "refs/tags/v",
    "sdk-python": "refs/tags/sdk-python-v",
    "sdk-typescript": "refs/tags/sdk-typescript-v",
}
# ref -> the jobs that must run for it
EXPECTED_JOBS = {
    "refs/tags/v1.2.3": {"verify", "server"},
    "refs/tags/v1.2.3-rc.1": {"verify", "server"},
    "refs/tags/sdk-python-v1.2.3": {"sdk-python"},
    "refs/tags/sdk-typescript-v1.2.3": {"sdk-typescript"},
    "refs/heads/main": set(),
    "refs/heads/v1.2.3": set(),
    "refs/tags/latest": set(),
}
ID_TOKEN_JOBS = {
    "server",
    "sdk-python",
    "sdk-typescript",
}  # cosign keyless / trusted publishing
PACKAGES_JOBS = {"server"}  # GHCR push

GUARD = re.compile(
    r"^(?:\$\{\{\s*)?startsWith\(github\.ref,\s*'([^']+)'\)(?:\s*\}\})?$"
)
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")
# A `uses:` line with the trailing comment that YAML parsing throws away:
#   uses: owner/repo@<40 hex> # v1.2.3
USES_LINE = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*?))?\s*$"
)
VERSION_COMMENT = re.compile(r"^v\d+\.\d+\.\d+\S*$")
PRERELEASE_GUARD = re.compile(r"steps\.meta\.outputs\.prerelease\s*==\s*'false'")


def tag_matches(ref: str) -> bool:
    """Whether a push of `ref` passes the `on.push.tags` filter (a glob `*` stops at `/`)."""
    if not ref.startswith("refs/tags/"):
        return False
    name = ref.removeprefix("refs/tags/")
    return any(
        re.fullmatch(re.escape(p).replace(r"\*", "[^/]*"), name) for p in TAG_PATTERNS
    )


def run_of(step: dict) -> str:
    return str(step.get("run", ""))


def uses_of(step: dict) -> str:
    return str(step.get("uses", ""))


def is_push(step: dict) -> bool:
    with_ = step.get("with") or {}
    return "docker push" in run_of(step) or str(with_.get("push")).lower() == "true"


def moves_latest(step: dict) -> bool:
    return ":latest" in run_of(step)


# The server job's publish pipeline, in the order it must run: (label, step test). The moving
# `latest` tag comes only after the image signature, so it can never point at an unsigned image.
SERVER_PIPELINE = [
    ("candidate smoke", lambda s: "deploy/smoke.sh" in run_of(s)),
    ("trivy scan", lambda s: "trivy" in run_of(s) and "--exit-code 1" in run_of(s)),
    ("image push", lambda s: is_push(s) and not moves_latest(s)),
    ("image signature", lambda s: "cosign sign " in run_of(s)),
    ("latest tag", moves_latest),
    ("sbom", lambda s: uses_of(s).startswith("anchore/sbom-action@")),
    ("sbom signature", lambda s: "cosign sign-blob " in run_of(s)),
    ("release", lambda s: uses_of(s).startswith("softprops/action-gh-release@")),
]


def check_triggers(wf: dict) -> list[str]:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as boolean True
    if not isinstance(on, dict) or set(on) != {"push"}:
        return ["workflow must trigger on `push` only"]
    push = on["push"]
    if not isinstance(push, dict) or set(push) != {"tags"}:
        return ["`push` must filter on `tags` only (no branches, no paths)"]
    if set(push["tags"]) != TAG_PATTERNS:
        return [
            f"tag patterns must be exactly {sorted(TAG_PATTERNS)}, got {push['tags']}"
        ]
    return []


def check_routing(jobs: dict) -> list[str]:
    if set(jobs) != set(GUARDS):
        return [f"jobs must be exactly {sorted(GUARDS)}, got {sorted(jobs)}"]
    errors, prefixes = [], {}
    for name, expected in GUARDS.items():
        condition = str(jobs[name].get("if", "")).strip()
        if name == "server":
            condition = condition.split(" && ", 1)[0]
        match = GUARD.match(condition)
        if not match:
            errors.append(
                f"job {name}: `if` must be a single startsWith(github.ref, '...')"
            )
        elif match.group(1) != expected:
            errors.append(
                f"job {name}: guard tests '{match.group(1)}', want '{expected}'"
            )
        else:
            prefixes[name] = match.group(1)
    if errors:
        return errors
    for ref, want in EXPECTED_JOBS.items():
        ran = {n for n, p in prefixes.items() if tag_matches(ref) and ref.startswith(p)}
        if ran != want:
            errors.append(f"ref {ref} runs {sorted(ran)}, want {sorted(want)}")
    return errors


def check_permissions(wf: dict, jobs: dict) -> list[str]:
    errors = []
    if wf.get("permissions") != {"contents": "read"}:
        errors.append("top-level permissions must be exactly {contents: read}")
    for name, job in jobs.items():
        perms = job.get("permissions", {})
        if not isinstance(perms, dict):
            errors.append(f"job {name}: permissions must be an explicit mapping")
            continue
        for scope, jobs_allowed in (
            ("id-token", ID_TOKEN_JOBS),
            ("packages", PACKAGES_JOBS),
        ):
            granted = perms.get(scope) == "write"
            if granted and name not in jobs_allowed:
                errors.append(f"job {name}: `{scope}: write` is not needed here")
            if not granted and name in jobs_allowed:
                errors.append(f"job {name}: needs `{scope}: write`")
    return errors


def check_pins(jobs: dict, text: str) -> list[str]:
    """Every `uses:` is a full commit SHA with a `# vX.Y.Z` comment naming its release."""
    errors = []
    declared = sum(1 for job in jobs.values() if job.get("uses")) + sum(
        1
        for job in jobs.values()
        for step in job.get("steps", [])
        if step.get("uses") is not None
    )
    lines = [
        (number, match)
        for number, line in enumerate(text.splitlines(), start=1)
        if (match := USES_LINE.match(line))
    ]
    if (
        len(lines) != declared
    ):  # e.g. a flow-style `{ uses: ... }` this check cannot read
        errors.append(
            f"found {len(lines)} `uses:` lines for {declared} steps; write one per line"
        )
    for number, match in lines:
        if match["ref"] == "./.github/workflows/ci.yml":
            continue  # Same-commit reusable workflow; all its actions are checked separately.
        action, _, ref = match["ref"].partition("@")
        if not SHA_PIN.match(ref):
            errors.append(
                f"line {number}: `{match['ref']}` must be pinned to a full 40-character commit SHA"
            )
        elif not VERSION_COMMENT.match(match["comment"] or ""):
            errors.append(
                f"line {number}: `{action}` needs a trailing `# vX.Y.Z` release comment"
            )
    return errors


def check_server_order(steps: list[dict]) -> list[str]:
    """The scan gates the push; signing, `latest`, SBOMs and the release follow it, in order."""
    errors, previous = [], -1
    for label, test in SERVER_PIPELINE:
        hits = [i for i, step in enumerate(steps) if test(step)]
        if not hits:
            errors.append(f"job server: no {label} step")
        elif hits[0] <= previous:
            errors.append(
                f"job server: {label} must come after the previous pipeline stage"
            )
        if hits:
            previous = hits[-1]  # the last SBOM step must precede the SBOM signing
            if label == "trivy scan":
                text = run_of(steps[hits[0]])
                if "CRITICAL,HIGH" not in text:
                    errors.append(
                        "job server: trivy must gate on --severity CRITICAL,HIGH"
                    )
                if steps[hits[0]].get("continue-on-error") or "|| true" in text:
                    errors.append(
                        "job server: the trivy scan must not be allowed to fail open"
                    )
            if label == "latest tag" and not PRERELEASE_GUARD.search(
                str(steps[hits[0]].get("if"))
            ):
                errors.append("job server: `latest` must be skipped for pre-releases")
    return errors


def check_isolation(jobs: dict) -> list[str]:
    """No step of one release train can perform the other's publish."""
    errors = []
    publishes = {
        "server": ("docker push", "docker/login-action", "docker/build-push-action"),
        "sdk-python": ("pypa/gh-action-pypi-publish@",),
        "sdk-typescript": ("npm publish",),
    }
    for name, job in jobs.items():
        for step in job.get("steps", []):
            text = step.get("run", "") + step.get("uses", "")
            for owner, needles in publishes.items():
                if owner == name:
                    continue
                for needle in needles:
                    if needle in text:
                        errors.append(
                            f"job {name}: must not do {owner}'s publish ({needle})"
                        )
    return errors


SERVER_GUARD = (
    "startsWith(github.ref, 'refs/tags/v') && needs.verify.result == 'success' "
    "&& needs.verify.outputs.verified_sha == github.sha"
)
REQUIRED_JOBS = {
    "backend",
    "sdk-python",
    "sdk-typescript",
    "frontend",
    "docs-site",
    "workflows",
    "docker",
    "compose-smoke",
}


def check_verification(jobs: dict, ci: dict) -> list[str]:
    """Reject bypasses of the same-commit, all-required-jobs success boundary."""
    errors = []
    verify, server = jobs["verify"], jobs["server"]
    if (
        verify.get("uses") != "./.github/workflows/ci.yml"
        or verify.get("with")
        or verify.get("secrets")
    ):
        errors.append(
            "verification must call same-commit CI without input/ref/secret overrides"
        )
    if server.get("needs") != "verify" or server.get("if") != SERVER_GUARD:
        errors.append(
            "server verification dependency must require success and matching verified SHA"
        )
    triggers = ci.get("on", ci.get(True, {}))
    output = (
        triggers.get("workflow_call", {}).get("outputs", {}).get("verified_sha", {})
    )
    if output.get("value") != "${{ jobs.required.outputs.sha }}":
        errors.append("CI verification must expose only the required gate's SHA")
    ci_jobs = ci.get("jobs", {})
    gate = ci_jobs.get("required", {})
    required = set(gate.get("needs", []))
    if required != set(ci_jobs) - {"required"} or not required >= REQUIRED_JOBS:
        errors.append("CI required gate must depend on every required job")
    if gate.get("if") != "always()" or gate.get("outputs") != {
        "sha": "${{ steps.verify.outputs.sha }}"
    }:
        errors.append(
            "CI required gate must run even after failure and output its verified SHA"
        )
    gate_steps = gate.get("steps", [])
    if (
        len(gate_steps) != 1
        or gate_steps[0].get("id") != "verify"
        or gate_steps[0].get("env") != {"NEEDS_JSON": "${{ toJSON(needs) }}"}
    ):
        errors.append("CI required gate must inspect all dependency results")
    for name, job in ci_jobs.items():
        if name != "required" and job.get("if"):
            errors.append(f"CI required job {name} cannot be conditional")
    errors += check_job_safety({**{f"CI {n}": j for n, j in ci_jobs.items()}, **jobs})
    errors += check_candidate_identity(server.get("steps", []))
    return errors


def check_job_safety(jobs: dict) -> list[str]:
    """Required checks cannot skip work, tolerate failure, or check out another commit."""
    errors = []
    for name, job in jobs.items():
        if job.get("continue-on-error"):
            errors.append(f"job {name}: verification cannot fail open")
        for step in job.get("steps", []):
            if step.get("continue-on-error"):
                errors.append(f"job {name}: verification steps cannot fail open")
            if step.get("if") and not (name == "server" and moves_latest(step)):
                errors.append(
                    f"job {name}: verification/publish steps cannot be skipped"
                )
            if (
                uses_of(step).startswith("actions/checkout@")
                and (step.get("with") or {}).get("ref") != "${{ github.sha }}"
            ):
                errors.append(f"job {name}: checkout must use the exact event SHA")
    return errors


def check_candidate_identity(steps: list[dict]) -> list[str]:
    """Candidate builds precede smoke; local IDs and pushed manifest config must agree."""
    errors = []
    smoke = [i for i, step in enumerate(steps) if "deploy/smoke.sh" in run_of(step)]
    if len(smoke) != 1:
        return [
            "server verification must smoke the release candidate images exactly once"
        ]
    command = run_of(steps[smoke[0]])
    for required_text in (
        'docker compose -p "$SMOKE_PROJECT"',
        'del service["build"]',
        'service["pull_policy"] = "never"',
        'cmp "$RUNNER_TEMP/candidate-ids" "$RUNNER_TEMP/tested-ids"',
    ):
        if required_text not in command:
            errors.append(
                "candidate verification must prevent rebuild/pull and preserve image IDs"
            )
    for step in steps[smoke[0] + 1 :]:
        if uses_of(step).startswith(
            "docker/build-push-action@"
        ) or "docker build" in run_of(step).replace(
            "docker buildx imagetools inspect", ""
        ):
            errors.append("candidate verification forbids rebuilding after smoke")
    pushes = [step for step in steps if is_push(step) and not moves_latest(step)]
    for step in pushes:
        for required_text in (
            'cmp "$RUNNER_TEMP/tested-ids" "$RUNNER_TEMP/publish-ids"',
            'if [[ "$local_id" != "$remote_id" && "$local_id" != "${digest_ref#*@}" ]]; then',
            "jq -er '.config.digest'",
        ):
            if required_text not in run_of(step):
                errors.append(
                    "candidate verification must match published registry digest to tested image ID"
                )
    return errors


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_WORKFLOW
    if not path.is_file():
        print(f"FAIL: {path} does not exist")
        return 1
    text = path.read_text()
    wf = yaml.safe_load(text)
    ci_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_WORKFLOW.with_name("ci.yml")
    ci_text = ci_path.read_text()
    ci = yaml.safe_load(ci_text)
    errors = check_triggers(wf)
    jobs = wf.get("jobs") or {}
    errors += check_routing(jobs)
    if set(jobs) == set(GUARDS):  # the remaining checks index both jobs
        errors += check_permissions(wf, jobs)
        errors += check_pins(jobs, text)
        errors += check_server_order(jobs["server"].get("steps", []))
        errors += check_isolation(jobs)
        errors += check_verification(jobs, ci)
        errors += check_pins(ci.get("jobs", {}), ci_text)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print("release workflow: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
