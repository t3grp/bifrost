# CI and approval boundaries

## Routine checks

`basic-ci.yml` runs on ordinary pull requests, pushes to `dev` and `main`, a
nightly schedule, and manual dispatch. It builds the core, runs core unit and
local MCP/mock tests, and runs the provider-harness runner's local unit tests.
These jobs use GitHub-hosted runners, a read-only repository token, checkout
without persisted credentials, and blocked egress with dependency-download
destinations. They do not receive provider/cloud secrets or use an environment.

`workflow-lint.yml` also runs for ordinary pull requests. It checks egress
configuration and the regression tests for the CI security boundaries.

The shared `scripts/test-core-unit.sh` excludes the live LLM test package and
packages whose tests import it. Package-discovery errors and an empty selection
fail the job. New live-provider tests must remain outside this automatic unit
test path; do not add provider credentials to Basic CI to make a test pass.

## Protected live tests

`pr-tests.yml` retains the **PR Tests (Requires Approval)** name. It admits
manual dispatches and same-repository pull requests carrying the `release`
label. It no longer runs on a timer. Its preflight keeps the `pr-testing`
approval environment but has no provider secrets or PR-write permission.

After preflight, the reusable `full-test-suite.yml` runs the existing live
provider, SDK integration, benchmark, and component suites. Callers map each
secret explicitly; `secrets: inherit` is not used. Every secret-consuming job
declares `pr-testing` directly, so the protection applies where credentials are
used. Own-repository checkouts use the same reviewed PR head SHA or event SHA.

The PR workflow then runs the real Go module in `tests/governance`, using the
gateway artifact built by that same workflow run. The runner starts its own
gateway on loopback with a temporary configuration and SQLite database, replaces
any externally supplied test base URL, and cleans up its process and state. Only
the OpenAI, Anthropic, and OpenRouter test credentials are passed to this job.
Python and TypeScript SDK tests remain in the existing reusable integration job.

The release pipeline also calls this reusable suite. Its secret-consuming test
jobs therefore require `pr-testing` approval before release processing can
continue. Existing release result checks, publishing jobs, and their permission
settings are retained. Environment reviewer/settings changes are not part of
this configuration repair.

An environment approval permits the reviewed code and its dependencies to run
with that job's credentials. Keep `pr-testing` credentials restricted to test
accounts, test resources, and appropriate spending limits. A secret name alone
does not establish its account, scope, or whether it is populated. Environment
secrets with matching names can take precedence over secrets passed by a caller;
review the environment's inventory as well as repository/organization secrets.

## Optional GPU tests

RunPod/vLLM tests are disabled by default in both callers and the reusable
workflow. Enabling them requires a manual dispatch with `run-vllm-tests` set to
true and both `vllm-image` and `vllm-reasoning-image` supplied as immutable
`repository@sha256:<64 lowercase hex characters>` references. Invalid or missing
references fail before downloading tools or provisioning pods.

GPU tests use paid external infrastructure and live model services. Review both
images and the RunPod/test account before opting in. Model remote-code loading
defaults to false. Image digests do not pin model repository revisions; an
explicit remote-code opt-in requires a separate review of that model code.
Existing pod cleanup and the 120-minute backstop remain in place.

## Downloaded tools and coverage

Benchmark code is pinned to a reviewed commit. Docker jobs use the runner's
Compose plugin instead of downloading a mutable latest binary. Coverage is
uploaded in a separate step with a version-pinned, SHA-256-verified Codecov CLI;
test commands do not receive the upload token. Steps in one job still share a
runner, so this scope reduction does not isolate the token from malicious code
executed earlier in the same job. The approval gate remains necessary.

## Local validation

Use the repository's Go and Node versions. These checks do not need provider
credentials or an existing Bifrost deployment:

```bash
source .github/workflows/scripts/setup-go-workspace.sh
(cd core && go build ./...)
make setup-mcp-tests USE_NODE=:
bash .github/workflows/scripts/test-core-unit.sh
make test-harness-runner-lib USE_NODE=:

python3 .github/workflows/scripts/check-egress-allowlist.test.py
python3 .github/workflows/scripts/test-core-unit.test.py
python3 .github/workflows/scripts/ci-policy.test.py
python3 .github/workflows/scripts/ci-security-helpers.test.py
python3 .github/workflows/scripts/test-governance.test.py
bash .github/workflows/scripts/check-egress-allowlist.sh
bash .github/workflows/scripts/version-format.test.sh
```

The Python policy/egress checks use PyYAML, as installed by Workflow Lint. The
governance runner regressions use a local fake gateway and fake Go command; they
do not run live governance/provider tests. For workflow syntax, run actionlint
against the changed workflow files.

Changes to workflows do not modify already-created runs. Review the exact
commit and configuration of any pending run before approving it. Successful
preflight or configuration tests do not establish that the protected live tests
have executed or passed.
