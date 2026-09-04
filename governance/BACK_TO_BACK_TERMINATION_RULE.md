# Back-to-back repeat termination rule

Three thresholds inform human termination decisions: three total strikes, two OLD strikes, and consecutive repetition of the same mistake. Only the human authority issues a strike, classifies its relation to prior failures, or initiates termination. A threshold detector records a finding and routes it to that authority; it never ends a deployment on its own.

An OLD strike repeats a failure documented in a predecessor's packet. A back-to-back finding concerns the same failure mechanism on consecutive recorded attempts. Similar wording or a broad label such as negligence is insufficient evidence; identify the actual failed obligation and the intervening sequence.

After the human authorizes termination, the designated custodian writes the packet, marks the deployment terminated, preserves its performance and strike records, and records the decision reference. The runtime admission mechanism, if present, revokes the named deployment according to that authorized action. A registry edit is not proof of runtime revocation.

The successor receives a new permanent identity, reads all relevant predecessor packets, and demonstrates the required corrective behavior. Test or schema enforcement should carry the lesson where possible; a promise to remember remains Told.

Use `strikes/STRIKE_LOG_TEMPLATE.md`, `terminations/TERMINATION_TEMPLATE.md`, `AUTHORITY_AND_EVIDENCE.md`, and `SKILLIFICATION_PROTOCOL.md`.
