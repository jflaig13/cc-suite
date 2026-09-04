# Provisional configuration

Intermediate package pins, admission records, cutover receipts and evaluation freezes describe what was tested and provide recovery evidence. They do not prevent an authorized owner from requesting a change.

Until the owner explicitly declares the named surface and version final, treat configuration as provisional. Implement authorized revisions, rebuild affected packages, refresh applicable admission or deployment records, and verify the resulting behavior. Do not ask again for each step of an already-authorized sequence.

An exact review subject stays frozen while it is reviewed. A requested implementation change produces a new subject; affected evidence is refreshed. This preserves traceability while allowing revision.

Reconfiguration retains protection for credentials, tenant data, consequential effects and source history. Never invent authority or waive a required financial or permission boundary. A final-activation declaration is a status decision about a named surface; it does not authorize unrelated work or turn obsolete evidence into current proof.
