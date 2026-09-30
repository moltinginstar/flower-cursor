import base64
import binascii
import io
import re

import pytest
from PIL import Image

from flower_cursor.kitty_graphics import place_image, transmit_png
from flower_cursor.output_arbiter import OutputArbiter
from flower_cursor.vt_framer import AltScreenChange, DeviceStatusQuery


def _png() -> bytes:
  image = io.BytesIO()
  Image.new("RGBA", (16, 16), (220, 80, 120, 255)).save(image, format="PNG")
  return image.getvalue()


def test_naive_chunk_forwarding_corrupts_a_split_graphics_command() -> None:
  child = transmit_png(_png(), image_id=3)
  flower = place_image(image_id=7, placement_id=7, columns=1, rows=1)
  split = child.index(b";") + 8
  interleaved = child[:split] + flower + child[split:]
  frame = re.search(rb"\x1b_G(.*?)\x1b\\", interleaved, re.DOTALL)
  assert frame is not None

  with pytest.raises((binascii.Error, ValueError)):
    base64.b64decode(frame.group(1).split(b";", 1)[1], validate=True)


def test_split_boundaries_preserve_a_complete_graphics_command() -> None:
  child = transmit_png(_png(), image_id=3)

  for split in range(len(child) + 1):
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append)
    arbiter.feed_child(child[:split])
    arbiter.feed_child(child[split:])

    assert b"".join(writes) == child


def test_child_output_is_forwarded_immediately_rather_than_buffered() -> None:
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append)

  arbiter.feed_child(b"\x1b_Ga=T,f=100;AAAA")

  assert writes == [b"\x1b_Ga=T,f=100;AAAA"]
  assert not arbiter.can_write


@pytest.mark.parametrize(
  "partial",
  [
    b"\x1b[31",  # mid-CSI
    b"\x1b",  # a bare escape
    b"\x1b]52;c;aGVsbG8",  # mid-OSC
    b"\x1bP+q544",  # mid-DCS
    b"\xf0\x9f\x8c",  # mid-UTF-8 character
    b"\x1b_Ga=T,f=100;AAAA",  # mid-APC
  ],
)
def test_writes_are_refused_in_the_middle_of_any_sequence(partial: bytes) -> None:
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append)
  arbiter.feed_child(partial)

  assert not arbiter.can_write
  with pytest.raises(RuntimeError, match="middle of a child escape sequence"):
    arbiter.write(b"flower")
  assert writes == [partial]


def test_multipart_transfer_blocks_renderer_output_until_the_final_chunk() -> None:
  child_chunks: list[bytes] = []
  transmit_png(b"x" * 4096, image_id=3, write=child_chunks.append)
  assert len(child_chunks) == 2
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append)

  arbiter.feed_child(child_chunks[0])
  assert not arbiter.can_write
  with pytest.raises(RuntimeError):
    arbiter.write(b"flower")

  arbiter.feed_child(child_chunks[1][:3])
  arbiter.feed_child(child_chunks[1][3:])

  assert b"".join(writes) == b"".join(child_chunks)
  assert arbiter.can_write


def test_long_multipart_transfers_stream_without_aggregate_buffering() -> None:
  child_chunks: list[bytes] = []
  transmit_png(b"x" * 10000, image_id=3, write=child_chunks.append)
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append)

  for chunk in child_chunks[:-1]:
    arbiter.feed_child(chunk)

  assert writes == child_chunks[:-1]
  assert not arbiter.can_write
  arbiter.feed_child(child_chunks[-1])
  assert writes == child_chunks
  assert arbiter.can_write


def test_child_queries_and_text_stream_between_multipart_chunks() -> None:
  chunks: list[bytes] = []
  transmit_png(b"x" * 10000, image_id=3, write=chunks.append)
  query = b"status\x1b[6n"
  for split in range(len(query) + 1):
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append)
    arbiter.feed_child(chunks[0])
    arbiter.feed_child(query[:split])
    arbiter.feed_child(query[split:])
    assert b"".join(writes) == chunks[0] + query
    assert not arbiter.can_write
    for chunk in chunks[1:]:
      arbiter.feed_child(chunk)
    assert b"".join(writes) == chunks[0] + query + b"".join(chunks[1:])
    assert arbiter.can_write


def test_a_slow_transfer_is_never_truncated() -> None:
  now = [0.0]
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append, clock=lambda: now[0])
  partial = b"\x1b_Ga=T,f=100;AAAA"
  arbiter.feed_child(partial)

  now[0] = 600.0
  arbiter.poll()

  # The upload is still open, so nothing was invented to close it.
  assert b"".join(writes) == partial


def test_an_unfinished_upload_is_abandoned_silently_after_the_timeout() -> None:
  now = [0.0]
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append, stuck_transfer_timeout=5.0, clock=lambda: now[0])
  chunk = b"\x1b_Ga=T,f=100,m=1;AAAA\x1b\\"
  arbiter.feed_child(chunk)

  assert arbiter.recovery_deadline == pytest.approx(5.0)
  assert not arbiter.can_write
  now[0] = 5.0
  arbiter.poll()

  assert arbiter.can_write
  assert writes == [chunk]


def test_an_incomplete_sequence_has_no_recovery_deadline() -> None:
  arbiter = OutputArbiter(lambda data: None)
  arbiter.feed_child(b"\x1b[31")

  # Nothing but the rest of the sequence can make this writable.
  assert arbiter.recovery_deadline is None
  arbiter.poll()
  assert not arbiter.can_write


def test_finish_child_cancels_a_sequence_the_child_never_completed() -> None:
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append)
  arbiter.feed_child(b"\x1b[31")

  arbiter.finish_child()

  assert writes == [b"\x1b[31", b"\x18"]
  assert arbiter.can_write


def test_finish_child_writes_nothing_when_the_stream_is_already_clean() -> None:
  writes: list[bytes] = []
  arbiter = OutputArbiter(writes.append)
  arbiter.feed_child(b"done\r\n")

  arbiter.finish_child()

  assert writes == [b"done\r\n"]


def test_stream_events_reach_the_runtime() -> None:
  events: list[object] = []
  arbiter = OutputArbiter(lambda data: None, on_event=events.append)

  arbiter.feed_child(b"\x1b[?1049h\x1b[6n")

  assert events == [
    AltScreenChange(active=True),
    DeviceStatusQuery(parameter=6, private=False),
  ]
