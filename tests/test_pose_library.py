from pathlib import Path

import pytest

from flower_cursor.lifecycle import FlowerPose
from flower_cursor.pose_library import PoseLibrary

ROOT = Path(__file__).parents[1]


def pose(stem: float, bloom: float, vitality: float, idle_phase: float = 0.0) -> FlowerPose:
    return FlowerPose(
        stem=stem,
        bloom=bloom,
        vitality=vitality,
        idle_phase=idle_phase,
        idle_sway=0.0,
    )


def test_default_library_loads_all_authored_poses() -> None:
    library = PoseLibrary.default()

    names = {asset.name for asset in library.assets}

    assert {"bud", "bloom", "mature", "wilt"}.issubset(names)
    assert {"bloom_01", "bloom_06", "wilt_01", "wilt_03"}.issubset(names)
    assert len(names) == 51
    assert all(asset.path.is_file() for asset in library.assets)


def test_pose_selection_uses_continuous_state_nearest_asset() -> None:
    library = PoseLibrary.default()

    assert library.select(pose(0.04, 0.03, 1.0)).name == "bud"
    assert library.select(pose(0.54, 0.54, 1.0)).name == "bloom_11"
    assert library.select(pose(1.0, 1.0, 1.0)).name == "mature"
    assert library.select(pose(0.16, 0.28, 0.35)).name == "wilt_12"
    assert library.select(pose(1.0, 1.0, 1.0, idle_phase=1.0)).name == "mature_idle_02"


def test_empty_library_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        PoseLibrary(())
