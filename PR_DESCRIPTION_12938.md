# Fix `crontab -l` mismatch on macOS local backend (fixes #12938)

## Summary
This PR fixes a local-backend inconsistency on macOS where Hermes reports `crontab -l` as empty (or `no crontab for <user>`) even when the same command in the user shell returns real entries for the same UNIX user and `$HOME`.

## Problem
### Expected behavior
- `crontab -l` should return the same data in Hermes local backend and in a normal user shell when both run as the same user on the same host.

### Actual behavior
- User identity checks match in both contexts (`whoami`, `id`, `$USER`, `$LOGNAME`, `$HOME`).
- In the user shell, `crontab -l` prints the configured cron entry.
- In Hermes local backend, `crontab -l` incorrectly reports no crontab / empty output.

### User impact
- Users cannot trust Hermes output for system-level inspection commands (cron, backup scheduling, permission-sensitive checks) on macOS local backend when results diverge from ground truth in the shell.

## Root cause
The terminal execution path for this command could return a misleading fallback outcome instead of faithfully returning the binary’s real stdout/stderr/exit semantics in this scenario. This made `crontab -l` appear empty even though the user had an existing crontab.

## What this PR changes
1. Ensures the local terminal path returns the real command result for `crontab -l` without synthetic “empty/no crontab” interpretation in this mismatch case.
2. Preserves raw stdout/stderr and exit code behavior so users can compare Hermes output directly with shell output.
3. Keeps interactive command restrictions unchanged (e.g., `crontab -e`, `vim`) since those are a separate, intentional policy boundary.

## Why this is safe
- Scope is narrow to command result handling for this backend path.
- No behavior change is introduced for approved interactive-command policy.
- The change improves fidelity (report actual command output) rather than adding new command capabilities.

## Validation
### Manual validation
- Reproduced with a user account that has an existing crontab:
  - Shell: `crontab -l` prints expected entries.
  - Hermes local backend: now returns matching entry output.
- Verified identity parity signals remain identical (`whoami`, `id`, `$USER`, `$LOGNAME`, `$HOME`).

### Regression validation
- Added/updated tests to ensure command output plumbing does not collapse this case into synthetic empty/no-crontab responses.
- Verified no regressions in standard non-interactive terminal command execution paths.

## Notes
- On modern macOS, `/usr/bin` is on the Signed System Volume, so wrapper-based verification via replacing `/usr/bin/crontab` is not feasible; this fix avoids relying on that mechanism.
- This PR addresses non-interactive output fidelity only.
