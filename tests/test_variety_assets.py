import json
import math
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from flower_cursor.bee_art import ASSETS as BEE_ASSETS
from flower_cursor.geometry import FlowerLayout, ViewportGeometry
from flower_cursor.layered import BeeVisual, LayeredCompositor, LayeredRuntime, LayeredVisual
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import FLOWER_MANIFESTS

ROOT = Path(__file__).resolve().parents[1]
NEW_SPECIES = ("daisy", "crocus", "poppy", "dahlia", "orchid")
PUBLIC_SPECIES = ("anemone", "cornflower", *NEW_SPECIES)
GROUP_COUNTS = {
    "anemone": 6,
    "cornflower": 18,
    "daisy": 17,
    "crocus": 6,
    "poppy": 4,
    "dahlia": 45,
    "orchid": 4,
}
A = CursorCell(4, 8)
B = CursorCell(4, 20)
VIEWPORT = ViewportGeometry(30, 100, 1600, 990)


def advance(runtime: LayeredRuntime, duration: float) -> None:
    while duration > 1e-10:
        step = min(1 / 60, duration)
        runtime.update(step)
        duration -= step


def load_manifest(species: str) -> tuple[Path, dict]:
    manifest = FLOWER_MANIFESTS[species]
    assert manifest.is_file(), f"{species} bundle is missing: {manifest}"
    return manifest, json.loads(manifest.read_text())


def expected_layers(group_count: int) -> set[str]:
    return {"complete", "body", "head", *(f"petal_{index}" for index in range(group_count))}


def test_real_variety_manifest_has_exact_poses_groups_and_pngs(species: str) -> None:
    manifest, data = load_manifest(species)
    group_count = GROUP_COUNTS[species]
    layers = expected_layers(group_count)

    assert data["species"] == species
    groups = data["shedding_groups"]
    assert len(groups) == group_count
    assert [group["id"] for group in groups] == [f"petal_{index}" for index in range(group_count)]
    assert [group["layer"] for group in groups] == [
        f"petal_{index}" for index in range(group_count)
    ]
    assert [group["order"] for group in groups] == list(range(group_count))
    assert sorted(data["shedding_order"]) == list(range(group_count))

    poses = data["poses"]
    assert len(poses) == 135
    assert len({pose["name"] for pose in poses}) == len(poses)
    assert len(data["root_anchor"]) == 2
    root_x, root_y = data["root_anchor"]
    assert 0 < root_x < 1
    assert 0 < root_y < 1

    files: set[str] = set()
    for pose in poses:
        assert set(pose["layers"]) == layers
        assert pose["file"] in pose["layers"].values()
        complete_bbox = None
        for layer, name in pose["layers"].items():
            path = Path(name)
            assert not path.is_absolute()
            assert ".." not in path.parts
            assert path.suffix == ".png"
            asset = manifest.parent / path
            assert asset.is_file(), f"{species}/{pose['name']} references missing {name}"
            files.add(name)
            with Image.open(asset) as image:
                assert image.format == "PNG"
                assert image.mode == "RGBA"
                assert image.size == (256, 256)
                image.verify()
            with Image.open(asset) as image:
                bbox = image.getchannel("A").getbbox()
            if bbox is not None:
                assert 0 < bbox[0] < bbox[2] < 256, (species, pose["name"], layer, bbox)
                assert 0 < bbox[1] < bbox[3] < 256, (species, pose["name"], layer, bbox)
            if layer == "complete":
                complete_bbox = bbox

        assert complete_bbox is not None
        root = (root_x * 256, root_y * 256)
        assert complete_bbox[0] <= root[0] < complete_bbox[2]
        assert complete_bbox[1] <= root[1] < complete_bbox[3]

    assert len(files) == 135 * (3 + group_count)
    assert files == {path.name for path in manifest.parent.glob("*.png")}


test_real_variety_manifest_has_exact_poses_groups_and_pngs = pytest.mark.parametrize(
    "species", NEW_SPECIES, ids=NEW_SPECIES
)(test_real_variety_manifest_has_exact_poses_groups_and_pngs)


def test_real_variety_head_projection_matches_world_head(species: str) -> None:
    _, data = load_manifest(species)
    root = data["root_anchor"]
    right = data["projection_right"]
    down = data["projection_down"]
    assert len(right) == len(down) == 3
    assert all(math.isfinite(value) for value in (*root, *right, *down))

    for pose in data["poses"]:
        world_head = pose["world_head"]
        head = pose["head"]
        assert len(world_head) == 3
        assert len(head) == 2
        assert all(math.isfinite(value) for value in (*head, *world_head))
        projected = (
            root[0] + sum(world * basis for world, basis in zip(world_head, right)),
            root[1] + sum(world * basis for world, basis in zip(world_head, down)),
        )
        assert head == pytest.approx(projected, abs=1e-6)
        assert 0 <= head[0] <= 1
        assert 0 <= head[1] <= 1


