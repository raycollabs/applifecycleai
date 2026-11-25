"""Sample: Read sessionSummaries from the settings file. Return enabled with the default caps."""
from dataclasses import dataclass, fields

from .db import settings


@dataclass(frozen=True)
class SessionSummarySettings:
    enabled: bool = True
    maxAttemptsPerRange: int = 3
    invalidOutputRetries: int = 1
    clientRetries: int = 2
    maxCallsPerConversationPerDay: int = 50
    maxCallsPerDay: int = 2000
    maxCostUsdPerDay: float = 5.00
    maxOutputTokens: int = 8000
    maxVisibleTokens: int = 800
    maxNodeVisibleTokens: int = 250
    inputUsdPerMillionTokens: float = 2.00
    outputUsdPerMillionTokens: float = 10.00


def read_record():
    # Sample: Read a settings file with no sessionSummaries record. Return an empty record.
    return settings().get('sessionSummaries') or {}


def load_session_settings():
    # Sample: Read {"enabled": true, "maxAttemptsPerRange": 1}. Return enabled with 1 attempt and other defaults.
    record = read_record()
    defaults = SessionSummarySettings()
    if not isinstance(record, dict) or set(record) - {field.name for field in fields(defaults)}:
        raise ValueError('The sessionSummaries record is invalid')
    values = {}
    for field in fields(defaults):
        default = getattr(defaults, field.name)
        value = record.get(field.name, default)
        if field.type is bool:
            valid = type(value) is bool
        elif field.type is int:
            valid = type(value) is int and value >= 0
        else:
            valid = type(value) in (int, float) and value >= 0
        if not valid:
            raise ValueError('The sessionSummaries record is invalid')
        values[field.name] = value
    return SessionSummarySettings(**values)
