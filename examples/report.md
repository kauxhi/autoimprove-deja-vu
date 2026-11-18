# autoimprove 2026-11-25 (EXAMPLE)

> This is an invented example report. Numbers, session ids and fingerprints are made up to show the format.

Next action: approve or reject proposals 1 to 3.

## Déjà Vu Rate: 18.2% | Ghosts: 1

errors_total 44, errors_repeat 8, within_week_repeats 2.

| Fingerprint | Tool | Count | Sessions | Weeks seen | Lesson |
|---|---|---|---|---|---|
| error: enoent: no such file or directory, open '\<path\>' | Read | 5 | 3 | 2 | check-path-before-read (Ghost) |
| command not found: \<path\> | Bash | 3 | 2 | 2 | none |
| string to replace not found in file. | Edit | 6 | 4 | 1 | none |
| tests failed: \<n\> of \<n\> | Bash | 4 | 3 | 1 | none |
| permission denied | Bash | 2 | 1 | 1 | none |

## Sync Score: 83.4 / 100 (v2, change: +2.5)

| Sub-score | Score | Count | Last week |
|---|---|---|---|
| corrections | 95.6 | 6 | 94.9 |
| reprompts | 94.1 | 2 | 93.7 |
| violations | 96.3 | 5 | 93.1 |
| wasted | 83.5 | 44 | 81.8 |
| rework | 71.4 | 4 of 14 | 64.0 |
| wins | 44.0 | 11 of 25 | 38.9 |
| deploys | 100.0 | 0 of 9 failed | 100.0 |
| migrations | n/a | 0 | 100.0 |

Window 2026-11-18 to 2026-11-25, 25 sessions, 133.3 active hours, 412 prompts, 14 commits.

## Top 3 friction patterns

1. Low one-pass rate. Evidence: a1b2c3d4 (2026-11-19) 4 corrections, e5f6a7b8 (2026-11-21) 3 corrections.
2. Rework after a feature commit. Evidence: commits 9c8d7e6f, 0a1b2c3d (fix within 48 hours of the change).
3. Edits on stale file content. Evidence: f1e2d3c4 (2026-11-22) 4 failed Edit calls.

## Repeat tasks

| Topic | Sessions | Friction | First | Last |
|---|---|---|---|---|
| auth-signin | 4 | 7 | 2026-10-30 | 2026-11-23 |
| deploy | 6 | 3 | 2026-10-28 | 2026-11-24 |

## Proposals (not applied)

1. Target: Ghosts. `.claude/settings.json`. Replace the prose lesson with a PreToolUse hook on Read that blocks a path which does not exist and suggests Glob.
   ```diff
   + "PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "~/.claude/hooks/path-exists.sh"}]}]
   ```
2. Target: wasted. `~/.claude/CLAUDE.md`.
   ```diff
   + - Re-read a file before a second Edit on it in the same turn; never edit from memory.
   ```
3. Target: corrections (playbook for auth-signin). New note `playbooks/auth-signin.md` with the working provider setup, redirect URL checklist and the two traps hit before.

## Last week's proposals

1. Run tests before commit: applied, rework moved 64.0 to 71.4.
2. Stop after two identical failures: applied, wasted moved 81.8 to 83.5.
3. Banned character check: applied, violations moved 93.1 to 96.3.
