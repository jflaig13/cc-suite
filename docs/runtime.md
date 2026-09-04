# Runtime source, example, and deployment boundary

This is the current reusable Company runtime exported from source commit `c90a0ed761d6eaffebd7e7d0f15fa241856fcaa0`. It contains the implementation used to define Company identities, retain work and evidence, separate authorities, supervise admitted processes, and recover interrupted effects. It is a source release with runnable library examples. Installing the Python package does **not** install or activate a live Company fleet.

The source inventory records included, adapted, and excluded components. Excluded customer-specific filenames are redacted in the public inventory; their source hashes and exclusion reasons remain. Private customer operations, authentication material, live process identities, signed founder instructions, historical admission repairs, and operating receipts are not distributed.

## Included implementation

| Component | Source | Behavior |
| --- | --- | --- |
| Identity | `fleet_kernel/identity.py` | Content-addressed role releases, manifests, workers, sessions, model/effort vocabulary, capabilities |
| Work and review | `company_workspace.py`, `company_workspace_api.py` | Versioned objectives, append-only events, isolated principals, signed exact-subject reviews, completion gate |
| Durable messaging | `company_message_plane/` and Company SQL | Send/claim/consume RPCs, sequence and byte proofs, admission requirements, lease and retry state |
| Admission and supervision | `company_scribe_admission.py`, `company_scribe_supervisor.py`, `company_role_runtime.py` | Exact packages, process/host identity, singleton locks, lease lineage, revocation and recovery |
| Effects | `scripts/company_scribe_effect_broker.py` | Authority checks around filesystem/command effects, durable mutation journal, idempotent retries and crash recovery |
| Shared state and evidence | `schemas.py`, `evidence_keys.py`, `m2`–`m8` | Versioned records, ownership leases, durable outbox, handoff/approval/spend/queue state, signed evidence, isolation |
| Evaluation | `eval_plane/` | Test-case/suite definitions, graders, holdout isolation, effect constraints and qualification artifacts |
| Channels and host adapters | `channels/`, `scripts/` | Current reusable channel and host mechanics with explicit deployment configuration |

Protocol and PostgreSQL object names retain their historical `mise` namespace where renaming would change identity, signatures, or referential integrity. These names grant no access to a Mise installation.

## Install and run the synthetic workflow