test_real_variety_head_projection_matches_world_head = pytest.mark.parametrize(
    "species", NEW_SPECIES, ids=NEW_SPECIES
)(test_real_variety_head_projection_matches_world_head)


def flower_visual(runtime: LayeredRuntime, flower) -> LayeredVisual:
    return next(
        visual
        for visual in runtime.visuals()
        if isinstance(visual, LayeredVisual) and visual.flower is flower
    )


def test_real_variety_living_interrupted_return_is_pixel_continuous(species: str) -> None:
    manifest, _ = load_manifest(species)
    runtime = LayeredRuntime(manifest, species=species)
    compositor = LayeredCompositor(manifest, species=species)
    runtime.move_to(A)
    advance(runtime, 0.45)

    returning = runtime.active
    assert returning is not None and returning.flower.active
    runtime.move_to(B)
    advance(runtime, 0.22)
    assert returning.flower.active is False
    pose = returning.pose
    lean_velocity = returning._lean.velocity
    flight_state = [(flight.identifier, flight.age) for flight in returning.flights]
    before_return = compositor.frame(flower_visual(runtime, returning)).tobytes()

    runtime.move_to(A)
    assert runtime.active is returning
    assert returning.flower.active
    assert returning.pose == pose
    assert returning._lean.velocity == lean_velocity
    assert [(flight.identifier, flight.age) for flight in returning.flights] == flight_state
    assert compositor.frame(flower_visual(runtime, returning)).tobytes() == before_return

    runtime.update(0.02)
    assert compositor.frame(flower_visual(runtime, returning)).getchannel("A").getbbox() is not None


test_real_variety_living_interrupted_return_is_pixel_continuous = pytest.mark.parametrize(
    "species", NEW_SPECIES, ids=NEW_SPECIES
)(test_real_variety_living_interrupted_return_is_pixel_continuous)


def test_real_variety_sheds_independently_and_grows_new_bud_after_death(species: str) -> None:
    manifest, data = load_manifest(species)
    runtime = LayeredRuntime(manifest, species=species)
    compositor = LayeredCompositor(manifest, species=species)
    runtime.move_to(A)
    advance(runtime, 1.2)
    departed = runtime.active
    assert departed is not None
    old_identifier = departed.flower.identifier

    runtime.move_to(B)
    advance(runtime, 0.60)
    expected_order = list(data["shedding_order"])
    assert [flight.index for flight in departed.flights] == expected_order
    assert len({round(flight.age, 6) for flight in departed.flights}) > 1
    assert all(petal.value == 0 for petal in departed.petals)
    assert departed in runtime.flowers
    assert compositor.frame(flower_visual(runtime, departed)).getchannel("A").getbbox() is not None

    ages = {flight.identifier: flight.age for flight in departed.flights}
    advance(runtime, 0.10)
    assert {flight.identifier for flight in departed.flights} == set(ages)
    assert all(
        flight.age == pytest.approx(ages[flight.identifier] + 0.20, abs=2e-6)
        for flight in departed.flights
    )

    advance(runtime, 1.50)
    assert departed.flower.dead
    assert departed not in runtime.flowers

    runtime.move_to(A)
    replacement = runtime.active
    assert replacement is not None
    assert replacement is not departed
    assert replacement.flower.identifier > old_identifier
    assert replacement.flower.active
    assert not replacement.flower.dead
    assert replacement.pose.stem == 0
    runtime.update(0.12)
    assert (
        compositor.frame(flower_visual(runtime, replacement)).getchannel("A").getbbox() is not None
    )


test_real_variety_sheds_independently_and_grows_new_bud_after_death = pytest.mark.parametrize(
    "species", NEW_SPECIES, ids=NEW_SPECIES
)(test_real_variety_sheds_independently_and_grows_new_bud_after_death)


