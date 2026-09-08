# Third-party notices

The generated agent bundle incorporates the following components. Their
copyrights and license conditions remain with their respective rightsholders.
The complete notices are stored in `LICENSES/third-party/` and embedded in
`channels/agent/channel.bundle.js` by the build process.

| Component | Version | License |
|---|---|---|
| @modelcontextprotocol/sdk | 1.29.0 | MIT |
| ajv | 8.18.0 | MIT |
| ajv-formats | 3.0.1 | MIT |
| fast-deep-equal | 3.1.3 | MIT |
| fast-uri | 3.1.0 | BSD-3-Clause |
| json-schema-traverse | 1.0.0 | MIT |
| zod | 4.3.6 | MIT |
| zod-to-json-schema | 3.25.2 | ISC |

The [component manifest](provenance/third-party-licenses.json) records exact
versions, upstream sources, archive integrity and license-text hashes.
The [license texts](LICENSES/third-party/) contain the complete upstream
copyright notices, permissions, conditions and disclaimers.

The Scribe bundle contains no identified copied npm dependency code. Python
dependencies and development packages installed separately are not bundled
into these JavaScript files and retain their own licenses. This inventory
covers the committed bundles, not every possible installed environment.

Run `python tools/license_bundles.py --check` to verify the bundle notices
against the checked-in manifest, lockfiles and license texts. An unknown
bundled package or a changed license basis requires review before updating
the manifest. Rebuilding must preserve the complete notices.
