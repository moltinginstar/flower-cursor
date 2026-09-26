import hashlib
import json

import pytest
from PIL import Image

from flower_cursor.layered import LayeredCompositor, LayeredRuntime, LayeredVisual
from flower_cursor.lifecycle import CursorCell
from flower_cursor.settings import FLOWER_MANIFESTS

EXIT_ART = {
    "anemone": "ceb41f5d9b86a2e68f765a573739421bb085368ea2e1c6726b97f94c5949adf0",
    "cornflower": "9c453fb7943291d87458693e77f5e7e5ad2a83cf22a4974ec0a09629ae01789d",
    "daisy": "a0116d02525aca869cfef6120475fccba01249b97e05e2bf671cf91dab398645",
    "crocus": "54d8b8bbd77019caff188b47af9dc290137d03432a0e7276bc70fe206b85f1f3",
    "poppy": "d58ba53abdcedd7b72c49cb058684fe48cb958d743b0aac57b8e8f91c026ce8e",
    "dahlia": "21dd9e2d64d6cea8dbebab3d98c155d94052bee7d7b934ff08296aefa3a8a73e",
    "orchid": "ec87e7e668705266d10740c9fed90e3f1ee9f35d9d1c4237d339f8f70cd9f5cc",
}


@pytest.mark.parametrize("species", EXIT_ART)
def test_normal_exit_art_and_pose_records_are_preserved(species):
    path = FLOWER_MANIFESTS[species]
    data = json.loads(path.read_text())
    digest = hashlib.sha256()
    for pose in data["poses"]:
        if pose["name"].startswith("exit_"):
            digest.update(json.dumps(pose, sort_keys=True).encode())
            for name in pose["layers"].values():
                digest.update((path.parent / name).read_bytes())
    assert digest.hexdigest() == EXIT_ART[species]


@pytest.mark.parametrize("species", EXIT_ART)
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


@pytest.mark.parametrize("species", EXIT_ART)
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
