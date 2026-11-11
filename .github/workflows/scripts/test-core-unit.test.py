#!/usr/bin/env python3
"""Exercise the real unit selector with a fake Go command; no toolchain or APIs."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER = Path(os.environ.get("BIFROST_UNIT_RUNNER_UNDER_TEST", SCRIPT_DIR / "test-core-unit.sh"))


class CoreUnitRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bifrost-unit-selector-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.core = self.root / "core"
        self.core.mkdir()
        scripts = self.root / ".github" / "workflows" / "scripts"
        scripts.mkdir(parents=True)
        self.runner = scripts / "test-core-unit.sh"
        shutil.copyfile(RUNNER, self.runner)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.listing = self.root / "packages.tsv"
        self.listing.write_text("")
        self.calls = self.root / "go-test-args.txt"
        fake_go = self.bin / "go"
        fake_go.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            "if sys.argv[1] == 'list':\n"
            "    print(pathlib.Path(os.environ['BIFROST_TEST_LIST']).read_text(), end='')\n"
            "    sys.exit(int(os.environ.get('BIFROST_TEST_LIST_STATUS', '0')))\n"
            "if sys.argv[1] == 'test':\n"
            "    pathlib.Path(os.environ['BIFROST_TEST_CALLS']).write_text('\\n'.join(sys.argv[2:]))\n"
            "    sys.exit(int(os.environ.get('BIFROST_TEST_TEST_STATUS', '0')))\n"
            "raise SystemExit('unexpected Go command: ' + repr(sys.argv))\n"
        )
        fake_go.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "BIFROST_TEST_LIST": str(self.listing),
            "BIFROST_TEST_CALLS": str(self.calls),
        }

    def package(self, name, test_source='package fixture\n'):
        directory = self.core / name
        directory.mkdir(parents=True, exist_ok=True)
        if test_source is not None:
            (directory / "fixture_test.go").write_text(test_source)
        import_path = "github.com/maximhq/bifrost/core/" + name
        with self.listing.open("a") as listing:
            listing.write(import_path + "\t" + str(directory) + "\n")
        return import_path

    def run_selector(self, cwd=None, **extra_env):
        return subprocess.run(
            ["bash", str(self.runner)],
            cwd=cwd or self.core,
            env={**self.env, **extra_env},
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_selects_unit_and_mock_packages_but_excludes_live_provider_packages(self):
        unit = self.package("schemas")
        mock = self.package("internal/mcptests")
        self.package("internal/llmtests")
        live = self.package(
            "providers/live",
            'package fixture\nimport "github.com/maximhq/bifrost/core/internal/llmtests"\n',
        )
        result = self.run_selector()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = self.calls.read_text().splitlines()
        self.assertIn(unit, arguments)
        self.assertIn(mock, arguments)
        self.assertNotIn(live, arguments)
        self.assertNotIn("github.com/maximhq/bifrost/core/internal/llmtests", arguments)

    def test_partial_discovery_failure_does_not_run_incomplete_test_set(self):
        self.package("schemas")
        result = self.run_selector(BIFROST_TEST_LIST_STATUS="23")
        self.assertNotEqual(result.returncode, 0, "partial go list failure must fail the job")
        self.assertFalse(self.calls.exists(), "tests must not run after discovery failed")

    def test_empty_discovery_fails(self):
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_only_live_packages_fails_instead_of_reporting_success(self):
        self.package("internal/llmtests")
        self.package(
            "providers/live",
            'package fixture\nimport "github.com/maximhq/bifrost/core/internal/llmtests"\n',
        )
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_packages_without_tests_do_not_turn_empty_suite_green(self):
        self.package("notests", test_source=None)
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_test_failures_propagate(self):
        self.package("schemas")
        result = self.run_selector(BIFROST_TEST_TEST_STATUS="17")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.calls.exists())

    def test_runs_from_repository_root(self):
        self.package("schemas")
        result = self.run_selector(cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_source_directory_fails_before_running_tests(self):
        self.package("schemas")
        shutil.rmtree(self.core / "schemas")
        result = self.run_selector()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
