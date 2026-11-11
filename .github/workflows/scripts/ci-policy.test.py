#!/usr/bin/env python3
"""Offline regression checks for automatic versus privileged CI execution."""

from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name):
    # BaseLoader preserves the Actions `on` key instead of treating it as YAML 1.1 true.
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def secrets_used(value):
    return set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", str(value)))


class CIPolicyTests(unittest.TestCase):
    def test_routine_ci_exists_and_is_unprivileged(self):
        path = WORKFLOWS / "basic-ci.yml"
        self.assertTrue(path.exists(), "Routine checks need a separate secret-free workflow")
        doc = workflow(path.name)
        events = doc["on"]
        self.assertIn("pull_request", events)
        self.assertIn("schedule", events)
        self.assertNotIn("pull_request_target", events)
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertFalse(secrets_used(doc))
        for name, job in doc["jobs"].items():
            with self.subTest(job=name):
                self.assertNotIn("environment", job)
                self.assertNotIn("secrets", job)
                self.assertNotIn("uses", job, "A reusable call must not hide privileged work")
                self.assertNotIn("release", job.get("if", ""))
                self.assertNotIn("write", str(job.get("permissions", {})))
                self.assertNotIn("self-hosted", str(job["runs-on"]))
                self.assertNotRegex(str(job), r"full-test-suite|setup-vllm-runpod|make test-core(?:\s|$)")
                for step in job.get("steps", []):
                    if step.get("uses", "").startswith("actions/checkout@"):
                        self.assertEqual(step.get("with", {}).get("persist-credentials"), "false")

    def test_live_workflow_does_not_run_on_a_timer(self):
        doc = workflow("pr-tests.yml")
        self.assertNotIn("schedule", doc["on"], "A nightly timer must not queue credentialed tests")
        self.assertNotIn("push", doc["on"])
        self.assertIn("workflow_dispatch", doc["on"])
        gate = doc["jobs"]["run-tests"]
        environment = gate["environment"]
        self.assertEqual(environment.get("name") if isinstance(environment, dict) else environment, "pr-testing")
        condition = gate.get("if", "")
        self.assertIn("github.event_name", condition)
        self.assertIn("head.repo.full_name == github.repository", condition)
        self.assertIn("'release'", condition)
        self.assertIn("run-tests", doc["jobs"]["full-tests"]["needs"])

    def test_preflight_has_no_credentials_or_write_token(self):
        doc = workflow("pr-tests.yml")
        gate = doc["jobs"]["run-tests"]
        self.assertFalse(secrets_used(gate), "The preflight must not receive provider/cloud credentials")
        self.assertEqual(gate.get("permissions", doc["permissions"]), {"contents": "read"})
        for step in gate.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                self.assertEqual(step.get("with", {}).get("persist-credentials"), "false")

    def test_reusable_secrets_are_explicit_and_complete(self):
        suite = workflow("full-test-suite.yml")
        declared = suite["on"]["workflow_call"].get("secrets", {})
        used = secrets_used(suite["jobs"])
        self.assertEqual(set(declared), used, "Declare precisely the secrets consumed by the test suite")
        self.assertNotIn("GH_TOKEN", declared)
        self.assertNotIn("DOCKER_PASSWORD", declared)
        for caller_name in ("pr-tests.yml", "release-pipeline.yml"):
            with self.subTest(caller=caller_name):
                supplied = workflow(caller_name)["jobs"]["full-tests"]["secrets"]
                self.assertIsInstance(supplied, dict, "Never inherit release publishing credentials")
                self.assertEqual(set(supplied), set(declared))
                for name, expression in supplied.items():
                    self.assertEqual(secrets_used(expression), {name})

    def test_every_reusable_secret_consumer_has_the_approval_environment(self):
        for name, job in workflow("full-test-suite.yml")["jobs"].items():
            if not secrets_used(job):
                continue
            with self.subTest(job=name):
                environment = job.get("environment", {})
                self.assertEqual(environment.get("name") if isinstance(environment, dict) else environment, "pr-testing")
                self.assertEqual(job.get("permissions", {"contents": "read"}), {"contents": "read"})
                for step in job.get("steps", []):
                    if step.get("uses", "").startswith("actions/checkout@"):
                        self.assertEqual(step.get("with", {}).get("persist-credentials"), "false")

    def test_gpu_provisioning_requires_manual_opt_in(self):
        suite = workflow("full-test-suite.yml")
        inputs = suite["on"]["workflow_call"]["inputs"]
        self.assertIn("run-vllm-tests", inputs)
        self.assertEqual(inputs["run-vllm-tests"].get("default"), "false")
        self.assertIn("inputs.run-vllm-tests", suite["jobs"]["test-vllm"]["if"])
        for caller_name in ("pr-tests.yml", "release-pipeline.yml"):
            with self.subTest(caller=caller_name):
                caller = workflow(caller_name)
                option = caller["on"]["workflow_dispatch"]["inputs"]["run-vllm-tests"]
                self.assertEqual(option.get("default"), "false")
                expression = caller["jobs"]["full-tests"]["with"]["run-vllm-tests"]
                self.assertIn("workflow_dispatch", expression)
                self.assertIn("inputs.run-vllm-tests", expression)

    def test_benchmark_code_is_pinned(self):
        found = 0
        for job in workflow("full-test-suite.yml")["jobs"].values():
            for step in job.get("steps", []):
                settings = step.get("with", {})
                if settings.get("repository") == "maximhq/bifrost-benchmarking":
                    found += 1
                    self.assertRegex(settings.get("ref", ""), r"^[a-f0-9]{40}$")
        self.assertEqual(found, 2)

    def test_coverage_tokens_are_not_in_test_commands(self):
        suite = workflow("full-test-suite.yml")
        for job in suite["jobs"].values():
            for step in job.get("steps", []):
                if re.search(r"scripts/test-(framework|all-plugins|bifrost-http)\.sh", step.get("run", "")):
                    self.assertNotIn("CODECOV_TOKEN", step.get("env", {}))
        for name in ("test-framework.sh", "test-all-plugins.sh", "test-bifrost-http.sh"):
            text = (WORKFLOWS / "scripts" / name).read_text()
            self.assertNotIn("uploader.codecov.io/latest", text)

    def test_default_test_entrypoint_avoids_stale_and_live_tests(self):
        text = (WORKFLOWS / "scripts/run-tests.sh").read_text()
        self.assertIn("test-core-unit.sh", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("pytest", text)
        self.assertNotIn("run_all_tests.py", text)
        self.assertNotIn("go test -v -run . ./...", text)

    def test_workflow_lint_covers_ordinary_prs(self):
        doc = workflow("workflow-lint.yml")
        for job in doc["jobs"].values():
            self.assertNotIn("'release'", job.get("if", ""))

    def test_governance_uses_only_test_credentials_and_this_runs_binary(self):
        job = workflow("pr-tests.yml")["jobs"].get("test-governance")
        self.assertIsNotNone(job, "Restore the intended Go governance E2E coverage")
        self.assertEqual(job["environment"], "pr-testing")
        self.assertEqual(secrets_used(job), {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"})
        self.assertIn("full-tests", job["needs"])
        self.assertIn("build-gateway-result == 'success'", job["if"])
        downloads = [step for step in job["steps"] if step.get("uses", "").startswith("actions/download-artifact@")]
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]["with"]["name"], "bifrost-http-binary")
        self.assertNotIn("run-id", downloads[0]["with"])
        self.assertNotIn("repository", downloads[0]["with"])

    def test_live_jobs_use_one_reviewed_revision(self):
        for filename in ("pr-tests.yml", "full-test-suite.yml"):
            for job in workflow(filename)["jobs"].values():
                for step in job.get("steps", []):
                    if not step.get("uses", "").startswith("actions/checkout@"):
                        continue
                    settings = step.get("with", {})
                    if "repository" not in settings:
                        self.assertEqual(settings.get("ref"), "${{ github.event.pull_request.head.sha || github.sha }}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
