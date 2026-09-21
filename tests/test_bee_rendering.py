from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST


def test_full_real_visit_has_padding_and_rest_tracks_the_live_flower():
    runtime = LayeredRuntime(DEFAULT_MANIFEST)
    compositor = LayeredCompositor(DEFAULT_MANIFEST)
    runtime.move_to(CursorCell(8, 12))
    for i in range(1400):
        runtime.update(1 / 60)
        for visual in runtime.visuals():
            if not isinstance(visual, BeeVisual):
                continue
            bounds = compositor.frame(visual).getchannel("A").getbbox()
            if bounds is not None:
                assert min(bounds[:2]) > 0
                assert max(bounds[2:]) < 256
            if 900 < i < 1100:
                head = compositor._vector(runtime.visuals()[0].samples, "head")
                assert abs(runtime.bee.x - head[0] - 0.018) < 0.003
                assert abs(runtime.bee.y - head[1] + 0.012) < 0.003
