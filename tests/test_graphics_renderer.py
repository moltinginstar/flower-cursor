from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.lifecycle import CursorCell, FlowerPose
from flower_cursor.runtime import FlowerCursorRuntime, FlowerVisual

A = CursorCell(4, 8)
B = CursorCell(4, 20)


def test_renderer_places_once_until_the_authored_pose_changes() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.05)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)

    renderer.render(runtime.visuals(), A)
    first_output = b"".join(writes)
    writes.clear()
    renderer.render(runtime.visuals(), A)

    assert b"\x1b[s\x1b[1A\x1b[3D" in first_output
    assert b"a=T,f=100,i=1001,p=1,c=5,r=2,X=8,Y=2,C=1,z=1" in first_output
    assert first_output.endswith(b"\x1b[u")
    assert writes == []

    runtime.update(0.4)
    renderer.render(runtime.visuals(), A)
    assert b"a=T,f=100,i=1001,p=1,c=5,r=2" in b"".join(writes)


def test_renderer_repositions_idle_motion_without_retransmitting_png() -> None:
    runtime = FlowerCursorRuntime()
    runtime.move_to(A)
    runtime.update(0.05)
    visual = runtime.visuals()[0]
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render((visual,), A)
    writes.clear()

    moved = FlowerVisual(
        identifier=visual.identifier,
        cell=visual.cell,
        pose=FlowerPose(
            stem=visual.pose.stem,
            bloom=visual.pose.bloom,
            vitality=visual.pose.vitality,
            idle_phase=1.7,
            idle_sway=0.03,
        ),
        asset=visual.asset,
    )
    renderer.render((moved,), A)

    output = b"".join(writes)
    assert b"a=p,i=1001,p=1,c=5,r=2,C=1,X=9,Y=3,z=1" in output
    assert b"a=T" not in output


def test_renderer_deletes_flowers_removed_from_the_runtime() -> None:
    runtime = FlowerCursorRuntime(retain_departures=True)
    runtime.move_to(A)
    runtime.update(0.1)
    writes: list[bytes] = []
    renderer = FlowerRenderer(runtime.pose_library, writes.append)
    renderer.render(runtime.visuals(), A)

    runtime.move_to(B)
    runtime.update(0.1)
    renderer.render(runtime.visuals(), B)
    runtime.update(3.0)
    writes.clear()
    renderer.render(runtime.visuals(), B)

    assert b"a=d,d=i,i=1001,p=1,q=2;" in b"".join(writes)
