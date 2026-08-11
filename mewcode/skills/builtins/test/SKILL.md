---
name: test
description: Detect the project type, run the appropriate tests, and diagnose failures
allowedTools: [Bash, ReadFile, Grep, Glob]
mode: inline
model: inherit
context: full
---
# Test SOP

User scope: $ARGUMENTS

1. Detect the project type from repository files before choosing a command:
   - `pyproject.toml` or `pytest.ini` → `pytest`
   - `go.mod` → `go test ./...`
   - `package.json` → inspect scripts, then use the declared npm/pnpm/yarn test command
   - `Cargo.toml` → `cargo test`
2. Prefer the narrowest relevant test first, then run the broader suite when practical.
3. Capture the exact failing command, exit status, and shortest useful error excerpt.
4. Distinguish a product-code bug from a stale/incorrect test; do not modify tests merely to make
   them pass.
5. After a fix, rerun the failing test and an appropriate regression suite.
6. Report commands executed, pass/fail counts, remaining failures, and untested risk.
