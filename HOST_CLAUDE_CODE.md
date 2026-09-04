# CC-Suite: Claude Code host adapter

Start by reading [HARNESS_CORE.md](HARNESS_CORE.md), the active role definition, and the task's relevant domain sources. The shared operating rules govern every host; this adapter describes entry and continuity for Claude Code.

Use the installed host's supported capabilities. Confirm the actual model, permissions, available tools, and workspace. Do not infer that a hook, channel, browser session, background worker, or command is active because its configuration file exists.

The generic [init-role command](.claude/commands/init-role.md) loads a role and its recorded context. It does not install a service, issue credentials, admit a runtime identity, or expand authority.

If your installation uses hooks or channels, configure them through the runtime documentation and test their observable effect. Keep host paths, model assignments, credentials, and live queues in local configuration. A foreground session, an ephemeral job, and an always-on runtime worker have different lifecycles; document which is in use.

Persist handoffs and evidence at context and session boundaries. Cross-model review must use an independently configured model family and an exact subject. An extra session using the same model family is not that review.
