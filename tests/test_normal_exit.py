import hashlib
import json

import pytest
from PIL import Image

from flower_cursor.layered import LayeredCompositor, LayeredRuntime, LayeredVisual
from flower_cursor.lifecycle import CursorCell
from flower_cursor.settings import FLOWER_MANIFESTS

HEALTHY = {
    "anemone": "3acedcde0b3e166c1e5bd1e0a55452b29f4713e76b99bc4f7f1466b76d8421e4",
    "cornflower": "0c4a74ddcee0b931f0c705c86aa25a18edde93096acc9d92ef6182f4dcf770ff",
    "daisy": "f893cfcfea598e6e064ddcbbd4cab4090f1a970b36687c7013cab7edfda104f4",
    "crocus": "fa0986f5524fe6738a9a56915183681f84e8efb9aa118dfba9418c0af82376fb",
    "poppy": "84313dda72a352b759e7d6bfa6623f7e584ccc6bc4009ba6ead04d3efa9eaa08",
    "dahlia": "f4e1e231c08d95ab3daa4c3fdc96d8fbec255b03177c15154e3301f085a2823d",
    "orchid": "353de253a3e1ee6ec7d0937c90f7ce312d1d49b06d068cca1b9d563592760bb2",
}


@pytest.mark.parametrize("species", HEALTHY)
def test_normal_healthy_art_and_pose_records_are_preserved(species):
    path = FLOWER_MANIFESTS[species]
    data = json.loads(path.read_text())
    digest = hashlib.sha256()
    for pose in data["poses"]:
        if pose["vitality"] >= 0.55:
            digest.update(json.dumps(pose, sort_keys=True).encode())
            for name in pose["layers"].values():
                digest.update((path.parent / name).read_bytes())
    assert digest.hexdigest() == HEALTHY[species]


@pytest.mark.parametrize("species", HEALTHY)
@pytest.mark.parametrize("age", ("young", "half", "full"))
def test_normal_stalk_is_near_root_before_final_thinning(species, age):
    path = FLOWER_MANIFESTS[species]
    data = json.loads(path.read_text())
    records = {pose["name"]: pose for pose in data["poses"]}
    root_y = data["root_anchor"][1] * 256
    for index, reach in ((19, 15), (20, 10), (23, 6)):
        pose = records[f"exit_{age}_{index:02d}"]
        with Image.open(path.parent / pose["layers"]["body"]) as image:
            bounds = image.getchannel("A").getbbox()
        if bounds is not None:
            assert root_y - bounds[1] < reach, (species, age, index, bounds)


@pytest.mark.parametrize("species", HEALTHY)
@pytest.mark.parametrize("warmup", (0.12, 0.45, 2.0))
def test_normal_repeated_late_returns_preserve_pixels_state_and_flights(species, warmup):
    path = FLOWER_MANIFESTS[species]
    runtime = LayeredRuntime(path, species=species, bee=False)
    compositor = LayeredCompositor(path, species=species)
    home, away = CursorCell(10, 20), CursorCell(10, 40)
    runtime.move_to(home)
    runtime.update(warmup)
    flower = runtime.active
    assert flower is not None

    def frame():
        visual = next(
            v for v in runtime.visuals() if isinstance(v, LayeredVisual) and v.flower is flower
        )
        return compositor.frame(visual).tobytes()

    for departure in (0.10, 0.22, 0.38):
        runtime.move_to(away)
        runtime.update(departure)
        assert not flower.flower.dead
        pose = flower.pose
        velocities = tuple(
            spring.velocity
            for spring in (flower.flower._stem, flower.flower._vitality, flower._lean)
        )
        flights = [(flight.identifier, flight.age) for flight in flower.flights]
        before = frame()
        runtime.move_to(home)
        assert runtime.active is flower
        assert flower.pose == pose
        assert velocities == tuple(
            spring.velocity
            for spring in (flower.flower._stem, flower.flower._vitality, flower._lean)
        )
        assert [(flight.identifier, flight.age) for flight in flower.flights] == flights
        assert frame() == before
        runtime.update(0.01)
        ages = dict(flights)
        assert all(f.age > ages[f.identifier] for f in flower.flights if f.identifier in ages)
        runtime.update(1.0)
        assert flower.pose.vitality > 0.99
