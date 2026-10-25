#!/usr/bin/env python3
"""Pin the release workflow's tag routing, permissions, action pins and publish order.

A `v*` tag must only ever release the server images, an `sdk-python-v*` tag must only ever
publish the Python SDK and an `sdk-typescript-v*` tag must only ever publish the TypeScript SDK.
Workflow YAML cannot be unit-tested on GitHub, so this reads .github/workflows/release.yml and
asserts those guarantees, with no expression evaluator: the `startsWith(github.ref, '...')`
guards are pulled out and applied to sample refs.

Every `uses:` must name a full 40-character commit SHA followed by a `# vX.Y.Z` comment. These
jobs hold `id-token: write`, `packages: write` and `contents: write`, and a tag such as `@v3`
can be moved to different code after review; a commit SHA cannot. The comment says which release
the SHA is, so a reviewer (and Dependabot) can read the pin.

Usage: check-release-workflow.py [path/to/release.yml]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

DEFAULT_WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/release.yml"

TAG_PATTERNS = {"v*", "sdk-python-v*", "sdk-typescript-v*"}
# job -> the ref prefix its `if:` must test
GUARDS = {
    "server": "refs/tags/v",
    "sdk-python": "refs/tags/sdk-python-v",
    "sdk-typescript": "refs/tags/sdk-typescript-v",
}
# ref -> the jobs that must run for it
EXPECTED_JOBS = {
    "refs/tags/v1.2.3": {"server"},
    "refs/tags/v1.2.3-rc.1": {"server"},
    "refs/tags/sdk-python-v1.2.3": {"sdk-python"},
    "refs/tags/sdk-typescript-v1.2.3": {"sdk-typescript"},
    "refs/heads/main": set(),
    "refs/heads/v1.2.3": set(),
    "refs/tags/latest": set(),
}
ID_TOKEN_JOBS = {"server", "sdk-python", "sdk-typescript"}  # cosign keyless / trusted publishing
PACKAGES_JOBS = {"server"}  # GHCR push

GUARD = re.compile(r"^(?:\$\{\{\s*)?startsWith\(github\.ref,\s*'([^']+)'\)(?:\s*\}\})?$")
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")
# A `uses:` line with the trailing comment that YAML parsing throws away:
#   uses: owner/repo@<40 hex> # v1.2.3
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*?))?\s*$")
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
        return [f"tag patterns must be exactly {sorted(TAG_PATTERNS)}, got {push['tags']}"]
    return []


def check_routing(jobs: dict) -> list[str]:
    if set(jobs) != set(GUARDS):
        return [f"jobs must be exactly {sorted(GUARDS)}, got {sorted(jobs)}"]
    errors, prefixes = [], {}
    for name, expected in GUARDS.items():
        match = GUARD.match(str(jobs[name].get("if", "")).strip())
        if not match:
            errors.append(f"job {name}: `if` must be a single startsWith(github.ref, '...')")
        elif match.group(1) != expected:
            errors.append(f"job {name}: guard tests '{match.group(1)}', want '{expected}'")
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
        for scope, jobs_allowed in (("id-token", ID_TOKEN_JOBS), ("packages", PACKAGES_JOBS)):
            granted = perms.get(scope) == "write"
            if granted and name not in jobs_allowed:
                errors.append(f"job {name}: `{scope}: write` is not needed here")
            if not granted and name in jobs_allowed:
                errors.append(f"job {name}: needs `{scope}: write`")
    return errors


def check_pins(jobs: dict, text: str) -> list[str]:
    """Every `uses:` is a full commit SHA with a `# vX.Y.Z` comment naming its release."""
    errors = []
    declared = sum(
        1 for job in jobs.values() for step in job.get("steps", []) if step.get("uses") is not None
    )
    lines = [
        (number, match)
        for number, line in enumerate(text.splitlines(), start=1)
        if (match := USES_LINE.match(line))
    ]
    if len(lines) != declared:  # e.g. a flow-style `{ uses: ... }` this check cannot read
        errors.append(f"found {len(lines)} `uses:` lines for {declared} steps; write one per line")
    for number, match in lines:
        action, _, ref = match["ref"].partition("@")
        if not SHA_PIN.match(ref):
            errors.append(
                f"line {number}: `{match['ref']}` must be pinned to a full 40-character commit SHA"
            )
        elif not VERSION_COMMENT.match(match["comment"] or ""):
            errors.append(f"line {number}: `{action}` needs a trailing `# vX.Y.Z` release comment")
    return errors


def check_server_order(steps: list[dict]) -> list[str]:
    """The scan gates the push; signing, `latest`, SBOMs and the release follow it, in order."""
    errors, previous = [], -1
    for label, test in SERVER_PIPELINE:
        hits = [i for i, step in enumerate(steps) if test(step)]
        if not hits:
            errors.append(f"job server: no {label} step")
        elif hits[0] <= previous:
            errors.append(f"job server: {label} must come after the previous pipeline stage")
        if hits:
            previous = hits[-1]  # the last SBOM step must precede the SBOM signing
            if label == "trivy scan":
                text = run_of(steps[hits[0]])
                if "CRITICAL,HIGH" not in text:
                    errors.append("job server: trivy must gate on --severity CRITICAL,HIGH")
                if steps[hits[0]].get("continue-on-error") or "|| true" in text:
                    errors.append("job server: the trivy scan must not be allowed to fail open")
            if label == "latest tag" and not PRERELEASE_GUARD.search(str(steps[hits[0]].get("if"))):
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
                        errors.append(f"job {name}: must not do {owner}'s publish ({needle})")
    return errors


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_WORKFLOW
    if not path.is_file():
        print(f"FAIL: {path} does not exist")
        return 1
    text = path.read_text()
    wf = yaml.safe_load(text)
    errors = check_triggers(wf)
    jobs = wf.get("jobs") or {}
    errors += check_routing(jobs)
    if set(jobs) == set(GUARDS):  # the remaining checks index both jobs
        errors += check_permissions(wf, jobs)
        errors += check_pins(jobs, text)
        errors += check_server_order(jobs["server"].get("steps", []))
        errors += check_isolation(jobs)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print("release workflow: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
