import unittest

from autoclip.desktop_protocol import PROTOCOL_VERSION, ProtocolError, failure, parse_request, success


class DesktopProtocolTests(unittest.TestCase):
    def test_request_and_envelopes(self) -> None:
        request = parse_request({"version": PROTOCOL_VERSION, "id": "1", "command": "doctor", "payload": {}})
        self.assertEqual(request.command, "doctor")
        self.assertEqual(success("1", {"ready": True})["data"]["ready"], True)
        self.assertEqual(failure("1", "bad", "Try again")["error"]["code"], "bad")

    def test_arbitrary_command_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError) as context:
            parse_request({"version": PROTOCOL_VERSION, "id": "1", "command": "run_shell", "payload": {"command": "whoami"}})
        self.assertEqual(context.exception.code, "unsupported_command")

    def test_protocol_version_is_required(self) -> None:
        with self.assertRaises(ProtocolError) as context:
            parse_request({"version": "999", "id": "1", "command": "doctor", "payload": {}})
        self.assertFalse(context.exception.recoverable)


if __name__ == "__main__":
    unittest.main()

