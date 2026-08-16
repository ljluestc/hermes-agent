## What does this PR do?

Fixes the desktop "Branch current chat" path so that it builds the child session from the full persisted display transcript instead of from the live, compacted runtime projection.

When a long session undergoes context compression, the in-memory `$messages` atom becomes a model projection (summary bubbles + recent tail). The previous code used `session.branch` with a count derived from that projection, so the new branch was missing the early messages and started from the wrong point. This PR makes the "current chat" branch behave like the right-click "branch stored session" path: it loads the full persisted history via `getAllSessionMessages` and creates the child with `session.create` + the full `messages` array.

If a specific `messageId` is supplied but cannot be mapped into the persisted transcript (e.g., the clicked bubble is a synthetic compression summary), `selectBranchMessages` now falls back to the complete authoritative transcript instead of the truncated local prefix, ensuring the branch never inherits a partial or offset context.

## Related Issue

Fixes #87949

## Type of Change

- [x] 🐛 Bug fix (non-breaking change that fixes an issue)

## Changes Made

- `apps/desktop/src/app/session/hooks/use-session-actions/utils.ts`
  - `selectBranchMessages`: when the clicked bubble cannot be mapped to a row in the authoritative transcript, fall back to the full authoritative transcript instead of the compacted local prefix.
- `apps/desktop/src/app/session/hooks/use-session-actions/index.ts`
  - `branchCurrentSession`: when the session has a stored id and the persisted transcript is available, use `session.create` with the full authoritative messages; keep `session.branch` only as the fallback for unsaved sessions.
- `apps/desktop/src/app/session/hooks/use-session-actions.test.tsx`
  - Update the compacted-live-chat branch test to expect `session.create` with the full persisted messages.
  - Update the drift-abort assertion to cover both `session.create` and `session.branch`.
- `apps/desktop/src/app/session/hooks/use-session-actions/utils.test.ts`
  - Add a test for the mapping-failure fallback to the full authoritative transcript.

## How to Test

1. Open a long desktop session (600+ messages) and let it undergo context compression, or use an existing compressed session.
2. Click the "Branch" button at the end of the conversation, or run `/branch` in the composer.
3. The new branch should contain the same number of messages as the parent (or the full prefix up to the selected message), starting from the actual first message, not from a summary or early-middle fragment.

Automated verification:

```bash
cd apps/desktop
npm run test:ui -- src/app/session/hooks/use-session-actions.test.tsx src/app/session/hooks/use-session-actions/utils.test.ts
npm run test:ui
npm run typecheck
```

Observed results:

- `src/app/session/hooks/use-session-actions.test.tsx` and `src/app/session/hooks/use-session-actions/utils.test.ts`: 162 tests passed.
- Full desktop UI suite: `484 files, 4504 tests passed`.
- `npm run typecheck`: passed.

## Checklist

### Code

- [x] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`fix(scope):`, `feat(scope):`, etc.)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (no unrelated commits)
- [x] I've added tests for my changes (required for bug fixes, strongly encouraged for features)
- [x] I've tested on my platform: Ubuntu 24.04

### Documentation & Housekeeping

- [x] I've considered cross-platform impact (Windows, macOS) per the [compatibility guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#cross-platform-compatibility) — the change is TypeScript desktop renderer logic; backend RPC behavior is unchanged.

## Screenshots / Logs

Full desktop UI test suite passes:

```
Test Files  484 passed (484)
Tests  4504 passed (4504)
```

`npm run typecheck` passes.
