import math
from dataclasses import replace

import pytest
from PIL import ImageStat

from flower_cursor.easing import smoothstep
from flower_cursor.layered import LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.manifest import manifest_data
from flower_cursor.pose_library import PoseLibrary
from flower_cursor.settings import FLOWER_MANIFESTS

LEAF_TOP = {
    "anemone": 60,
    "cornflower": 50,
    "daisy": 70,
    "crocus": 80,
    "poppy": 80,
    "dahlia": 75,
    "orchid": 90,
}


@pytest.mark.parametrize("species", FLOWER_MANIFESTS)
@pytest.mark.parametrize("fps", [30, 60, 120, 240])
def test_real_entry_has_one_gradual_handoff_without_shrinking(species, fps):
    manifest = FLOWER_MANIFESTS[species]
    runtime = LayeredRuntime(manifest, bee=False)
    compositor = LayeredCompositor(manifest)
    runtime.move_to(CursorCell(10, 10))
    records = {record["name"]: record for record in manifest_data(manifest)["poses"]}
    previous = None
    leaf_areas = []
    for frame in range(round(1.2 * fps)):
        runtime.update(1 / fps)
        visual = runtime.visuals()[0]
        pose = visual.pose
        if 0.95 <= pose.bloom <= 0.995:
            idle_weight = sum(w for a, w in visual.samples if a.name.startswith("idle_"))
            assert idle_weight == pytest.approx(smoothstep((pose.bloom - 0.95) / 0.045))
            assert all(a.name.startswith(("grow_", "idle_")) for a, _ in visual.samples)
        head = tuple(
            sum(records[a.name]["head"][axis] * weight * 256 for a, weight in visual.samples)
            for axis in (0, 1)
        )
        if (frame + 1) / fps >= 0.6:
            body = compositor._whole(compositor._blend(visual.samples, "body"))
            alpha = body.getchannel("a").crop((0, LEAF_TOP[species], 256, 128))
            leaf_areas.append(ImageStat.Stat(alpha).sum[0])
            if previous is not None:
                assert math.dist(head, previous) * fps < 20
        previous = head
    assert min(leaf_areas) >= leaf_areas[0] * 0.98


@pytest.mark.parametrize("species", FLOWER_MANIFESTS)
def test_settled_idle_keeps_phase_linear_sampling(species):
    runtime = LayeredRuntime(FLOWER_MANIFESTS[species], bee=False)
    original = PoseLibrary(runtime.pose_library.assets)
    runtime.move_to(CursorCell(10, 10))
    runtime.update(2)
    for _ in range(240):
        runtime.update(1 / 60)
        visual = runtime.visuals()[0]
        assert visual.samples == original.sample_animated(visual.pose)


@pytest.mark.parametrize("species", FLOWER_MANIFESTS)
@pytest.mark.parametrize("channel", ["bloom", "vitality"])
@pytest.mark.parametrize("boundary", [0.90, 0.95, 0.995])
def test_handoff_boundaries_preserve_continuity(species, channel, boundary):
    runtime = LayeredRuntime(FLOWER_MANIFESTS[species], bee=False)
    runtime.move_to(CursorCell(10, 10))
    runtime.update(0.73)
    pose = runtime.active.pose
    before = replace(pose, **{channel: boundary - 1e-8})
    after = replace(pose, **{channel: boundary + 1e-8})
    left = dict(runtime.pose_library.sample_animated(before))
    right = dict(runtime.pose_library.sample_animated(after))
    assert sum(abs(left.get(a, 0) - right.get(a, 0)) for a in left.keys() | right.keys()) < 1e-4
