#!/usr/bin/env python3
"""Exercise the real egress guard with small, isolated Postman collections."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("check-egress-allowlist.sh")
CALLBACK = "https://harness.example/cb"
MCP_URL = "https://mcp.deepwiki.com/mcp"


def request_item(url, *, method="POST", body=None, **extra):
    request = {"method": method, "url": url}
    if body is not None:
        request["body"] = {"mode": "raw", "raw": json.dumps(body)}
    return {"name": "fixture", "request": request, **extra}


class EgressContextTests(unittest.TestCase):
    def check(self, items, *, config=None, allowed=()):
        with tempfile.TemporaryDirectory(prefix="bifrost-egress-test-") as tmp:
            root = Path(tmp)
            script = root / ".github/workflows/scripts/check-egress-allowlist.sh"
            script.parent.mkdir(parents=True)
            shutil.copyfile(SCRIPT, script)
            collection = root / "tests/e2e/api/collections/provider-harness.json"
            collection.parent.mkdir(parents=True)
            collection.write_text(json.dumps({"item": items}))
            provider_config = root / "tests/integrations/python/config.json"
            provider_config.parent.mkdir(parents=True)
            provider_config.write_text(json.dumps(config or {}))
            workflow = script.parent.parent / "fixture.yml"
            workflow.write_text(
                "jobs:\n  harness:\n    steps:\n"
                "      - uses: step-security/harden-runner@fixture\n"
                "        with:\n          egress-policy: block\n"
                f"          allowed-endpoints: {' '.join(allowed)!r}\n"
                "      - run: bash .github/workflows/scripts/test-provider-harness.sh\n"
            )
            return subprocess.run(
                ["bash", str(script), str(workflow)],
                text=True, capture_output=True, check=False,
            )

    def assert_passes(self, items, **kwargs):
        result = self.check(items, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_blocks(self, items, host, **kwargs):
        result = self.check(items, **kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(host, result.stderr)

    def test_documentation_metadata_and_comment_lines_are_not_dialled(self):
        item = request_item("{{baseUrl}}/v1/chat/completions")
        item["description"] = "Docs: https://ai.google.dev/gemini-api/docs/media-resolution"
        item["event"] = [{"script": {"exec": [
            "  // Docs: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/prompt-caching",
        ]}}]
        self.assert_passes([item])

    def test_same_hosts_as_runtime_targets_still_require_allowlisting(self):
        for url in ("https://ai.google.dev/runtime", "https://learn.microsoft.com/runtime",
                    CALLBACK, MCP_URL):
            with self.subTest(url=url):
                host = url.split("/")[2]
                self.assert_blocks([request_item(url)], host)
                self.assert_blocks([request_item("{{baseUrl}}/v1/responses", body={
                    "input": [{"type": "input_image", "image_url": url}],
                })], host)

    def test_documentation_host_in_executable_script_still_requires_allowlisting(self):
        for script in (
            'pm.sendRequest("https://learn.microsoft.com/runtime");',
            '// Docs: https://ai.google.dev/docs\npm.sendRequest("https://ai.google.dev/runtime");',
        ):
            with self.subTest(script=script):
                item = request_item("{{baseUrl}}/v1/responses")
                item["event"] = [{"script": {"exec": [script]}}]
                self.assert_blocks([item], "microsoft.com" if "microsoft" in script else "ai.google.dev")

    def test_provider_side_mcp_reference_is_limited_to_anthropic_field(self):
        self.assert_passes([request_item("{{baseUrl}}/anthropic/v1/messages", body={
            "mcp_servers": [{"type": "url", "url": MCP_URL}],
        })])
        self.assert_blocks([request_item("{{baseUrl}}/mcp/clients", body={
            "mcp_servers": [{"type": "url", "url": MCP_URL}],
        })], "mcp.deepwiki.com")
        self.assert_blocks([request_item("{{baseUrl}}/anthropic/v1/messages", body={
            "mcp_servers": [{"type": "url", "url": MCP_URL}],
            "image_url": MCP_URL,
        })], "mcp.deepwiki.com")

    def test_oauth_registration_fixture_is_limited_to_redirect_uri_field(self):
        self.assert_passes([request_item("{{baseUrl}}/oauth2/register", body={
            "redirect_uris": [CALLBACK],
        })])
        self.assert_blocks([request_item("{{baseUrl}}/v1/responses", body={
            "redirect_uris": [CALLBACK],
        })], "harness.example")
        self.assert_blocks([request_item("{{baseUrl}}/oauth2/register", body={
            "redirect_uris": [CALLBACK], "logo_uri": CALLBACK,
        })], "harness.example")

    def test_oauth_authorization_fixture_requires_redirects_disabled(self):
        url = {"raw": f"{{{{baseUrl}}}}/oauth2/authorize?redirect_uri={CALLBACK}&client_id=fixture",
               "query": [{"key": "redirect_uri", "value": CALLBACK}]}
        self.assert_passes([request_item(url, method="GET", protocolProfileBehavior={"followRedirects": False})])
        for behavior in ({}, {"followRedirects": True}):
            with self.subTest(behavior=behavior):
                self.assert_blocks([request_item(url, method="GET", protocolProfileBehavior=behavior)],
                                   "harness.example")
        self.assert_blocks([request_item(
            f"{{{{baseUrl}}}}/oauth2/authorize?redirect_uri={CALLBACK}/other",
            method="GET", protocolProfileBehavior={"followRedirects": False},
        )], "harness.example")

    def test_provider_config_is_never_classified_as_collection_metadata(self):
        self.assert_blocks([], "ai.google.dev", config={"description": "https://ai.google.dev/runtime"})

    def test_literal_host_with_templated_path_is_still_checked(self):
        self.assert_blocks([request_item("https://files.fixture.test/{{file_id}}")], "files.fixture.test")
        self.assert_passes([request_item("https://files.fixture.test/{{file_id}}")],
                           allowed=("files.fixture.test:443",))


if __name__ == "__main__":
    unittest.main()
