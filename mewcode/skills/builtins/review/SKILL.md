---
name: review
description: Review the current change set in an isolated context and rank actionable findings
allowedTools: [Bash, ReadFile, Grep, Glob]
mode: fork
model: inherit
context: none
---
# Code Review SOP

Extra focus from the user: $ARGUMENTS

Inspect the repository and relevant diff before reaching conclusions. Review these dimensions:

1. Logic errors, broken invariants, edge cases, and incorrect error handling.
2. Security boundaries, permissions, injection risks, unsafe file or command handling.
3. Performance regressions, unbounded work, blocking I/O, and avoidable resource use.
4. Code style, readability, duplication, and consistency with the surrounding project.
5. Maintainability, API compatibility, missing tests, and operational risks.

Return findings only when they are concrete and actionable. Classify each as **Critical**,
**Warning**, or **Info**, cite the narrowest file/line range available, and explain a practical fix.
If no findings remain, say so explicitly and list residual testing risks.
