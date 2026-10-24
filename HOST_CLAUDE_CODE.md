# CC-Suite: Claude Code host adapter

Start by reading [HARNESS_CORE.md](HARNESS_CORE.md), the active role definition, and the task's relevant domain sources. The shared operating rules govern every host; this adapter describes entry and continuity for Claude Code.

Use the installed host's supported capabilities. Confirm the actual model, permissions, available tools, and workspace. Do not infer that a hook, channel, browser session, background worker, or command is active because its configuration file exists.

The generic [init-role command](.claude/commands/init-role.md) resumes a seat and its recorded context. It does not install a service, issue credentials, admit a runtime identity, or expand authority.

## Skills

Put each reusable procedure in its own skill: `.claude/skills/<name>/SKILL.md`, with a frontmatter `name` and a `description` that says what it does and names the words that trigger it. The description is how the host decides to load the skill, so it carries the triggers; the body carries the procedure. Add or revise a skill only for procedure that remains after mechanical enforcement ([SKILLIFICATION_PROTOCOL.md](governance/SKILLIFICATION_PROTOCOL.md)).

Keep one shorthand table in the root instructions every session reads. Each row maps the owner's trigger words to the skill they open, and the table is the index to the skills: a trigger appears there and in its skill's description, nowhere else. A trigger fires only on the owner's own message, as its own word, never inside another word, a peer agent's message or a file.

| The owner says | It opens |
|---|---|
| [trigger words] | the `[skill-name]` skill |
| [pause] | stop now, say what you were doing and why, and wait |

## Lanes

A coordinating session hands work to lanes that run as their own sessions, each with one outcome. Two agent definitions cover them:

- [build-lane](.claude/agents/build-lane.md): changes code on its own branch in an isolated working copy and lands as its own pull request through the merge queue.
- [read-lane](.claude/agents/read-lane.md): reads, classifies or researches and changes nothing.

A lane ends at its hand-in. No session waits on a lane with a polling loop; outcomes are read from the record when there is a reason.

## Continuity

If your installation uses hooks or channels, configure them through the runtime documentation and test their observable effect. Keep host paths, model assignments, credentials, and live queues in local configuration. A foreground session, an ephemeral job, and an always-on runtime worker have different lifecycles; document which is in use. A hook that ends background loops when a turn ends keeps forgotten waits from piling up.

Persist handoffs and evidence at context and session boundaries. Independent review uses reviewers from other model families ([HARNESS_CORE.md](HARNESS_CORE.md) section 8); an extra session of the same model family is not that review.
