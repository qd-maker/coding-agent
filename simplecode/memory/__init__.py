"""Public memory, instruction, and session persistence API."""

from simplecode.memory.auto_memory import MemoryManager
from simplecode.memory.instructions import load_instructions, process_includes
from simplecode.memory.session import (
    ResumeResult,
    Session,
    SessionManager,
    SessionMeta,
    SessionRecord,
    build_time_gap_message,
    generate_session_summary,
    validate_message_chain,
)

__all__ = [
    "MemoryManager",
    "ResumeResult",
    "Session",
    "SessionManager",
    "SessionMeta",
    "SessionRecord",
    "build_time_gap_message",
    "generate_session_summary",
    "load_instructions",
    "process_includes",
    "validate_message_chain",
]