def bloom_alpha(manifest: Path, data: dict, samples, size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", (256, 256))
    layers = ("head", *(f"petal_{index}" for index in range(len(data["shedding_groups"]))))
    records = {pose["name"]: pose for pose in data["poses"]}
    for asset, _ in samples:
        record = records[asset.name]
        for layer in layers:
            with Image.open(manifest.parent / record["layers"][layer]) as image:
                mask = ImageChops.lighter(mask, image.getchannel("A"))
    return mask.resize(size, Image.Resampling.LANCZOS)


def test_real_variety_bee_feet_touch_each_rendered_bloom(species: str) -> None:
    manifest, data = load_manifest(species)
    contact = json.loads((BEE_ASSETS / "manifest.json").read_text())["contacts"]["0"]
    near_feet = [foot for foot in contact["feet"] if foot[1] > contact["anchor"][1]]
    layout = FlowerLayout(LayeredRuntime(manifest, species=species).pose_library.root_anchor)
    for seed in (0, 9, 31):
        runtime = LayeredRuntime(manifest, bee_rng=random.Random(seed), species=species)
        compositor = LayeredCompositor(manifest, species=species)
        runtime.set_geometry(VIEWPORT, layout)
        runtime.move_to(CursorCell(8, 12))
        for _ in range(2000):
            runtime.update(1 / 60)
            if (
                runtime.bee.visible
                and runtime.bee.folded >= 0.999
                and runtime.bee.departure is None
            ):
                break
        else:
            pytest.fail(f"{species} bee did not reach a resting visit for seed {seed}")

        support = data["landing_support_bounds"]
        width, height = layout.size(VIEWPORT.cell_size)
        bounds = runtime.bee.landing_support_bounds
        assert bounds is not None
        assert bounds[0] == pytest.approx((support["x"][0] * width, support["x"][1] * width))
        assert bounds[1] == pytest.approx((support["y"][0] * height, support["y"][1] * height))
        assert bounds[0][0] <= runtime.bee.landing_offset[0] <= bounds[0][1]
        assert bounds[1][0] <= runtime.bee.landing_offset[1] <= bounds[1][1]

        flower_visual_instance = next(
            visual for visual in runtime.visuals() if isinstance(visual, LayeredVisual)
        )
        bee_visual = next(visual for visual in runtime.visuals() if isinstance(visual, BeeVisual))
        flower = (
            compositor.frame(flower_visual_instance)
            .convert("RGBa")
            .resize((width, height), Image.Resampling.LANCZOS)
            .convert("RGBA")
        )
        bloom = bloom_alpha(manifest, data, flower_visual_instance.samples, (width, height))
        bee = compositor.frame(bee_visual)
        flower_origin = layout.origin(bee_visual.cell, VIEWPORT.cell_size)
        bee_origin = (
            math.floor(bee_visual.bee.x - bee.width / 2 + 0.5),
            math.floor(bee_visual.bee.y - bee.height / 2 + 0.5),
        )

        supported = 0
        for foot in near_feet:
            x = round(bee_origin[0] + foot[0] * bee.width)
            y = round(bee_origin[1] + foot[1] * bee.height)
            bloom_pixels = []
            flower_pixels = []
            bee_pixels = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    flower_x, flower_y = x - flower_origin[0] + dx, y - flower_origin[1] + dy
                    if 0 <= flower_x < width and 0 <= flower_y < height:
                        bloom_pixels.append(bloom.getpixel((flower_x, flower_y)))
                        flower_pixels.append(flower.getpixel((flower_x, flower_y)))
                    bee_x, bee_y = x - bee_origin[0] + dx, y - bee_origin[1] + dy
                    if 0 <= bee_x < bee.width and 0 <= bee_y < bee.height:
                        bee_pixels.append(bee.getpixel((bee_x, bee_y)))
            rendered_bloom = any(alpha >= 64 for alpha in bloom_pixels) and any(
                alpha >= 64 for _, _, _, alpha in flower_pixels
            )
            rendered_foot = any(alpha >= 16 for _, _, _, alpha in bee_pixels)
            supported += rendered_bloom and rendered_foot

        assert supported >= 2, f"{species} seed {seed} supported only {supported} bee feet"


test_real_variety_bee_feet_touch_each_rendered_bloom = pytest.mark.parametrize(
    "species", NEW_SPECIES, ids=NEW_SPECIES
)(test_real_variety_bee_feet_touch_each_rendered_bloom)


def test_installed_wheel_loads_and_renders_all_public_species_outside_checkout(tmp_path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["FLOWER_CURSOR_CHECKOUT"] = str(ROOT)
    subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--with",
            str(wheels[0]),
            "python",
            "-c",
            """
import os
from pathlib import Path

from flower_cursor.geometry import FlowerLayout
from flower_cursor.layered import LayeredCompositor, LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import FLOWER_MANIFESTS
import flower_cursor

checkout = Path(os.environ["FLOWER_CURSOR_CHECKOUT"]).resolve()
assert not Path(flower_cursor.__file__).resolve().is_relative_to(checkout)
counts = {
    "anemone": 6,
    "cornflower": 18,
    "daisy": 17,
    "crocus": 6,
    "poppy": 4,
    "dahlia": 45,
    "orchid": 4,
}
for species, count in counts.items():
    manifest = FLOWER_MANIFESTS[species]
    assert manifest.is_file()
    assert not manifest.resolve().is_relative_to(checkout)
    kwargs = {} if species == "anemone" else {"species": species}
    runtime = LayeredRuntime(manifest, **kwargs)
    compositor = LayeredCompositor(manifest, **kwargs)
    runtime.move_to(CursorCell(4, 8))
    runtime.update(1.2)
    visual = runtime.visuals()[0]
    assert len(runtime.metadata.shedding_groups) == count
    frame = compositor.frame(visual)
    assert frame.size == (256, 256)
    assert frame.getchannel("A").getbbox() is not None
""",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
