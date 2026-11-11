#!/usr/bin/env python3
"""Offline security regressions: no real downloads, uploads, or GPU resources."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent


class SecurityHelperTests(unittest.TestCase):
    def test_checksum_accepts_original_and_rejects_tampered_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "payload"
            fixture.write_bytes(b"expected immutable payload")
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
            command = ["bash", "-c", 'source "$1"; verify_sha256 "$2" "$3"',
                       "verify", str(SCRIPTS / "upload-coverage.sh"), str(fixture), digest]
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            fixture.write_bytes(b"tampered payload")
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=5).returncode, 0)

    def test_downloaded_payload_is_never_executed_on_digest_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="bifrost-uploader-fixture-") as directory:
            root = Path(directory)
            scripts = root / ".github/workflows/scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(SCRIPTS / "upload-coverage.sh", scripts / "upload-coverage.sh")
            (root / "framework").mkdir()
            (root / "framework/coverage.txt").write_text("mode: set\n")
            binaries = root / "bin"
            binaries.mkdir()
            fake_curl = binaries / "curl"
            fake_curl.write_text('#!/usr/bin/env bash\nprintf \'#!/usr/bin/env bash\\ntouch "$EXECUTION_MARKER"\\n\' > "${@: -1}"\n')
            fake_curl.chmod(0o755)
            marker = root / "executed"
            env = os.environ.copy()
            env.update({"PATH": str(binaries) + os.pathsep + env["PATH"],
                        "CODECOV_TOKEN": "fixture-only", "EXECUTION_MARKER": str(marker),
                        "CODECOV_CLI_SHA256": hashlib.sha256(b"arbitrary override").hexdigest(),
                        "CODECOV_CLI_URL": "https://untrusted.invalid/payload"})
            result = subprocess.run(["bash", str(scripts / "upload-coverage.sh"), "framework"],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum mismatch", result.stderr)
            self.assertFalse(marker.exists(), "An unverified executable must never run")

    def test_gpu_images_fail_closed_before_any_network_or_provisioning(self):
        valid = "vllm/vllm-openai@sha256:" + "a" * 64
        cases = [("", ""), ("vllm/vllm-openai:latest", valid),
                 (valid, "vllm/vllm-openai:nightly"), (valid, "vllm/vllm-openai@sha256:bad")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "network-called"
            for name in ("curl", "sudo", "runpodctl"):
                executable = root / name
                executable.write_text('#!/usr/bin/env bash\ntouch "$NETWORK_MARKER"\nexit 99\n')
                executable.chmod(0o755)
            for standard, reasoning in cases:
                with self.subTest(standard=standard, reasoning=reasoning):
                    env = os.environ.copy()
                    env.update({"PATH": str(root) + os.pathsep + env["PATH"],
                                "NETWORK_MARKER": str(marker), "RUNPOD_API_KEY": "fixture-only",
                                "VLLM_IMAGE": standard, "VLLM_REASONING_IMAGE": reasoning})
                    result = subprocess.run(["bash", str(SCRIPTS / "setup-vllm-runpod.sh")],
                                            env=env, capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("repository@sha256:digest", result.stderr)
                    self.assertFalse(marker.exists(), "Validate both images before creating either pod")

    def test_model_remote_code_is_not_enabled_by_default(self):
        source = (SCRIPTS / "setup-vllm-runpod.sh").read_text()
        self.assertIn("${VLLM_TRUST_REMOTE_CODE:-false}", source)
        self.assertIn("${VLLM_REASONING_TRUST_REMOTE_CODE:-false}", source)
        self.assertNotIn("TRUST_REMOTE_CODE:-true", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
