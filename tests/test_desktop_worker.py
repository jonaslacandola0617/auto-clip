from __future__ import annotations

import io
import json
import sys

from autoclip.desktop_worker import serve


class _Service:
    def dispatch(self, _command: str, _payload: dict[str, object]) -> dict[str, str]:
        return {"current_stage": "Transcribing locally · 0.0 min processed"}


def test_stdio_protocol_stays_utf8_when_windows_stdout_uses_a_legacy_code_page(monkeypatch) -> None:
    request = {"version": "1", "id": "encoding", "command": "doctor", "payload": {}}
    output_bytes = io.BytesIO()
    output = io.TextIOWrapper(output_bytes, encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    monkeypatch.setattr(sys, "stdout", output)

    assert serve(_Service()) == 0
    output.flush()
    response = json.loads(output_bytes.getvalue().decode("utf-8"))

    assert response["data"]["current_stage"] == "Transcribing locally · 0.0 min processed"
