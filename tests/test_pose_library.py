import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from flower_cursor.lifecycle import FlowerPose
from flower_cursor.pose_library import PoseAsset, PoseLibrary

ROOT = Path(__file__).parents[1]


def pose_for(asset: PoseAsset) -> FlowerPose:
    return FlowerPose(
        stem=asset.stem,
        bloom=asset.bloom,
        vitality=asset.vitality,
        idle_phase=asset.idle_phase,
        idle_sway=0.0,
        leaf_unfurl=asset.leaf_unfurl,
        petal_open=asset.petal_open,
        head_lean=asset.head_lean,
        stem_bend=asset.stem_bend,
        maturity=asset.maturity,
    )


def test_default_library_loads_complete_manifest_and_decodable_rgba_assets() -> None:
    library = PoseLibrary.default()
    manifest = json.loads((ROOT / "assets/renders/manifest.json").read_text())
    names = {asset.name for asset in library.assets}
    assert len(names) == len(library.assets) == len(manifest["poses"])
    assert {"bud", "bloom", "mature", "wilt"}.issubset(names)
    assert library.root_anchor == tuple(manifest["root_anchor"])
    for asset in library.assets:
        with Image.open(asset.path) as image:
            assert image.mode == "RGBA"
            assert image.size == (256, 256)
            image.verify()


def test_atlas_covers_partial_exits_and_late_revivals() -> None:
    names = {asset.name for asset in PoseLibrary.default().assets}
    departures = ("wilt", "partial_06", "partial_13", "partial_22")
    revivals = (*departures, "late_wilt", "late_partial_22")
    for prefix in departures:
        assert {f"{prefix}_00_half", f"{prefix}_40"}.issubset(names)
    for prefix in revivals:
        assert {f"revive_{prefix}_00_half", f"revive_{prefix}_36"}.issubset(names)
    assert {f"mature_idle_{index:02d}" for index in range(24)}.issubset(names)


@pytest.mark.parametrize("name", [
    "bloom_13", "mature", "mature_idle_06", "wilt_20", "partial_13_12",
    "revive_wilt_10", "revive_late_wilt_10", "revive_late_partial_22_10",
])
def test_exact_authored_state_selects_a_single_matching_sample(name: str) -> None:
    library = PoseLibrary.default()
    asset = next(asset for asset in library.assets if asset.name == name)
    samples = library.sample(pose_for(asset))
    assert samples == ((asset, 1.0),)


@pytest.mark.parametrize("channel", ["collapse", "petal_curl", "retraction"])
def test_exit_shape_channels_influence_selection(channel: str) -> None:
    library = PoseLibrary.default()
    asset = min(
        (asset for asset in library.assets if 0 < asset.retraction < 1),
        key=lambda asset: abs(asset.vitality - 0.01),
    )
    wrong = replace(asset, name="wrong", **{channel: 0.0})
    selection = PoseLibrary((wrong, asset)).select(pose_for(asset))
    assert selection is asset


def test_interpolated_samples_have_positive_normalized_weights() -> None:
    library = PoseLibrary.default()
    asset = next(asset for asset in library.assets if asset.name == "bloom_13")
    pose = replace(pose_for(asset), stem=asset.stem + 0.003, bloom=asset.bloom + 0.007)
    samples = library.sample(pose)
    assert 1 < len(samples) <= 3
    assert all(0 < weight < 1 for _, weight in samples)
    assert sum(weight for _, weight in samples) == pytest.approx(1.0)
    assert samples[0][0] == library.select(pose)


@pytest.mark.parametrize("phase, expected", [(0, 0), (math.pi / 4, 0.25),
                                             (math.pi / 2, 0.5), (math.pi, 1)])
def test_animated_idle_interpolates_phase_without_exit_neighbors(phase, expected) -> None:
    left = PoseAsset("idle_00", 1, 1, 1, Path("left.png"), idle_phase=0)
    right = replace(left, name="idle_12", path=Path("right.png"), idle_phase=math.pi)
    poison = replace(left, name="exit_full_00", idle_phase=phase)
    library = PoseLibrary((left, right, poison))
    samples = dict(library.sample_animated(pose_for(poison)))
    assert poison not in samples
    assert samples.get(left, 0) == pytest.approx(1 - expected)
    assert samples.get(right, 0) == pytest.approx(expected)


def test_animated_idle_wraps_and_preserves_non_idle_sampling() -> None:
    left = PoseAsset("idle_00", 1, 1, 1, Path("left.png"), idle_phase=0)
    right = replace(left, name="idle_12", idle_phase=math.pi)
    young = replace(left, name="grow_00", bloom=0.2)
    library = PoseLibrary((left, right, young))
    samples = dict(library.sample_animated(replace(pose_for(left), idle_phase=-math.pi / 4)))
    assert samples[left] == pytest.approx(0.75)
    assert samples[right] == pytest.approx(0.25)
    assert library.sample_animated(pose_for(young)) == library.sample(pose_for(young))
    assert PoseLibrary((young,)).sample_animated(pose_for(left)) == ((young, 1.0),)


def test_animated_idle_entry_blends_continuously() -> None:
    left = PoseAsset("idle_00", 1, 1, 1, Path("left.png"))
    right = replace(left, name="idle_12", idle_phase=math.pi)
    growing = replace(left, name="grow_35", bloom=0.95, idle_phase=math.pi / 2)
    library = PoseLibrary((left, right, growing))
    for boundary in (0.95, 0.995):
        before = dict(library.sample_animated(replace(pose_for(growing), bloom=boundary - 1e-7)))
        after = dict(library.sample_animated(replace(pose_for(growing), bloom=boundary + 1e-7)))
        assert sum(before.values()) == pytest.approx(1)
        assert sum(after.values()) == pytest.approx(1)
        assert max(abs(before.get(a, 0) - after.get(a, 0)) for a in library.assets) < 1e-4


def test_empty_library_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        PoseLibrary(())
