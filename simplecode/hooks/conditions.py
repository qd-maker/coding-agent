"""Small, deliberately non-Turing-complete condition expression language."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Literal

from simplecode.hooks.models import HookContext

_OPERATORS = ("==", "!=", "=~", "~=")


class ConditionParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    operator: str
    value: str

    def evaluate(self, context: HookContext) -> bool:
        actual = context.get_field(self.field)
        if self.operator == "==":
            return actual == self.value
        if self.operator == "!=":
            return actual != self.value
        if self.operator == "=~":
            try:
                return re.search(self.value, actual) is not None
            except re.error:
                return False
        if self.operator == "~=":
            return fnmatch.fnmatch(actual, self.value)
        return False


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    conditions: list[Condition] = field(default_factory=list)
    logic: Literal["and", "or"] = "and"

    def evaluate(self, context: HookContext) -> bool:
        values = (condition.evaluate(context) for condition in self.conditions)
        return all(values) if self.logic == "and" else any(values)


def _parse_single(expression: str) -> Condition:
    text = expression.strip()
    for operator in _OPERATORS:
        if operator not in text:
            continue
        field_name, raw_value = text.split(operator, 1)
        field_name = field_name.strip()
        value = raw_value.strip()
        if not field_name or not value:
            raise ConditionParseError(f"Invalid condition: {expression!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if operator == "=~" and len(value) >= 2 and value.startswith("/") and value.endswith("/"):
            value = value[1:-1]
        return Condition(field_name, operator, value)
    raise ConditionParseError(f"Condition has no supported operator: {expression!r}")


def parse_condition(expression: str | None) -> Condition | ConditionGroup | None:
    if expression is None or not expression.strip():
        return None
    has_and = "&&" in expression
    has_or = "||" in expression
    if has_and and has_or:
        raise ConditionParseError("Cannot mix '&&' and '||' in a single condition expression")
    if has_and:
        return ConditionGroup([_parse_single(part) for part in expression.split("&&")], "and")
    if has_or:
        return ConditionGroup([_parse_single(part) for part in expression.split("||")], "or")
    return _parse_single(expression)


__all__ = [
    "Condition",
    "ConditionGroup",
    "ConditionParseError",
    "_OPERATORS",
    "_parse_single",
    "parse_condition",
]
