import random

import pytest

from flower_cursor.bee import IdleBee
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.lifecycle import CursorCell, FlowerInstance
from flower_cursor.observer import DEFAULT_MANIFEST, FLOWER_MANIFESTS, build_parser
from flower_cursor.shedding import SheddingFlower
from flower_cursor.species import LEGACY_CORAL_SHEDDING_ORDER, metadata_from_manifest

CORNFlower_ORDER = (1, 7, 3, 9, 5, 11, 0, 6, 2, 8, 4, 10, 12, 15, 13, 16, 14, 17)


def test_legacy_manifest_metadata_preserves_coral_shedding_order() -> None:
    metadata = metadata_from_manifest({})

    assert metadata.species_id == "coral"
    assert [group.layer for group in metadata.shedding_groups] == [f"petal_{i}" for i in range(6)]
    assert metadata.shedding_order == LEGACY_CORAL_SHEDDING_ORDER
    assert metadata.landing_support_bounds is None


def test_cornflower_metadata_accepts_eighteen_groups_and_support_bounds() -> None:
    groups = [
        {
            "id": f"outer_{i:02d}" if i < 12 else f"center_{i - 12:02d}",
            "layer": f"petal_{i}",
            "order": i,
        }
        for i in range(18)
    ]
    metadata = metadata_from_manifest(
        {
            "species": "cornflower",
            "shedding_groups": groups,
            "shedding_order": list(CORNFlower_ORDER),
            "landing_support_bounds": {"x": [-0.008, 0.008], "y": [-0.006, 0.006]},
        },
        selected_species="cornflower",
    )

    assert len(metadata.shedding_groups) == 18
    assert metadata.shedding_groups[12].identifier == "center_00"
    assert metadata.shedding_order == CORNFlower_ORDER
    assert metadata.landing_support_bounds == ((-0.008, 0.008), (-0.006, 0.006))


@pytest.mark.parametrize(
    "manifest, message",
    [
        ({"shedding_groups": []}, "non-empty"),
        (
            {
                "shedding_groups": [{"id": "a", "layer": "petal_0", "order": 1}],
            },
            "order",
        ),
        (
            {
                "shedding_groups": [
                    {"id": "a", "layer": "petal_0", "order": 0},
                    {"id": "a", "layer": "petal_1", "order": 1},
                ],
            },
            "ids must be unique",
        ),
        (
            {
                "shedding_groups": [{"id": "a", "layer": "petal_0", "order": 0}],
                "shedding_order": [1],
            },
            "permutation",
        ),
        ({"landing_support_bounds": {"x": [-0.6, 0.1], "y": [0, 0]}}, "finite ordered"),
    ],
)
def test_species_metadata_rejects_invalid_schema(manifest, message) -> None:
    with pytest.raises(ValueError, match=message):
        metadata_from_manifest(manifest)


def test_custom_shedding_count_and_order_are_used() -> None:
    flower = SheddingFlower(
        FlowerInstance.bud(1, CursorCell(4, 8)),
        group_count=3,
        shedding_order=(2, 0, 1),
    )
    flower.update(3.0)
    flower.begin_withering()
    flower.update(0.4)

    assert [flight.index for flight in flower.flights] == [2, 0, 1]
    assert len(flower.petals) == 3


def test_species_support_bounds_clamp_bee_landing_offsets() -> None:
    bee = IdleBee(random.Random(9))
    bee.set_geometry(ViewportGeometry(30, 100, 1600, 990), (50, 45))
    bee.set_landing_support_bounds(((-0.25, 0.25), (-0.2, 0.2)))
    bee.move_to(CursorCell(8, 12))
    for _ in range(11):
        bee.update(1.0, (192.0, 250.0))

    assert bee.visible
    assert bee.landing_support_bounds == ((-0.25, 0.25), (-0.2, 0.2))
    assert -0.25 <= bee.landing_offset[0] <= 0.25
    assert -0.2 <= bee.landing_offset[1] <= 0.2


def test_flower_selectors_keep_coral_bundle_and_reject_custom_conflict() -> None:
    assert FLOWER_MANIFESTS["coral"] == DEFAULT_MANIFEST
    assert FLOWER_MANIFESTS["cornflower"] == (
        DEFAULT_MANIFEST.parent.parent / "cornflower" / "manifest.json"
    )
    assert build_parser().parse_args(["--flower", "cornflower"]).flower == "cornflower"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--flower", "coral", "--shedding-manifest", "custom.json"])
