"""How a wrapper and the shared renderer talk: one framed message at a time.

Each message is two big-endian 32-bit lengths, a JSON header of that first
length, and an opaque payload of the second - graphics bytes on the way back,
nothing on the way there. JSON rather than pickle because the renderer
decodes whatever arrives on its socket; the socket is the user's own, but a
format that cannot run code is still the one to read it with.
"""

from __future__ import annotations

import json
import socket
import struct

_LENGTHS = struct.Struct("!II")
# Far above any real header; a larger one means the stream is not ours.
_MAX_HEADER = 1 << 20
_MAX_PAYLOAD = 1 << 28


class WireError(ConnectionError):
  """The other end went away, or sent something that is not a message."""


def send(connection: socket.socket, header: dict, payload: bytes = b"") -> None:
  encoded = json.dumps(header, separators=(",", ":")).encode()
  connection.sendall(_LENGTHS.pack(len(encoded), len(payload)) + encoded + payload)


def receive(connection: socket.socket) -> tuple[dict, bytes]:
  header_length, payload_length = _LENGTHS.unpack(_exactly(connection, _LENGTHS.size))
  if header_length > _MAX_HEADER or payload_length > _MAX_PAYLOAD:
    raise WireError("message too large")
  try:
    header = json.loads(_exactly(connection, header_length))
  except ValueError as error:
    raise WireError(f"unreadable header ({error})") from None
  if not isinstance(header, dict):
    raise WireError("header is not an object")
  return header, _exactly(connection, payload_length)


def _exactly(connection: socket.socket, length: int) -> bytes:
  buffer = bytearray()
  while len(buffer) < length:
    chunk = connection.recv(min(length - len(buffer), 1 << 20))
    if not chunk:
      raise WireError("connection closed")
    buffer.extend(chunk)
  return bytes(buffer)
