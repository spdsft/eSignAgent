"""
Chrome Native Messaging protocol implementation.

Wire format on stdin/stdout:
  - 4 bytes: little-endian uint32 length of the JSON payload
  - N bytes: UTF-8 encoded JSON

The browser extension writes/reads this format; we mirror it exactly.

Reference: https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
"""
import json
import struct
import sys
from typing import Optional


# Use binary stdin/stdout — text mode would corrupt the length-prefix
_stdin = sys.stdin.buffer
_stdout = sys.stdout.buffer


MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MB — Chrome's hard limit is 1 MB inbound


def read_message() -> Optional[dict]:
    """Read one Native Messaging message. Returns None on clean EOF."""
    raw_length = _stdin.read(4)
    if len(raw_length) == 0:
        return None  # browser closed the pipe
    if len(raw_length) != 4:
        raise IOError(f"Truncated length header ({len(raw_length)} bytes)")

    (msg_length,) = struct.unpack("<I", raw_length)
    if msg_length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {msg_length} bytes")

    payload = _stdin.read(msg_length)
    if len(payload) != msg_length:
        raise IOError(
            f"Truncated payload ({len(payload)}/{msg_length} bytes)"
        )
    return json.loads(payload.decode("utf-8"))


def send_message(message: dict) -> None:
    """Send one Native Messaging message."""
    encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
    _stdout.write(struct.pack("<I", len(encoded)))
    _stdout.write(encoded)
    _stdout.flush()
