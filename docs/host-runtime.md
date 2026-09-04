# Exported host and channel mechanics

This is reusable source from revision `c90a0ed761d6eaffebd7e7d0f15fa241856fcaa0`, with explicit portability adaptations. It is not an installed or admitted fleet. The native Company/Scribe supervisors retain their exact package, identity, lineage, effect-broker, and admission checks. An adopting organization supplies its own governance, workspace, authority, interpreters, model access, and deployment configuration.

## Included mechanics

- Closed Scribe MCP protocol, brokered send/reply, durable delivery journal, inbox cursors, restart checkpoint behavior, send receipts, exact source/lock/bundle manifest, and its native package launcher.
- Generic Company channel with push, pending/reply, sends, arbitration, FODL, durable queues and receipts; Python relay with the corresponding HTTP/tool interface and cursor primitives.
- Company-only role MCP templates, source role/model routing and contract checks, host and legacy-lineage gates, authentication-variable scrub, explicit-config Claude launcher, and macOS Terminal fleet launcher.
- Optional handoff/FODL and Company message-plane adapters using the package's M4/M5/message-plane code. Their file/DB authority contracts remain those of the native implementations.

Only the configured Company workspace and state/bus paths are used. Source dependency discovery does not search private checkouts, home-state defaults, or ancestor virtual environments. Restaurant fleet spawning and destination aliases are outside this export.

## Required deployment configuration

Identity values must match the adopter's configured Company and Scribe deployment. Workspace, state, bus and authorizer paths must be canonical absolute paths. Executables must be explicit absolute paths. No credentials belong in these settings or in role MCP JSON.

| Variable | Purpose |
| --- | --- |
| `CC_SUITE_COMPANY_ID` | Explicit Company identifier, required before host-gate imports. |
| `CC_SUITE_SCRIBE_DEPLOYMENT_ID` | Explicit Scribe deployment identifier for the Company runtime, also required for other Company roles. |
| `CC_SUITE_PACKAGE_ROOT` | Reviewed installed payload containing `scripts`, `channels`, and `mcp_servers`; native role runtime supplies this from its verified package. |
| `CC_SUITE_WORKSPACE` | Adopter's mutable workspace and governance/initialization context. |
| `CC_SUITE_STATE_ROOT` | Parent for per-role runtime state. |
| `CC_SUITE_CHANNEL_SHARED_DIR` | Explicit Company message bus. |
| `CC_SUITE_PYTHON` | Explicit Python entry path with package dependencies installed; preserve the virtual-environment path (such as `.venv/bin/python`) instead of resolving it to the base interpreter. |
| `CC_SUITE_BUN` | Explicit Bun executable for the generic channel wrapper. |
| `CC_SUITE_HOST_AUTHORIZER` | Executable adopter policy adapter described below. It is a path, not a credential. |
| `CC_SUITE_CLAUDE` | Explicit Claude executable for the legacy interactive launcher. |
| `CC_SUITE_PERMISSION_MODE` | Explicit host permission policy; no automatic consent actions are exported. |

Both identity values must contain 1–128 ASCII letters, digits, dots, underscores or hyphens, beginning with a letter or digit. Missing or invalid identities return `REFUSED` with exit 78 before host-gate imports, generated command writes, or host launch. The shared shell guard exports validated identities; fleet command files preserve them for a fresh Terminal environment. These identifiers are nonsecret configuration and grant no admission authority.

`launch_channel.sh` derives `CC_SUITE_CHANNEL_STATE_DIR` as `$CC_SUITE_STATE_ROOT/company-<role>/channel`; push/relay code rejects a different role's state directory. Role templates retain native `<role>-channel-push` server keys and source port allocation. Generic channels require explicit role and port; no default role is chosen. Scribe never starts through a generic channel or legacy launcher: its supervisor generates the admitted brokered channel configuration and supplies native Scribe environment bindings.

The native runtime's child environment must pass the required nonsecret variables while binding package/workspace itself. Database credentials and authority secrets must remain in the native broker/deployment boundary; do not add them to a model-child passthrough allowlist to make an optional adapter work.

The legacy Claude launcher accepts an explicit initial command/prompt as additional arguments. If none is supplied, it requires the adopter's `.claude/commands/init-<role>.md` in the configured workspace and refuses if absent. The generic `init-role.md` and governance templates do not invent admitted identities or fill this prerequisite automatically. No role contracts or founder mandates are bundled by this host lane.

Headless legacy launch additionally requires an explicit prompt file and `AUTONOMY_RUN_BUDGET_USD`. Optional `CC_SUITE_ENABLE_CHROME=1` selects the browser flag; no browser is activated by default. Source role-routing overrides still require their matching routing contract and retain verification-seat restrictions.

