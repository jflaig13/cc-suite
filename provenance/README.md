# Release provenance

This distribution was prepared on September 4, 2026 against the CC-Suite reference implementation at commit `c90a0ed761d6eaffebd7e7d0f15fa241856fcaa0`. Its previous public baseline was `6c57e98d4055cb716ca4ca41b38b2f1950fdb123`.

- [Runtime inventory](runtime-source-inventory.json) maps reference components to included or excluded public source and records portability changes.
- [Governance inventory](governance-source-inventory.json) records the portable protocols and templates reconciled with the current operating rules.
- [Host inventory](host-source-inventory.json) records channel, relay, and launcher source adaptations.
- [Public support inventory](public-support-inventory.json) records added packaging, documentation, and regression support.
- [Root inventory](root-source-inventory.json) records the updated entry points, README, operating core, and release tooling.

Output hashes bind each inventory to the files it describes. `python tools/check_distribution.py` checks those hashes, local Markdown links, Python import closure for Fleet Kernel, and a limited set of public-distribution hazards. It supplements source review and runtime tests; it is not a security certification.

Excluded files have no public output. Private customer-bearing names may be redacted in the public inventory; the entry states that redaction and retains a reference-source digest. A source digest identifies a revision without exposing its contents.

Portability adaptations replace deployment-specific bindings with explicit configuration, preserve authority refusals, and correct documented compatibility defects. The runtime guide describes the verified public behavior and prerequisites for adopting deployment-bound components.
