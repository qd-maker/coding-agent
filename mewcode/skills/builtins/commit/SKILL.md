---
name: commit
description: Inspect repository changes and create a focused conventional commit
allowedTools: [Bash, ReadFile, Grep]
mode: inline
model: inherit
context: full
---
# Commit SOP

User request: $ARGUMENTS

1. Run `git status --short` and inspect the exact scope of tracked and untracked changes.
2. Read `git diff` and `git diff --staged`; never infer changes only from filenames.
3. Exclude secrets, generated files, unrelated edits, and files the user did not ask to include.
4. Stage only the coherent change set with explicit paths.
5. Write a concise Conventional Commit message that explains intent rather than listing files.
6. Run `git commit` and verify it with `git status --short` plus `git log -1 --oneline`.
7. Report the commit hash, message, included scope, and any remaining changes.