The library requires Python 3.11 or newer and PostgreSQL for its database workflows. Core runtime tests also use Git. The complete release suite additionally requires Bash, zsh, and Bun 1.3.12. The example creates a disposable, owner-only Unix-socket database and destroys it afterward. It never reads a database URL from the environment, calls a model, sends a message outside that database, opens a browser, or installs a service.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
export CC_SUITE_COMPANY_ID=example-company
export CC_SUITE_SCRIBE_DEPLOYMENT_ID=Scribe-Example
export CC_SUITE_TEST_PG_BIN="$(pg_config --bindir)"
python -m fleet_kernel.example
python -m pytest -q tests/runtime
```

Set `CC_SUITE_TEST_PG_BIN` directly if PostgreSQL's tools are not on `PATH`. Missing PostgreSQL fails the runtime suite; database tests are not silently skipped. PostgreSQL's `initdb` must run as a non-root user. Run `python -m pytest -q tests` for the complete assembled suite, including host and generated-launcher checks; install Bash, zsh, and Bun 1.3.12 first so those checks can run fully.

`CC_SUITE_TEST_STATE_ROOT` optionally selects an **existing absolute parent directory** for synthetic pytest state; otherwise the parent is `HOME`. Every ancestor must be a real directory owned by the current user or root, with no group/world writes or symlink components. The fixture creates a unique owner-only child with the process UID/GID, uses it for `tmp_path`/`tmp_path_factory`, and cleans it up at session end. It does not relocate Python's general temporary directory, because the disposable PostgreSQL example needs short Unix-socket paths.

Choose a private local cache or other unsynced directory outside any workspace whose tools capture, synchronize, or hard-link files. The same exclusive-storage requirement applies to real Scribe authority, journal, and private-object state. During a paired synthetic filesystem check, an inert workspace file gained a second hard link while an equivalent external-cache file retained one; the responsible process was not identified. The unchanged source passed with test state outside the monitored workspace. The broker's single-link, ownership, mode, and ancestry guards remain intact: relocating test fixtures does not relax those checks.

The example uses the real Company API, database triggers, registered synthetic signing keys, and receipt path. It creates an objective, moves it to review, signs the exact work subject using the configured reviewer identity, records the review, completes the objective, restarts PostgreSQL, and verifies unchanged durable state. The signature is generated by a synthetic test reviewer; the example makes no claim that a language model performed a substantive review.

Tests cover current reviewer compatibility, signature/subject/model/worker mismatches, missing admissions, stale versions, narrow principal privileges, history immutability, concurrent review, database restart, signed authority checks, local broker idempotency and recovery, and refused host-install replacement. They do not certify an adopter's live deployment.

## Configuration

Set `CC_SUITE_COMPANY_ID` and `CC_SUITE_SCRIBE_DEPLOYMENT_ID` explicitly **before importing** Company runtime modules. Use 1–128 ASCII letters, digits, dots, underscores, or hyphens, beginning with a letter or digit. Identity is fixed for the lifetime of a process; restart after changing deployment configuration.

Database consumers require an explicit connection argument or the documented native connection variable. `CompanyMessagePlaneClient` accepts a connection string directly, or uses `FLEET_KERNEL_DDL_DATABASE_URL` / an owner-only file named by `FLEET_KERNEL_DDL_DATABASE_URL_FILE`. It does not fall back to an author's home-directory credential file. Use separate credentials for serving, worker, reviewer, attestor, signer, and provisioning roles; a prompt does not grant any of them.

`fleet_kernel.database.install_schema(conn)` installs the exported shared/Company SQL into an **empty, dedicated** database, in one transaction. It does not select a database or enroll workers. Deployment identity is substituted explicitly. Unconfigured authority bindings retain an unmistakable `UNCONFIGURED_...` sentinel in database predicates and `None` in Python; they cannot satisfy a native activation contract.

The optional legacy message-plane bridge requires `CC_SUITE_ENABLE_KERNEL_BRIDGE=1`. Its database setter and relay use the same `kernel_cutover.json` projection under `CC_SUITE_CHANNEL_SHARED_DIR`, which must name an existing absolute directory without symlinks. Missing configuration refuses the setter before a database write; relay fallback defaults to inactive. Local writers serialize their database operation through atomic projection so a delayed read cannot overwrite a newer rollback. The database remains canonical: if projection fails after commit, the setter reports the partial result and requires an idempotent retry before relying on fallback. Relays cache state for up to five seconds; offline projections are snapshots, not proof of current admission. This preserves an optional migration bridge, not a completed live cutover or an authorization mechanism.

The local projection lock stays held during the database operation. An unavailable database can delay other local readers and setters for the connection attempt (configured with a 15-second connect timeout); a stalled query can hold it longer. Account for this serialization when configuring the optional bridge. A successful canonical read remains usable even if its local projection cannot be written.

### Workspace API

`fleet_kernel.company_workspace_api.create_company_workspace_app()` creates an ASGI application; it does not start a server or select a listening address. Host it within the adopter's protected operator interface, intended for loopback/internal use.

| Variable or argument | Behavior |
| --- | --- |
| `COMPANY_WORKSPACE_ACCESS_TOKEN` | Required bearer token unless the factory receives an explicit `access_token=` argument. Missing configuration refuses app creation; missing or incorrect request credentials return HTTP 401. |
| `COMPANY_WORKSPACE_DATABASE_URL` | Required PostgreSQL connection configuration when the factory constructs its own `CompanyWorkspace`. An injected `service=` supplies the configured service instead. |

The API token protects operator access; its write routes act as `founder` in the Company workspace. It is not a per-agent identity or a replacement for signed reviews, database principal isolation, or live admission. Keep the token and database authentication material in the adopter's secret configuration, never in role MCP JSON or model-child passthrough variables. Database operations still require the provisioned Company principal and explicit password or client-certificate/key authentication. An injected service can provide the separate credential lookups supported by `CompanyWorkspace`; configuring a URL alone does not provision identities, grants, keys, or approvals.

### Handoff and shared shadow adapters

`CC_SUITE_FOUNDER_IDENTITIES` is an optional comma-separated list of owner-name aliases for the handoff ledger. Values are stripped and compared case-insensitively; `founder` is always recognized. Set it before importing `scripts.handoff_ledger` (restart to change it). It classifies founder-owned pending decisions so they are excluded from handoff stall escalation. These aliases do **not** authenticate anyone or grant founder permissions.

| Variable | Consumer and requirement |
| --- | --- |
| `FLEET_KERNEL_HANDOFF_READ_MODE` | Handoff ledger: `legacy` by default; `kernel` opts the configured canonical ledger into the native kernel read/mirror path. Other values refuse. |
| `FLEET_KERNEL_DATABASE_URL` | Explicit PostgreSQL configuration for kernel handoff reads/mirroring and the M5 shadow sync command. No database is discovered implicitly. |
| `FLEET_KERNEL_TENANT_ID` | Explicit shared-state namespace for those adapters; required alongside their database configuration. It does not enroll a customer or create a fleet. |
| `MISE_TENANT` | A nonempty value is refused by these preserved pre-tenant cutover paths. It is not a switch for activating restaurant support. |
| `FLEET_KERNEL_M5_GAP_ROOT` | Explicit absolute directory for M5 dispatch-gap markers when using the shared dispatch read adapter. |

The handoff adapter uses the workspace and bus paths from the [host configuration](host-runtime.md). Kernel mode applies to its configured canonical ledger; a caller-supplied different path remains on the legacy path. Native writers commit the legacy ledger first and synchronously mirror it when enabled, reporting mirror failure after the legacy write. This is the preserved migration behavior, not a claim of a completed live cutover.

`scripts/sync_kernel_dispatch_approval_shadow.py` performs one M5 shadow synchronization cycle. It requires `--dispatch-intents`, `--fodl-audit`, and `--fodl-current` paths plus the database and namespace variables above; `--cycle-key` is optional. `--validate-config-only` checks the required environment, the `MISE_TENANT` refusal, and connection-string syntax. It does not connect to PostgreSQL, inspect those source files, verify grants/schema/parity, or prove that a real sync will succeed. Without that flag, the command reads the three stable legacy surfaces and updates/checks their kernel shadow state. It does not dispatch the described operational work or activate a fleet.

## Live admission prerequisites

A working library example is not a live worker activation. A real deployment needs all of the following:

1. An adopter-owned workspace and separate exclusive local state/bus directories, with the native filesystem ownership and isolation requirements. Keep authority and recovery state away from syncing or hard-linking workspace capture tools.
2. A dedicated initialized PostgreSQL database, registered Company role identities and keys, separated database principals, manifests, and the required current worker/session records.
3. A clean, committed source checkout and verified immutable package. Package construction and held installation are distinct from admission.
4. Adopter-owned authority artifacts and exact accepted bindings. `CC_SUITE_AUTHORITY_BINDINGS_FILE` may name an absolute, non-symlink JSON mapping for the native authority metadata, issuer, artifact hashes, and deployment identifiers. No real or sample activation approval is bundled. The preserved validators require exact artifact bytes, identity, scope, and predecessor/recovery evidence; arbitrary JSON values do not authorize work.
5. An installed, authenticated, admitted model host and explicit executable/configuration paths. Source schemas preserve current native host constraints; this release does not invent a replacement authority contract or support retired host doors.
6. A supervisor-created current admission and transaction binding. The effect broker and database recheck that authority. Restart recovery requires its exact prior lineage and receipts; copying a state file or setting an `ADMITTED` environment flag is insufficient.

Where the governance hierarchy names search policy or agent policy, the applicable policy comes from the host and adopting organization. This package does not ship deployment-specific policies or grant permissions through those names.

Historical restaurant/Mac-mini activation APIs remain source-compatible types and validation logic where shared Company admission imports them. Their private domain integrations and authority history are excluded. They are not an independently supported restaurant deployment path in this public package. The published example does not bypass these conditions or create a live admission.

For the native supervisor's explicit arguments, use:

```sh
python -m scripts.company_scribe_supervisor --help
python -m scripts.company_scribe_supervisor build-package --help
python -m scripts.company_scribe_supervisor install-package --help
python -m scripts.company_scribe_supervisor prepare-initial --help
python -m scripts.company_scribe_supervisor admit --help
```

`install-supervisor` requires an explicit `--plist` and `--label`. It publishes a new plist without overwriting an existing target. Replacement requires `--replace-existing`; loading a job requires the separate `--bootstrap` argument. No service installation or live admission is part of the example/tests.

## Native Scribe cold package

The preserved native Scribe cold-runtime builder is specific to its reviewed **macOS Python 3.14 framework layout and Bun 1.3.12 executable**. The broader Python library is portable; this native packaging path is not a universal installer. It deliberately refuses an unreviewed executable, missing host tooling, a dirty source checkout, or mismatched source/lock/bundle bytes.

The committed Scribe JavaScript bundle is derived from the source and lockfile. Rebuild with the repository's Scribe package scripts, using Bun 1.3.12, and refresh the frozen manifest only after reviewing the changed inputs. To verify the checked-in linkage and reproduce the bundle from closed inputs without opening a channel:

```sh
python -c 'from pathlib import Path; from fleet_kernel.company_scribe_admission import _verify_frozen_channel_bundle; print(_verify_frozen_channel_bundle(Path.cwd()))'
```

This check requires the same reviewed Bun executable as the native builder. The core Python suite in `tests/runtime` does not require Bun. The complete release suite in `tests` also exercises host/channel boundaries and requires Bash, zsh, and Bun 1.3.12 to run fully. The channel adapters require explicit paths and an admitted runtime; standalone invocation without those bindings refuses before channel effects.

## Documented export modifications

The public copy preserves the current implementation while replacing private installation paths, Company/deployment IDs, and signed founder metadata with explicit configuration. The public release inventory replaces the original package inventory's dependency on a restaurant payroll release. It excludes the data-only historical admission repair and extracts the current shared runtime-worker validator from the adjacent restaurant migration.

A narrow compatibility fix is also included: the pinned source's Company identity declares the current CCPO model as `claude-fable-5-1`, while its workspace signature, public-key lookup, receipt, and MAC paths still assumed `claude-fable-5`. The public copy binds all those paths to the registered reviewer's admitted model and requires the receipt's model to match that immutable identity. Current-model review/completion passes; authentic signatures with a mismatched model, changed subject/output, unregistered worker, or unadmitted model are rejected. This fix is recorded against the pinned source in the provenance inventory; the private source repository was not changed.


The same current-role model lookup replaces stale Scribe launch defaults. Nonsecret Company identity is passed into isolated child/proxy processes so imports resolve consistently; authority binding paths and database credentials are not forwarded to cognition. Trusted supervisors retain their explicit authority configuration.

Evaluation interception preserves the native in-process patching, redaction, evidence ledger, simulation and uninstall algorithms. Its original private product adapter registry is excluded. An empty registry refuses; callers must provide explicit `ChokepointSpec` objects and verify every relevant effect path. These controls do not constrain a raw socket, shell, or other process: production credentials must be absent and external egress denied by the worker boundary. `TEST_ACCOUNT`, `SHADOW`, and `SUPERVISED_LIVE` remain defined but unimplemented modes.

The shared migration adapters retain consistency/rollback algorithms with explicit configuration and caller-supplied source surfaces; private operational input files are excluded.

Native legacy channel/host setup additionally requires adopter-specific role startup instructions and a policy authorizer; see [host runtime configuration](host-runtime.md). The source release inventory includes channel/host assets used by cold packaging. Python wheels contain the library and schema; build native cold packages from a clean source checkout, not an installed wheel.
