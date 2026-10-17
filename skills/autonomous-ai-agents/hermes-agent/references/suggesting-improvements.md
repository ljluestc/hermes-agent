# Suggesting Improvements to the Hermes Team

Turn friction the user hits in a session into a well-formed, deduplicated issue on
`NousResearch/hermes-agent`. Hermes never files anything on its own: it drafts, the user
reviews, and only an explicit "file it" sends it.

## When to Use

- The user says "suggest this to the Hermes team", "file a feature request", "report this bug".
- The same Hermes limitation came up repeatedly this session and the user might want it
  raised. **Offer** once ("Want me to draft a suggestion for the Hermes team?"), never draft
  or file unprompted.

Not for: security vulnerabilities (see Pitfalls), questions the docs answer
(check `llms.txt` first; see the hub's Scope & Verification), or the user's own repos (the
`github` skill covers those).

## Prerequisites

- `gh` authenticated (`gh auth status`). Without it, hand the user the finished draft and
  the link https://github.com/NousResearch/hermes-agent/issues/new/choose to paste it into.

## Procedure

1. **Classify.** Bug (something shipped is broken) → `bug_report` form; anything else
   (new capability, UX, docs, performance) → `feature_request` form. Setup trouble is a
   question, not an issue: point at the docs or the Discord first.

2. **Check it isn't already shipped.** Fetch `llms.txt` and search for the feature. "Hermes
   can't do X" filed against something Hermes already does gets closed as
   `implemented_on_main`.

3. **Sweep for duplicates** with 2–3 phrasings of the core problem, open and closed:

   ```bash
   gh issue list -R NousResearch/hermes-agent --state all --limit 10 --search "<keywords> in:title,body"
   ```

   If one matches, show it to the user and offer to 👍 it (`gh api -X POST
   repos/NousResearch/hermes-agent/issues/<n>/reactions -f content=+1`) or add a comment with
   the new detail, instead of opening a duplicate. Only continue when the user says theirs
   differs, and say how in the draft.

4. **Draft** into a file in the session's temp dir, not inline in the command:
   - Feature: fill `templates/feature-request.md` (the headings mirror the repo's issue form
     so triage reads it the same way). Title `[Feature]: <one line>`, label `enhancement`.
   - Bug: headings Bug Description, Steps to Reproduce, Expected Behavior, Actual Behavior,
     Affected Component, Operating System, Hermes Version (`hermes version`). Title
     `[Bug]: <one line>`, label `bug`.
   - **One problem per issue.** A list of ten ideas is ten drafts or one discussion, never
     one issue: wishlists can't be closed by any single PR and stall.
   - State the *problem* from what happened in the session, then the proposed behaviour
     (command, flag, config key). Prefer a skill/plugin shaped proposal over a new core
     tool — the project rejects core tools a skill could cover.

5. **Scrub, then show.** Remove API keys, tokens, hostnames, usernames in paths
   (`/home/alice/…` → `~/…`), private conversation content and third-party names the user
   didn't ask to include. Show the full title, labels and body with `read_file`, and ask
   for an explicit go-ahead. Edits loop back here.

6. **File** only after that go-ahead:

   ```bash
   gh issue create -R NousResearch/hermes-agent --title "<title>" --label enhancement --body-file <draft-path>
   ```

   Report the URL `gh` prints. To follow it later: `gh issue view <n> -R
   NousResearch/hermes-agent --comments`, or a `cronjob` that checks its state weekly if
   the user asks to be told when it changes.

## Pitfalls

- **Security issues never go in a public issue.** Anything that crosses a trust boundary
  (secret leak, sandbox escape, auth bypass) goes to
  https://github.com/NousResearch/hermes-agent/security/advisories/new — tell the user and
  stop; do not draft it publicly.
- **`hermes debug share` uploads logs to a public paste service.** Only run it, and only
  attach its links, with the user's explicit consent for that upload.
- Labels other than `enhancement`/`bug` may not exist or be restricted; `gh` fails the whole
  create on an unknown label, so don't invent them.
- Filing on the user's behalf posts under *their* GitHub account. Never file without the
  go-ahead from step 5, even if the user asked "suggest it" earlier in the session.

## Verification

- The duplicate sweep was run and its result shown to the user.
- The user saw the exact final body before filing.
- The reported URL is the one `gh issue create` printed, not a guess.
