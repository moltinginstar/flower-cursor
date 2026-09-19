import base64
import binascii
import io
import re

import pytest
from PIL import Image

from flower_cursor.kitty_graphics import place_image, transmit_png
from flower_cursor.output_arbiter import OutputArbiter


def _png() -> bytes:
    image = io.BytesIO()
    Image.new("RGBA", (16, 16), (220, 80, 120, 255)).save(image, format="PNG")
    return image.getvalue()


def test_naive_chunk_forwarding_corrupts_a_split_graphics_command() -> None:
    child = transmit_png(_png())
    flower = place_image(image_id=7, placement_id=7, columns=1, rows=1)
    split = child.index(b";") + 8
    interleaved = child[:split] + flower + child[split:]
    frame = re.search(rb"\x1b_G(.*?)\x1b\\", interleaved, re.S)
    assert frame is not None

    with pytest.raises((binascii.Error, ValueError)):
        base64.b64decode(frame.group(1).split(b";", 1)[1], validate=True)


def test_split_boundaries_preserve_a_complete_graphics_command() -> None:
    child = transmit_png(_png())

    for split in range(len(child) + 1):
        writes: list[bytes] = []
        arbiter = OutputArbiter(writes.append)
        arbiter.feed_child(child[:split])
        arbiter.feed_child(child[split:])

        assert b"".join(writes) == child
        assert arbiter.can_write


def test_multipart_transfer_blocks_external_output_until_final_chunk() -> None:
    child_chunks: list[bytes] = []
    transmit_png(b"x" * 4096, write=child_chunks.append)
    assert len(child_chunks) == 2
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append)

    arbiter.feed_child(child_chunks[0])
    assert writes == [child_chunks[0]]
    assert not arbiter.can_write
    with pytest.raises(RuntimeError, match="incomplete"):
        arbiter.write(b"flower")

    arbiter.feed_child(child_chunks[1][:3])
    assert writes == [child_chunks[0]]
    arbiter.feed_child(child_chunks[1][3:])

    assert b"".join(writes) == b"".join(child_chunks)
    assert arbiter.can_write


def test_complete_multipart_chunks_stream_without_aggregate_buffering() -> None:
    child_chunks: list[bytes] = []
    transmit_png(b"x" * 10000, write=child_chunks.append)
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, max_buffered_bytes=16)

    for chunk in child_chunks[:-1]:
        arbiter.feed_child(chunk)

    assert writes == child_chunks[:-1]
    assert not arbiter.can_write
    arbiter.feed_child(child_chunks[-1])
    assert writes == child_chunks
    assert arbiter.can_write


def test_incomplete_graphics_timeout_releases_the_output_lock() -> None:
    now = [0.0]
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, incomplete_timeout=0.25, clock=lambda: now[0])
    partial = b"\x1b_Ga=T,f=100;AAAA"

    arbiter.feed_child(partial)
    assert not arbiter.can_write
    now[0] = 0.25
    arbiter.poll()

    assert b"".join(writes) == partial + b"\x1b\\"
    assert arbiter.can_write


def test_completed_partial_command_resets_timeout_for_the_next_command() -> None:
    now = [0.0]
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, incomplete_timeout=0.25, clock=lambda: now[0])
    command = b"\x1b_Ga=T,f=100;AAAA\x1b\\"

    arbiter.feed_child(command[:-2])
    now[0] = 0.1
    arbiter.feed_child(command[-2:])
    assert arbiter.can_write

    now[0] = 1.0
    next_partial = b"\x1b_Ga=T,f=100;BBBB"
    arbiter.feed_child(next_partial)
    arbiter.poll()

    assert not arbiter.can_write
    assert b"".join(writes) == command


@pytest.mark.parametrize("next_partial", [b"\x1b_Ga=T,f=100;BBBB", b"\x1b"])
def test_coalesced_command_boundary_starts_a_fresh_timeout(next_partial) -> None:
    now = [0.0]
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, clock=lambda: now[0])
    command = b"\x1b_Ga=T,f=100;AAAA\x1b\\"
    arbiter.feed_child(command[:-2])
    now[0] = 0.24
    arbiter.feed_child(command[-2:] + next_partial)
    now[0] = 0.26
    arbiter.poll()
    assert not arbiter.can_write
    assert arbiter.recovery_deadline == pytest.approx(0.49)
    assert b"".join(writes) == command


def test_completed_plain_escape_prefix_does_not_age_the_next_prefix() -> None:
    now = [0.0]
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, clock=lambda: now[0])
    arbiter.feed_child(b"\x1b")
    now[0] = 0.24
    arbiter.feed_child(b"[6n\x1b")
    now[0] = 0.26
    arbiter.poll()
    assert not arbiter.can_write
    assert arbiter.recovery_deadline == pytest.approx(0.49)
    assert b"".join(writes) == b"\x1b[6n"


def test_child_queries_and_text_stream_between_multipart_chunks() -> None:
    chunks: list[bytes] = []
    transmit_png(b"x" * 10000, write=chunks.append)
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


def test_prefix_to_multipart_transition_uses_a_fresh_timeout() -> None:
    now = [0.0]
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, incomplete_timeout=0.25, clock=lambda: now[0])

    arbiter.feed_child(b"\x1b")
    now[0] = 0.2
    arbiter.feed_child(b"_Gm=1;AAAA\x1b\\")
    now[0] = 0.3
    arbiter.poll()

    assert not arbiter.can_write
    assert writes == [b"\x1b_Gm=1;AAAA\x1b\\"]


def test_buffer_limit_releases_an_unbounded_incomplete_stream() -> None:
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, max_buffered_bytes=16, incomplete_timeout=10)
    partial = b"\x1b_Ga=T,f=100;" + b"x" * 32

    arbiter.feed_child(partial)

    assert b"".join(writes) == partial + b"\x1b\\"
    assert arbiter.can_write


def test_multipart_timeout_sends_a_bounded_recovery_final_chunk() -> None:
    child_chunks: list[bytes] = []
    transmit_png(b"x" * 4096, write=child_chunks.append)
    now = [0.0]
    writes: list[bytes] = []
    arbiter = OutputArbiter(writes.append, incomplete_timeout=0.25, clock=lambda: now[0])

    arbiter.feed_child(child_chunks[0])
    now[0] = 0.25
    arbiter.poll()

    assert writes == [child_chunks[0], b"\x1b_Gm=0,q=2;\x1b\\"]
    assert arbiter.can_write
