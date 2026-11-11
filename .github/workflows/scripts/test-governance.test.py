#!/usr/bin/env python3
"""Exercise governance orchestration with a local fake gateway and fake Go."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).with_name("test-governance.sh")


class GovernanceRunnerTests(unittest.TestCase):
    def run_fixture(self, *, missing_key=False, go_status=0, exited_gateway=False):
        self.assertTrue(SCRIPT.exists(), "Governance needs a real Go test entry point")
        with tempfile.TemporaryDirectory(prefix="bifrost-governance-fixture-") as directory:
            root = Path(directory)
            scripts = root / ".github/workflows/scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(SCRIPT, scripts / SCRIPT.name)
            suite = root / "tests/governance"
            suite.mkdir(parents=True)
            (suite / "config.json").write_text(json.dumps({
                "providers": {}, "config_store": {"config": {"path": "./data/old.db"}}
            }))
            binaries = root / "bin"
            binaries.mkdir()
            gateway = binaries / "fake-gateway"
            gateway.write_text(textwrap.dedent('''\
                #!/usr/bin/env python3
                import http.server, json, os, sys
                from pathlib import Path
                args=sys.argv[1:]
                host=args[args.index('-host')+1]
                port=int(args[args.index('-port')+1])
                app=args[args.index('-app-dir')+1]
                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        self.send_response(200 if self.path=='/health' else 404)
                        self.end_headers()
                    def log_message(self,*args): pass
                server=http.server.HTTPServer((host,port),Handler)
                Path(os.environ['GOVERNANCE_LAUNCH']).write_text(json.dumps({
                    'pid':os.getpid(),'host':host,'port':port,'app':app,
                    'config':json.loads((Path(app)/'config.json').read_text())
                }))
                if os.environ.get('GOVERNANCE_EXITED_GATEWAY') == '1':
                    sys.exit(0)
                server.serve_forever()
            '''))
            gateway.chmod(0o755)
            fake_go = binaries / "go"
            fake_go.write_text(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json,os,sys
                from pathlib import Path
                Path(os.environ['GOVERNANCE_GO']).write_text(json.dumps({
                    'argv':sys.argv[1:],'cwd':str(Path.cwd()),
                    'base_url':os.environ.get('BIFROST_TEST_BASE_URL'),
                    'workspace':os.environ.get('GOWORK')
                }))
                sys.exit(int(os.environ['GOVERNANCE_GO_STATUS']))
            '''))
            fake_go.chmod(0o755)
            if exited_gateway:
                # Simulate a stale successful probe after the owned process has
                # exited. A 200 response alone must not allow the Go suite to run.
                # Other Python calls still use the real interpreter unchanged.
                fake_python = binaries / "python3"
                fake_python.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                    import os, sys, time
                    args = sys.argv[1:]
                    if len(args) == 3 and args[0] == '-' and all(arg.isdigit() for arg in args[1:]):
                        sys.stdin.read()
                        pid = int(args[2])
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            try:
                                os.kill(pid, 0)
                            except ProcessLookupError:
                                sys.exit(0)
                            time.sleep(0.01)
                        raise SystemExit('Fixture gateway did not exit')
                    os.execv(sys.executable, [sys.executable, *args])
                '''))
                fake_python.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(binaries) + os.pathsep + env["PATH"],
                "OPENAI_API_KEY": "fixture-only", "ANTHROPIC_API_KEY": "fixture-only",
                "OPENROUTER_API_KEY": "fixture-only",
                "BIFROST_TEST_BASE_URL": "https://production.invalid",
                "GOVERNANCE_LAUNCH": str(root / "launch.json"),
                "GOVERNANCE_GO": str(root / "go.json"),
                "GOVERNANCE_GO_STATUS": str(go_status),
                "GOVERNANCE_EXITED_GATEWAY": "1" if exited_gateway else "0",
            })
            if missing_key:
                env.pop("OPENAI_API_KEY")
            result = subprocess.run(["bash", str(scripts / SCRIPT.name), str(gateway)],
                                    env=env, text=True, capture_output=True, timeout=15)
            launch_file = root / "launch.json"
            go_file = root / "go.json"
            launch = json.loads(launch_file.read_text()) if launch_file.exists() else None
            go = json.loads(go_file.read_text()) if go_file.exists() else None
            if launch:
                self.assertFalse(Path(launch["app"]).exists(), "Disposable database must be cleaned up")
                with self.assertRaises(ProcessLookupError, msg="Owned gateway must be stopped"):
                    os.kill(launch["pid"], 0)
            return result, launch, go

    def test_uses_real_go_module_and_owned_local_gateway(self):
        result, launch, go = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(launch["host"], "127.0.0.1")
        self.assertEqual(go["base_url"], f"http://127.0.0.1:{launch['port']}")
        self.assertEqual(go["workspace"], "off")
        self.assertEqual(go["argv"], ["test", "-count=1", "-timeout", "30m", "./..."])
        self.assertTrue(go["cwd"].endswith("tests/governance"))
        db = Path(launch["config"]["config_store"]["config"]["path"])
        self.assertTrue(db.is_relative_to(launch["app"]))

    def test_test_failure_propagates_and_gateway_is_cleaned_up(self):
        result, launch, go = self.run_fixture(go_status=31)
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertIsNotNone(launch)
        self.assertIsNotNone(go)

    def test_missing_credentials_fail_before_starting_gateway(self):
        result, launch, go = self.run_fixture(missing_key=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENAI_API_KEY", result.stderr)
        self.assertIsNone(launch)
        self.assertIsNone(go)

    def test_exited_gateway_after_health_never_starts_tests(self):
        result, launch, go = self.run_fixture(exited_gateway=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exited after its health check", result.stderr)
        self.assertIsNotNone(launch)
        self.assertIsNone(go, "An exited gateway must not send Go tests to a stale listener")


if __name__ == "__main__":
    unittest.main(verbosity=2)
