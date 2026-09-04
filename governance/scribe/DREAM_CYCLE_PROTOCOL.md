# Maintenance cycle

A maintenance worker performs authorized checks of the fleet's records and observation paths. It operates with a narrow identity and read scope. A recurring trigger requires an explicit owner-approved schedule or event contract; the framework prescribes no nightly time or fixed lookback window.

For each run, reconcile changed artifacts with required completion records, knowledge files with their active references, pending events with their actual consumers, and monitors with their owning sources. Use a durable cursor or explicit source revision so repeated runs resume rather than rescan completed work unnecessarily.

A missing queue file is not automatically healthy. Check the transport's contract and actual state before reporting empty. A stored event's age is assessed against the configured requirement; inventing a stale threshold creates a false alarm or silent gap.

Record the subject revisions, checks performed, observed failures and unresolved coverage. Deliver a digest only to an authorized destination and according to the owner's notification preferences. Keep unchanged routine state quiet when that is the agreed behavior; retain a machine-readable run record regardless.

The maintenance role does not ratify rules, issue strikes, initiate terminations or take over another worker's authority. Findings route to the responsible owner with evidence. Routine in-scope repairs require their own granted capability; otherwise the run records the pending dependency.

Before relying on the worker, exercise its real trigger, successful output, interruption, retry, missed-run detection and failure reporting. A process that starts is not proof of a complete maintenance cycle. See `../MONITORING_CHAIN_INTEGRITY.md` and `../AUTHORITY_AND_EVIDENCE.md`.
