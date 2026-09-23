from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = "1"
COMMANDS = {
    "doctor",
    "create_project",
    "open_project",
    "get_project_state",
    "inspect_media",
    "relink_source",
    "list_recent_projects",
    "remove_recent_project",
    "get_settings",
    "update_settings",
    "start_job",
    "cancel_job",
    "get_job",
    "list_jobs",
}


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str, *, recoverable: bool = True, details: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.details = details


@dataclass(frozen=True, slots=True)
class CommandRequest:
    id: str
    command: str
    payload: dict[str, Any]
    version: str = PROTOCOL_VERSION


def parse_request(raw: Any) -> CommandRequest:
    if not isinstance(raw, dict):
        raise ProtocolError("invalid_request", "The desktop request must be a JSON object.")
    if raw.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_protocol", "The desktop and processing worker versions do not match.", recoverable=False)
    request_id = raw.get("id")
    command = raw.get("command")
    payload = raw.get("payload", {})
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("invalid_request", "The desktop request is missing an id.")
    if command not in COMMANDS:
        raise ProtocolError("unsupported_command", "This desktop action is not supported.")
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_request", "The desktop request payload must be an object.")
    return CommandRequest(request_id, command, payload)


def success(request_id: str, data: Any) -> dict[str, Any]:
    return {"version": PROTOCOL_VERSION, "id": request_id, "ok": True, "data": data}


def failure(request_id: str, code: str, message: str, *, recoverable: bool = True, details: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "recoverable": recoverable}
    if details:
        error["details"] = details
    return {"version": PROTOCOL_VERSION, "id": request_id, "ok": False, "error": error}
