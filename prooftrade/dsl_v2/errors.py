"""Error and warning catalogue for the v2 DSL.

Every issue carries four things, because an error message that only says *what* is
wrong makes the reader guess at *where* and *what to do instead*:

* a stable machine `code`, safe to branch on and safe to translate
* a JSON Pointer `path` to the exact offending node
* a `message` in plain English, which names the fix
* the offending value, so the reader does not have to go and look it up

Errors block execution. Warnings never do: they are things a careful reader would
raise in review, and suppressing them would make the tool quieter than it should be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    path: str
    message: str
    found: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "found": self.found,
        }

    def format(self) -> str:
        marker = "error" if self.severity == "error" else "warn "
        location = self.path or "/"
        found = "" if self.found is None else f"  (found: {self.found!r})"
        return f"{marker}  {location}\n       {self.message}{found}"


@dataclass
class ValidationResult:
    """The outcome of validating one document."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    strategy: Any = None          # the parsed Strategy, when valid

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
        }

    def format(self) -> str:
        if self.ok and not self.warnings:
            return "valid"
        lines: list[str] = []
        if self.errors:
            lines.append(f"{len(self.errors)} error(s):")
            lines += [i.format() for i in self.errors]
        if self.warnings:
            lines.append(f"{len(self.warnings)} warning(s):")
            lines += [i.format() for i in self.warnings]
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Codes. Grouped so the catalogue reads as a table rather than a list of constants.
# --------------------------------------------------------------------------------------

# Structural - the document does not match the schema.
E_VERSION            = "E_VERSION"
E_UNKNOWN_FIELD      = "E_UNKNOWN_FIELD"
E_MISSING_FIELD      = "E_MISSING_FIELD"
E_WRONG_TYPE         = "E_WRONG_TYPE"
E_NOT_IN_ENUM        = "E_NOT_IN_ENUM"
E_OUT_OF_RANGE       = "E_OUT_OF_RANGE"
E_BAD_LENGTH         = "E_BAD_LENGTH"
E_BAD_TICKER         = "E_BAD_TICKER"
E_PERIOD_TOO_SHORT   = "E_PERIOD_TOO_SHORT"
E_NOT_AN_OBJECT      = "E_NOT_AN_OBJECT"

# Semantic - the document parses but does not mean anything runnable.
E_COMPARISON_NEEDS_SERIES   = "E_COMPARISON_NEEDS_SERIES"
E_COMPARISON_NEEDS_CONSTANT = "E_COMPARISON_NEEDS_CONSTANT"
E_COMPARISON_NEEDS_RANGE    = "E_COMPARISON_NEEDS_RANGE"
E_RANGE_NOT_ALLOWED         = "E_RANGE_NOT_ALLOWED"
E_RANGE_BOUNDS              = "E_RANGE_BOUNDS"
E_UNIT_MISMATCH             = "E_UNIT_MISMATCH"
E_SELF_COMPARISON           = "E_SELF_COMPARISON"
E_DUPLICATE_EXIT            = "E_DUPLICATE_EXIT"
E_CONFLICTING_SIGNAL_EXITS  = "E_CONFLICTING_SIGNAL_EXITS"
E_R_MULTIPLE_NEEDS_STOP     = "E_R_MULTIPLE_NEEDS_STOP"
E_R_MULTIPLE_AMBIGUOUS      = "E_R_MULTIPLE_AMBIGUOUS"
E_SIZING_MISSING_PARAM      = "E_SIZING_MISSING_PARAM"
E_SIZING_UNEXPECTED_PARAM   = "E_SIZING_UNEXPECTED_PARAM"
E_SIZING_OVER_ALLOCATION    = "E_SIZING_OVER_ALLOCATION"
E_SLIPPAGE_MISSING_PARAM    = "E_SLIPPAGE_MISSING_PARAM"
E_SLIPPAGE_UNEXPECTED_PARAM = "E_SLIPPAGE_UNEXPECTED_PARAM"
E_CALENDAR_DATE_ORDER       = "E_CALENDAR_DATE_ORDER"
E_CALENDAR_WEEKDAY          = "E_CALENDAR_WEEKDAY"
E_CALENDAR_MONTH            = "E_CALENDAR_MONTH"

# Warnings - runnable, but a careful reader would object.
W_PRICE_VS_CONSTANT   = "W_PRICE_VS_CONSTANT"
W_NO_STOP             = "W_NO_STOP"
W_TARGET_INSIDE_STOP  = "W_TARGET_INSIDE_STOP"
W_ZERO_COSTS          = "W_ZERO_COSTS"
W_TARGET_FIRST        = "W_TARGET_FIRST"
W_FILL_AT_LEVEL       = "W_FILL_AT_LEVEL"
W_SIZING_STOP_MISMATCH = "W_SIZING_STOP_MISMATCH"
W_NARROW_UNIVERSE     = "W_NARROW_UNIVERSE"
W_DUPLICATE_CONDITION = "W_DUPLICATE_CONDITION"
W_TRAILING_AND_FIXED  = "W_TRAILING_AND_FIXED"
W_INERT_INTRABAR_PRIORITY = "W_INERT_INTRABAR_PRIORITY"


def error(code: str, path: str, message: str, found: Any = None) -> Issue:
    return Issue(code=code, severity="error", path=path, message=message, found=found)


def warning(code: str, path: str, message: str, found: Any = None) -> Issue:
    return Issue(code=code, severity="warning", path=path, message=message, found=found)
