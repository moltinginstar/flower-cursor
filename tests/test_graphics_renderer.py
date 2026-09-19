import base64
import io
import re
from dataclasses import replace

import pytest
from PIL import Image

from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.lifecycle import CursorCell
from flower_cursor.runtime import FlowerCursorRuntime

A = CursorCell(4, 8)
B = CursorCell(4, 20)


def commands(output: bytes) -> list[tuple[dict[str, str], bytes]]:
    return [
        (dict(item.split("=", 1) for item in parameters.decode().split(",")), payload)
        for parameters, payload in re.findall(rb"\x1b_G([^;]*);(.*?)\x1b\\", output, re.S)
    ]


def transmitted_image(output: bytes) -> Image.Image:
    payload = b"".join(
        payload for parameters, payload in commands(output)
        if parameters.get("a") == "T" or "a" not in parameters
    )
    image = Image.open(io.BytesIO(base64.b64decode(payload)))
    image.load()
    return image


def test_renderer_uses_absolute_placement_and_native_pixels_at_the_root() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)

    renderer.render(runtime.visuals(), B, cell_size=(16, 33))

    output = b"".join(writes)
    parameters = commands(output)[0][0]
    image = transmitted_image(output)
    row, column = map(int, re.search(rb"\x1b\[(\d+);(\d+)H", output).groups())
    root_x = (column - 1) * 16 + int(parameters["X"])
    root_x += runtime.pose_library.root_anchor[0] * image.width
    assert abs(root_x - A.column * 16) <= 0.5
    assert row == A.row
    assert image.size == (72, 64)
    assert image.mode == "RGBA"
    assert parameters["p"] == str(runtime.visuals()[0].identifier)
    assert parameters["C"] == "1"
    assert parameters["z"] == "1"
    assert "c" not in parameters and "r" not in parameters
    assert output.startswith(b"\x1b[s")
    assert output.endswith(b"\x1b[u")


def test_renderer_skips_unchanged_pixels_and_alternates_two_image_slots() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), A)
    first_id = commands(b"".join(writes))[0][0]["i"]
    writes.clear()
    renderer.render(runtime.visuals(), A)
    assert writes == []

    image_ids = [first_id]
    for _ in range(2):
        runtime.update(0.15)
        writes.clear()
        renderer.render(runtime.visuals(), A)
        operations = [p for p, _ in commands(b"".join(writes)) if "a" in p]
        assert [p["a"] for p in operations] == ["T", "d"]
        assert operations[1]["i"] == image_ids[-1]
        assert operations[1]["d"] == "I"
        image_ids.append(operations[0]["i"])
    assert image_ids[0] != image_ids[1]
    assert image_ids[0] == image_ids[2]


def test_each_flower_owns_its_image_even_when_the_pose_is_shared() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    visual = runtime.visuals()[0]
    other = replace(visual, identifier=visual.identifier + 1, cell=B)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)

    renderer.render((visual, other), B)

    transfers = [p for p, _ in commands(b"".join(writes)) if p.get("a") == "T"]
    assert len(transfers) == 2
    assert len({p["i"] for p in transfers}) == 2
    assert {int(p["p"]) for p in transfers} == {visual.identifier, other.identifier}


@pytest.mark.parametrize("clear", [False, True])
def test_renderer_frees_removed_images_and_cleanup_is_idempotent(clear: bool) -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), A)
    image_id = commands(b"".join(writes))[0][0]["i"]
    writes.clear()

    if clear:
        renderer.clear()
    else:
        renderer.render((), A)

    operations = commands(b"".join(writes))
    assert len(operations) == 1
    assert operations[0][0] == {"a": "d", "d": "I", "i": image_id, "p": "1", "q": "2"}
    writes.clear()
    renderer.clear()
    assert writes == []


@pytest.mark.parametrize("reset", [
    b"\x1b[2J", b"\x1b[3J", b"\x1bc", b"\x1b[?47h", b"\x1b[?47l",
    b"\x1b[?1047h", b"\x1b[?1047l", b"\x1b[?1049h", b"\x1b[?1049l",
])
def test_screen_reset_split_across_chunks_reuploads_unchanged_frame(reset: bytes) -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), A)
    writes.clear()

    renderer.observe_terminal_output(reset[:-1])
    renderer.render(runtime.visuals(), A)
    assert writes == []
    renderer.observe_terminal_output(reset[-1:])
    renderer.render(runtime.visuals(), A)
    assert any(p.get("a") == "T" for p, _ in commands(b"".join(writes)))


def test_ordinary_output_does_not_invalidate_the_frame() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), A)
    writes.clear()
    renderer.observe_terminal_output(b"text\x1b[H\x1b[31m")
    renderer.render(runtime.visuals(), A)
    assert writes == []


def test_cell_resize_reuploads_native_pixel_geometry() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), A, cell_size=(8, 16))
    assert transmitted_image(b"".join(writes)).size == (33, 30)
    writes.clear()
    renderer.render(runtime.visuals(), A, cell_size=(16, 33))
    assert transmitted_image(b"".join(writes)).size == (72, 64)


def test_left_edge_crops_image_without_shifting_the_root() -> None:
    runtime = FlowerCursorRuntime()
    cell = CursorCell(4, 0)
    runtime.move_to(cell)
    runtime.update(0.35)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), cell, cell_size=(16, 33))

    output = b"".join(writes)
    image = transmitted_image(output)
    parameters = commands(output)[0][0]
    cropped = 72 - image.width
    assert 0 < cropped < 72
    assert abs(runtime.pose_library.root_anchor[0] * 72 - cropped) <= 0.5
    assert int(parameters["X"]) == 0
    assert b"\x1b[4;1H" in output
