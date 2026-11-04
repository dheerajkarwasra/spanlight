"""Release contract regressions and adversarial workflow fixtures (no Docker or network)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from itertools import product
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / ".github/workflows/release.yml"
CI = ROOT / ".github/workflows/ci.yml"


class ReleaseContractTests(unittest.TestCase):
    def check_workflow(self, release, ci=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release.yml"
            path.write_text(release)
            ci_path = Path(directory) / "ci.yml"
            ci_path.write_text(ci or CI.read_text())
            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/check-release-workflow.py"),
                    str(path),
                    str(ci_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_current_contract_passes(self):
        result = self.check_workflow(RELEASE.read_text())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_publication_without_verification_dependency_is_rejected(self):
        fixture = RELEASE.read_text().replace("    needs: verify\n", "")
        result = self.check_workflow(fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("verification", result.stdout)

    def test_branch_checkout_is_rejected(self):
        fixture = CI.read_text().replace("ref: ${{ github.sha }}", "ref: main", 1)
        result = self.check_workflow(RELEASE.read_text(), fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("checkout", result.stdout)

    def test_missing_required_job_is_rejected(self):
        fixture = CI.read_text().replace(
            "needs: [backend, sdk-python, sdk-typescript, frontend, docs-site, workflows, docker, compose-smoke]",
            "needs: [backend]",
        )
        result = self.check_workflow(RELEASE.read_text(), fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("required", result.stdout)

    def test_mismatched_sha_guard_is_rejected(self):
        fixture = RELEASE.read_text().replace(
            "needs.verify.outputs.verified_sha == github.sha",
            "needs.verify.outputs.verified_sha != github.sha",
        )
        result = self.check_workflow(fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_candidate_smoke_cannot_fail_open(self):
        fixture = RELEASE.read_text().replace(
            "      - name: Test release candidate images\n",
            "      - name: Test release candidate images\n        continue-on-error: true\n",
        )
        result = self.check_workflow(fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_rebuild_after_candidate_smoke_is_rejected(self):
        fixture = RELEASE.read_text().replace(
            "      - name: Push images",
            "      - run: docker build .\n\n      - name: Push images",
        )
        result = self.check_workflow(fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_missing_registry_digest_identity_check_is_rejected(self):
        fixture = RELEASE.read_text().replace(
            'if [[ "$local_id" != "$remote_id" && "$local_id" != "${digest_ref#*@}" ]]; then',
            "if false; then",
        )
        result = self.check_workflow(fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_published_identity_accepts_manifest_or_config_only(self):
        workflow = yaml.safe_load(RELEASE.read_text())
        push = next(
            step
            for step in workflow["jobs"]["server"]["steps"]
            if step.get("id") == "push"
        )
        # YAML removes common indentation; isolate the real mismatch branch.
        command = (
            'if [[ "$local_id"'
            + push["run"].split('if [[ "$local_id"', 1)[1].split("fi", 1)[0]
            + "fi"
        )
        for local_id, code in [
            ("sha256:config", 0),
            ("sha256:manifest", 0),
            ("sha256:other", 1),
        ]:
            with self.subTest(local_id=local_id):
                result = subprocess.run(
                    ["bash", "-e", "-c", command],
                    env={
                        **os.environ,
                        "local_id": local_id,
                        "remote_id": "sha256:config",
                        "digest_ref": "example/image@sha256:manifest",
                        "ref": "example/image",
                        "VERSION": "1",
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, code, result.stderr)

    def test_default_compose_project_cannot_escape_smoke_isolation(self):
        fixture = RELEASE.read_text().replace(
            'docker compose -p "$SMOKE_PROJECT" -f deploy/compose.yaml',
            "docker compose -f deploy/compose.yaml",
        )
        result = self.check_workflow(fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_candidate_compose_transform_preserves_images_without_rebuilds(self):
        workflow = yaml.safe_load(RELEASE.read_text())
        smoke = next(
            step
            for step in workflow["jobs"]["server"]["steps"]
            if step.get("name") == "Test release candidate images"
        )
        command = (
            smoke["run"].split("python3 - <<'PYTHON'\n", 1)[1].split("\nPYTHON", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy").mkdir()
            (root / "candidate-compose.json").write_text(
                json.dumps(
                    {
                        "services": {
                            "api": {
                                "image": "candidate:1",
                                "build": {"target": "backend"},
                            },
                            "worker": {
                                "image": "candidate:1",
                                "build": {"target": "backend"},
                            },
                            "web": {"image": "web:1", "build": {"target": "web"}},
                            "postgres": {"image": "postgres:17"},
                        }
                    }
                )
            )
            subprocess.run(
                [sys.executable, "-c", command],
                cwd=root,
                env={**os.environ, "RUNNER_TEMP": directory},
                check=True,
            )
            config = json.loads((root / "deploy/compose.yaml").read_text())
            self.assertEqual(
                config,
                {
                    "services": {
                        "api": {"image": "candidate:1", "pull_policy": "never"},
                        "worker": {"image": "candidate:1", "pull_policy": "never"},
                        "web": {"image": "web:1", "pull_policy": "never"},
                        "postgres": {"image": "postgres:17"},
                    }
                },
            )

    def test_success_is_the_only_allowed_pair_of_dependency_states(self):
        command = yaml.safe_load(CI.read_text())["jobs"]["required"]["steps"][0]["run"]
        states = ("success", "failure", "skipped", "cancelled", "pending", "")
        for frontend, backend in product(states, repeat=2):
            with (
                self.subTest(frontend=frontend, backend=backend),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "output"
                output.touch()
                result = subprocess.run(
                    ["bash", "-e", "-c", command],
                    env={
                        **os.environ,
                        "NEEDS_JSON": json.dumps(
                            {
                                "frontend": {"result": frontend},
                                "backend": {"result": backend},
                            }
                        ),
                        "GITHUB_SHA": "b" * 40,
                        "GITHUB_OUTPUT": str(output),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                expected = 0 if (frontend, backend) == ("success", "success") else 1
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(bool(output.read_text()), expected == 0)

    def test_required_gate_rejects_empty_missing_and_first_failed_results(self):
        command = yaml.safe_load(CI.read_text())["jobs"]["required"]["steps"][0]["run"]
        for results in (
            {},
            {"frontend": {}},
            {"frontend": {"result": "failure"}, "backend": {"result": "success"}},
        ):
            with (
                self.subTest(results=results),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "output"
                output.touch()
                result = subprocess.run(
                    ["bash", "-e", "-c", command],
                    env={
                        **os.environ,
                        "NEEDS_JSON": json.dumps(results),
                        "GITHUB_SHA": "a" * 40,
                        "GITHUB_OUTPUT": str(output),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(output.read_text(), "")

    def test_required_gate_runs_real_script_against_all_results(self):
        ci = yaml.safe_load(CI.read_text())
        gate = ci["jobs"].get("required")
        self.assertIsNotNone(gate, "required verification aggregate must exist")
        command = gate["steps"][0]["run"]
        for status, code in [
            ("success", 0),
            ("failure", 1),
            ("skipped", 1),
            ("cancelled", 1),
            ("pending", 1),
            ("", 1),
        ]:
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory) / "output"
                output.touch()
                result = subprocess.run(
                    ["bash", "-e", "-c", command],
                    env={
                        **os.environ,
                        "NEEDS_JSON": json.dumps(
                            {
                                "frontend": {"result": "success"},
                                "backend": {"result": status},
                            }
                        ),
                        "GITHUB_SHA": "a" * 40,
                        "GITHUB_OUTPUT": str(output),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(
                    output.read_text(), "sha=" + "a" * 40 + "\n" if code == 0 else ""
                )


if __name__ == "__main__":
    unittest.main()
