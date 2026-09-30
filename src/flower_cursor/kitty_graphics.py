"""Helpers for rendering the flower as Kitty foreground graphics.

Image ids are a namespace shared with every other program drawing into the same
window, and the protocol gives no way to ask which ids are free, so callers must
choose one deliberately - there is no default. Placement ids need no such care:
the protocol identifies a placement by the (image id, placement id) pair.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

_ESC = b"\x1b"
_ST = _ESC + b"\\"
_MAX_ENCODED_CHUNK = 4096
MAX_IMAGE_ID = 2**32 - 1
_MAX_ID = MAX_IMAGE_ID


def _graphics_command(parameters: str, encoded_payload: bytes = b"") -> bytes:
  return _ESC + b"_G" + parameters.encode("ascii") + b";" + encoded_payload + _ST


def _validate_id(name: str, value: int) -> None:
  if not 1 <= value <= _MAX_ID:
    raise ValueError(f"{name} must be between 1 and {_MAX_ID}")


def transmit_png(
  png: bytes,
  *,
  image_id: int,
  placement_id: int | None = None,
  columns: int | None = None,
  rows: int | None = None,
  x_offset: int = 0,
  y_offset: int = 0,
  z_index: int = 1,
  place: bool = True,
  write: Callable[[bytes], object] | None = None,
) -> bytes:
  """Transmit PNG data to Kitty in protocol-sized base64 chunks.

  With *place* false the data is stored but nothing is drawn, which is how one
  image is shared by many placements.
  """
  _validate_id("image_id", image_id)
  if placement_id is not None:
    _validate_id("placement_id", placement_id)
  if columns is None and rows is not None:
    raise ValueError("columns and rows must be provided together")
  if columns is not None:
    if rows is None:
      raise ValueError("columns and rows must be provided together")
    if columns < 1 or rows < 1:
      raise ValueError("columns and rows must be positive")
  if x_offset < 0 or y_offset < 0:
    raise ValueError("pixel offsets must be non-negative")
  if not png:
    raise ValueError("png must not be empty")

  encoded = base64.b64encode(png)
  commands: list[bytes] = []
  for offset in range(0, len(encoded), _MAX_ENCODED_CHUNK):
    chunk = encoded[offset : offset + _MAX_ENCODED_CHUNK]
    more = offset + len(chunk) < len(encoded)
    if offset == 0:
      placement = f",p={placement_id}" if placement_id is not None else ""
      geometry = f",c={columns},r={rows}" if columns is not None else ""
      pixel_offsets = f",X={x_offset},Y={y_offset}" if x_offset or y_offset else ""
      parameters = (
        f"a={'T' if place else 't'},f=100,i={image_id}{placement}{geometry}{pixel_offsets},"
        f"C=1,z={z_index},"
        f"q=2,m={int(more)}"
      )
    else:
      parameters = f"m={int(more)},q=2"
    command = _graphics_command(parameters, chunk)
    commands.append(command)
    if write is not None:
      write(command)
  return b"".join(commands)


def place_image(
  *,
  image_id: int,
  placement_id: int = 1,
  columns: int | None = None,
  rows: int | None = None,
  x_offset: int = 0,
  y_offset: int = 0,
  z_index: int = 1,
  write: Callable[[bytes], object] | None = None,
) -> bytes:
  """Place a transmitted image without changing the terminal cursor position."""
  _validate_id("image_id", image_id)
  _validate_id("placement_id", placement_id)
  if (columns is None) != (rows is None):
    raise ValueError("columns and rows must be provided together")
  if columns is not None and rows is not None and (columns < 1 or rows < 1):
    raise ValueError("columns and rows must be positive")
  if x_offset < 0 or y_offset < 0:
    raise ValueError("pixel offsets must be non-negative")

  geometry = f",c={columns},r={rows}" if columns is not None else ""
  command = _graphics_command(
    f"a=p,i={image_id},p={placement_id}{geometry},C=1,X={x_offset},Y={y_offset},z={z_index},q=2"
  )
  if write is not None:
    write(command)
  return command


def delete_placement(
  *,
  image_id: int,
  placement_id: int = 1,
  free_data: bool = False,
  write: Callable[[bytes], object] | None = None,
) -> bytes:
  """Delete one flower placement."""
  _validate_id("image_id", image_id)
  _validate_id("placement_id", placement_id)
  selector = "I" if free_data else "i"
  command = _graphics_command(f"a=d,d={selector},i={image_id},p={placement_id},q=2")
  if write is not None:
    write(command)
  return command


def delete_image(*, image_id: int, write: Callable[[bytes], object] | None = None) -> bytes:
  """Delete an image and every placement of it in one command."""
  _validate_id("image_id", image_id)
  command = _graphics_command(f"a=d,d=I,i={image_id},q=2")
  if write is not None:
    write(command)
  return command
