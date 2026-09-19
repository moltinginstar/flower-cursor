import base64

import pytest

from flower_cursor.kitty_graphics import delete_placement, place_image, transmit_png


def test_transmit_png_uses_kitty_png_transport() -> None:
    command = transmit_png(b"png-bytes", image_id=7)

    assert command.startswith(b"\x1b_Ga=T,f=100,i=7,C=1,z=1,q=2,m=0;")
    assert base64.b64decode(command.split(b";", 1)[1][:-2]) == b"png-bytes"
    assert command.endswith(b"\x1b\\")


def test_transmit_png_chunks_large_payloads() -> None:
    commands: list[bytes] = []
    transmit_png(b"x" * 4096, write=commands.append)

    assert len(commands) == 2
    assert b"a=T,f=100,i=1,C=1,z=1,q=2,m=1;" in commands[0]
    assert commands[1].startswith(b"\x1b_Gm=0,q=2;")
    assert all(len(command.split(b";", 1)[1][:-2]) <= 4096 for command in commands)


def test_place_image_supports_multi_cell_offsets_and_foreground_z_index() -> None:
    command = place_image(
        image_id=7,
        placement_id=11,
        columns=3,
        rows=2,
        x_offset=4,
        y_offset=8,
        z_index=1,
    )

    assert command == b"\x1b_Ga=p,i=7,p=11,c=3,r=2,C=1,X=4,Y=8,z=1,q=2;\x1b\\"


def test_place_image_rejects_invalid_geometry() -> None:
    with pytest.raises(ValueError, match="positive"):
        place_image(columns=0, rows=1)
    with pytest.raises(ValueError, match="non-negative"):
        place_image(columns=1, rows=1, x_offset=-1)


def test_delete_placement_targets_only_the_placement() -> None:
    assert delete_placement(image_id=7, placement_id=11) == b"\x1b_Ga=d,d=i,i=7,p=11,q=2;\x1b\\"
