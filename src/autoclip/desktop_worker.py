from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any

from .desktop_protocol import ProtocolError, failure, parse_request, success
from .desktop_service import DesktopService


def handle(service: DesktopService, raw: Any) -> dict[str, Any]:
    request_id = raw.get("id", "unknown") if isinstance(raw, dict) else "unknown"
    try:
        request = parse_request(raw)
        return success(request.id, service.dispatch(request.command, request.payload))
    except ProtocolError as exc:
        return failure(request_id, exc.code, exc.message, recoverable=exc.recoverable, details=getattr(exc, "details", None))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return failure(request_id, "internal_error", "AutoClip couldn't complete this action. Try again or view the development logs.")


def serve(service: DesktopService) -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            response = failure("unknown", "invalid_json", "The desktop sent an invalid request.")
        else:
            response = handle(service, raw)
        # Keep the stdio protocol ASCII-safe. On Windows a redirected Python
        # stdout can use a legacy code page, while Tauri's Rust reader expects
        # UTF-8. Escaping non-ASCII JSON characters prevents progress text such
        # as the middle dot in transcription stages from corrupting the stream.
        sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoclip-desktop-worker")
    parser.add_argument("--stdio", action="store_true", required=True)
    parser.parse_args(argv)
    return serve(DesktopService())


if __name__ == "__main__":
    raise SystemExit(main())
