---
name: autoimprove
description: Auto improve how Claude Code works with you. Weekly review of your local Claude Code sessions and git history: reports the Déjà Vu Rate (mistakes that came back) and the Sync Score (0 to 100, formula v2), diagnoses the top 3 friction patterns, flags repeat tasks and proposes up to 3 improvements as diffs you approve. Use when the user says "autoimprove", "auto improve", "self improve", "weekly review", "sync score", "deja vu rate", or when a weekly scheduled task fires.
---

# autoimprove

Your AI should never make the same mistake twice. This skill measures whether it does.

Output: one report in the notes folder, one SCOREBOARD row, at most 3 proposals. Nothing is applied without the user's approval.

## Hard rules

- Read only. Nothing leaves the machine. The only optional network use is MCP connector counts (step 1), and only counts come back.
- Counts and ids only. Never quote transcript text, prompts, or raw error text in the report. Session ids, dates, commit shas and normalised fingerprints are fine.
- Every number comes from `collect.py` output. Every number is counted, never estimated. A signal that could not be measured is `n/a` and drops out of the weights.
- Never apply a proposal. No edits to `CLAUDE.md`, skills, hooks or memory. Proposals are exact diffs in the report; the user approves each one.
- Writes allowed only in the notes folder (default `~/.claude/autoimprove/`, ask the user once on the first run and remember the answer there in `config.json`). That folder holds the reports, `mistakes.json` (the ledger) and `SCOREBOARD.md`.
- Report shape: next action first, numbered, lists capped at 5, no preamble.

## Steps

1. Optional connector counts. If deploy or database MCP tools are connected, count for the same 7-day window and write `$TMPDIR/autoimprove-extra.json`:
   `{"deploys": {"total": int, "failed": int}, "migrations": {"total": int, "fix": int}}`
   - `failed` = deployments in an error state. Cancelled builds do not count.
   - `fix` = migrations whose name contains fix, revert, hotfix or rollback.
   - Counts only: never read row data. Skip any connector that is unavailable and leave its key out (its sub-score becomes n/a).
2. Collect: `python3 ~/.claude/skills/autoimprove/collect.py --extra "$TMPDIR/autoimprove-extra.json" --ledger ~/.claude/autoimprove/mistakes.json > "$TMPDIR/autoimprove.json"`. Drop `--extra` if step 1 was skipped. Add `--end YYYY-MM-DD` to rerun a past week. A missing ledger is fine (empty ledger, baseline week).
3. Save the ledger: write the JSON's `ledger_next` to `~/.claude/autoimprove/mistakes.json` with the Write tool. The script never writes it.
4. Report the Déjà Vu Rate first (`repeat_mistakes`), then the Sync Score (`sync_score`, `sub_scores`) with the delta against the previous SCOREBOARD row. Week 1 is the baseline.
5. Diagnose: the 3 lowest weighted sub-scores. For each, cite `evidence` entries (session id and date, or commit sha). You may open cited sessions to understand the pattern, but report only what kind of thing happened.
6. Repeat tasks: for the top `repeat_tasks` entry by friction, one proposal must be a playbook (see below).
7. Propose at most 3 improvements, each an exact diff (file path, lines to add or change) naming the one signal it should move. Check last week's proposals first: applied or not, and whether their signal moved.
8. Write `~/.claude/autoimprove/YYYY-MM-DD.md` (template below). Append the row from `collect.py ... --scoreboard-row` to `~/.claude/autoimprove/SCOREBOARD.md`.
9. Reply with the Déjà Vu Rate, the Sync Score, the delta, and the proposals as numbered choices. Stop there.

## Déjà Vu Rate (the headline)

- A mistake = the fingerprint of a failed tool call: first line of the error, lower-cased, with paths, urls, ids, long quoted strings and digits stripped.
- `deja_vu_rate` = share of this week's failed tool calls whose fingerprint was already seen in an earlier week. Goal: 0. n/a on the baseline week.
- `ghosts` = fingerprints that came back even though a lesson exists for them. A Ghost means the lesson failed.
- `within_week_repeats` = fingerprints hit in 3 or more sessions this week.
- Unscored in v2. It becomes a scored sub-score in v3 once two weeks of ledger exist.

Lesson rules:

a. List up to 5 mistakes, repeats first (the JSON is already sorted that way).
b. Every repeat (`seen_before` true, or 3 or more `sessions`) without a `lesson` gets one: a one-line rule, proposed as a diff, that names the wrong action and the right one. Once approved, write its slug into that fingerprint's `lesson` field in `mistakes.json`.
c. A Ghost gets a stronger enforcement: a hook or a script check. Never a reworded sentence.

## Sync Score, formula v2

Active hours = sum of gaps between consecutive main-thread events, each capped at 5 minutes. Only sessions with a human prompt count. Subagent transcripts and `EXCLUDE_PROJECTS` folders are skipped.

| Sub-score | Weight | Formula (clamped 0 to 100) | Counted as |
|---|---|---|---|
| corrections | 20 | 100 - 20 x count per active hour | prompts opening with no / wrong / I said / again / stop / undo / revert / "not what" / "still broken", plus interrupts |
| reprompts | 10 | 100 - 40 x count per active hour | a prompt at least 80% similar to an earlier one in the same session |
| violations | 15 | 100 - 10 x count per active hour | assistant messages matching a `BANNED_OUTPUT` regex; n/a when the list is empty |
| wasted | 10 | 100 - 5 x count per active hour | failed tool results, including permission denials |
| rework | 15 | 100 x (1 - rework commits / commits) | own commits titled revert, or titled fix touching a file from an own non-fix commit in the prior 48 hours |
| wins | 15 | 100 x one-pass sessions / sessions | sessions with zero corrections and zero reprompts |
| deploys | 10 | 100 x (1 - failed / total) | from `--extra`; n/a without it |
| migrations | 5 | 100 x (1 - fix / total) | from `--extra`; n/a without it |

Sync Score = weighted mean of the sub-scores that are not n/a. Changing a weight or formula means bumping `FORMULA_VERSION` and recomputing last week with `--end`.

## Playbooks

A playbook is a short note or skill holding the known-good steps, the working stack choice and the traps hit before, so the next ask for that topic is done in one pass. Write it from the cited sessions. Never copy personal data into it. Topics live in `TOPICS` in `collect.py`; adding one needs no formula bump.

## Report template

```markdown
# autoimprove YYYY-MM-DD

Next action: approve or reject proposals 1 to 3.

## Déjà Vu Rate: N.N% (or n/a, baseline) | Ghosts: N

errors_total N, errors_repeat N, within_week_repeats N.

| Fingerprint | Tool | Count | Sessions | Weeks seen | Lesson |
|---|---|---|---|---|---|

## Sync Score: NN.N / 100 (v2, change: +N.N | baseline)

| Sub-score | Score | Count | Last week |
|---|---|---|---|

Window, sessions, active hours, prompts, commits.

## Top 3 friction patterns

1. Pattern. Evidence: session id (date), count.

## Repeat tasks

| Topic | Sessions | Friction | First | Last |
|---|---|---|---|---|

## Proposals (not applied)

1. Target signal. File. Diff in a fenced block.

## Last week's proposals

Applied or not, and whether the signal moved.
```

## Check

`python3 ~/.claude/skills/autoimprove/collect.py --selftest` must print `selftest ok` after any edit to the script.