## Adopter authorization seam

Before generic channel startup or legacy host actions, `portable_host.py` invokes the configured adapter with:

```text
--role ROLE --operation OPERATION --workspace PATH --state-root PATH --channel-shared-dir PATH
```

Operations are `channel-push`, `channel-relay`, `launch-claude`, `fleet-spawn`, and the optional `slack-ack`. The adapter must exit successfully within ten seconds and return one JSON object with `authorized` set to the JSON boolean `true` and matching `role`, `operation`, `workspace`, `state_root`, and `channel_shared_dir` fields. A missing adapter, failed process, malformed JSON, or mismatched subject refuses. This is an explicit organization-supplied policy integration, not a new substitute for native admission or a shipped grant of authority. The included tests use temporary synthetic receipts and never install an approving adapter.

## Optional integrations and exclusions

`CC_SUITE_ENABLE_KERNEL_BRIDGE=1` opts into the legacy Company message-plane bridge. It is off by default, so a fresh package does not discover a database or live cutover mirror. With explicit deployment configuration, the source bridge retains its file-bus-first, best-effort shadow/migration behavior; this export does not claim it is a completed production cutover. M4 handoff and M5 FODL projection adapters also require their explicit native database/tenant/pointer configuration.

`CC_SUITE_ENABLE_SLACK_BRIDGE=1` additionally requires a separately supplied `mcp_servers.slack_bridge.client` and an authorization decision for `slack-ack`. That private transport, Slack credentials/registries, live poller and channels are excluded. The export does not install project prompt/Stop hooks. Native push uses the Bun server; relay-only sessions explicitly call pending/drain unless the adopter configures a separate prompt hook.

Also excluded: actual queue/cursor/audit data, customer records, live kernel cutover files, database credentials, native service installations, launchd plists, automatic UI consent/key presses, automatic process killing, source private environments, historical live migration driver `admit_company_role_cutover.py`, and the retired missing Grok launcher. No included script installs a service or activates a fleet by being imported for local verification.

## Local verification

Use Bun **1.3.12**. Each channel owns its actual module entry, declared dependencies, exact lock, and these scripts. From each of `channels/scribe` and `channels/agent`:

```sh
bun install --frozen-lockfile --ignore-scripts
bun run typecheck
bun run build
bun run test
```

Scribe uses TypeScript 5.9.3 and `@types/bun` 1.3.11 for development. The generic channel also declares `@modelcontextprotocol/sdk` 1.29.0. Runtime launch uses the compiled generic `channel.bundle.js`, including its SDK dependency, so no `node_modules` tree or automatic package install is needed to start a reviewed payload. Bun runs and bundles the channels; a separate Node runtime is not required by these commands. The checked-in Scribe bundle must match every manifest input, its bundle hash and the canonical manifest link hash. Rebuild with the manifest's exact command and refresh that manifest only as part of a reviewed source change.

With package Python dependencies, pytest, Bash, zsh, and Bun 1.3.12 installed, run from the package root. Use the [secure test-state configuration](runtime.md#install-and-run-the-synthetic-workflow) when the default HOME is inside a captured or synced workspace:

```sh
python -m pytest tests/test_portable_host_boundary.py tests/test_tier1_channel_relay_durable_inbox.py tests/test_tier1_durable_inbox_cursor_seed.py -q
```

These checks use synthetic temporary state: authority receipt acceptance/refusal, exact role-state ownership, offline Scribe import, wrong-parent rejection, legacy recovery fences, queue durability/cursors, cold-start replay, explicit identity refusal, and generated-command identity propagation through the real host gate without launching a host. The Bun suites cover delivery identity/lifecycle, send receipts, cursor recovery, arbitration and advisor recognition. The boundary suite executes only refusal/offline channel paths and never starts a listening service. Test fixtures contain no live organization data. Python relay runtime dependencies include `mcp` and `httpx`; native projection adapters use `psycopg` and the package's `fleet_kernel` modules.

`provenance/host-cold-verification.json` records the **initial staged host-only run**, performed with Python 3.14.0, pytest 8.4.2, and Bun 1.3.12. It is historical evidence for those staged inputs, including fresh dependency installation, typechecks/builds, byte-identical bundles, manifest hashes, and shell syntax; it is not a claim about the final assembled test environment. Final assembled-package verification uses Python 3.11 and the repository-pinned pytest 9.1.1, with explicit secure test state outside the monitored workspace. Current test totals and outcomes belong to the evidence for the exact reviewed revision and CI run, rather than a fixed number in this guide. Host-only checks do not write to a database; the full assembled suite and library example deliberately use disposable local PostgreSQL state. No real Claude host, Terminal fleet, external transport, service installation, or production activation is exercised by these checks.
