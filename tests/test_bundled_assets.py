import json
from pathlib import Path

import pytest
from PIL import Image

from flower_cursor import layered, observer
from flower_cursor.lifecycle import CursorCell
from flower_cursor.settings import FLOWER_CATALOGUE


def test_bundled_manifest_contains_complete_portable_runtime_assets():
    manifest = observer.DEFAULT_MANIFEST
    data = json.loads(manifest.read_text())
    assert len(data["poses"]) == 135
    assert len({pose["name"] for pose in data["poses"]}) == 135
    layers = {"complete", "body", "head", *(f"petal_{i}" for i in range(6))}
    files = set()
    for pose in data["poses"]:
        assert set(pose["layers"]) == layers
        assert pose["file"] in pose["layers"].values()
        for name in pose["layers"].values():
            assert not Path(name).is_absolute()
            assert ".." not in Path(name).parts
            files.add(name)
    assert len(files) == 1215
    assert files == {path.name for path in manifest.parent.glob("*.png")}
    crops = data["crops"]["layers"]
    for pose in data["poses"]:
        for layer, name in pose["layers"].items():
            with Image.open(manifest.parent / name) as image:
                image.verify()
            with Image.open(manifest.parent / name) as image:
                assert image.mode == "RGBA"
                assert image.size == (256, 256)
                bounds = image.getchannel("A").getbbox()
            if bounds is not None:
                left, top, right, bottom = crops[layer]
                assert left <= bounds[0] and top <= bounds[1]
                assert bounds[2] <= right and bounds[3] <= bottom


@pytest.mark.parametrize("spec", FLOWER_CATALOGUE, ids=lambda spec: spec.name)
def test_every_bundled_species_keeps_only_the_rectangle_each_layer_draws(spec):
    data = json.loads(spec.manifest.read_text())
    assert data["crops"]["canvas"] == [256, 256]
    names = {layer for pose in data["poses"] for layer in pose["layers"]}
    assert set(data["crops"]["layers"]) == names
    assert layered.LayeredCompositor(spec.manifest, species=spec.name)._crops


def test_bundled_runtime_renders_outside_the_checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runtime = layered.LayeredRuntime(observer.DEFAULT_MANIFEST)
    compositor = layered.LayeredCompositor(observer.DEFAULT_MANIFEST)
    runtime.move_to(CursorCell(4, 8))
    runtime.update(1.5)
    frame = compositor.frame(runtime.visuals()[0])
    assert frame.size == (256, 256)
    assert frame.getchannel("A").getbbox() is not None
    runtime.move_to(CursorCell(4, 20))
    runtime.update(0.15)
    assert runtime.flowers[0].flights
    assert compositor.frame(runtime.visuals()[0]).getchannel("A").getbbox() is not None


# Nothing to opt into any more: a window either grows the bundled flower or
# the artwork it was pointed at, and both of them draw.
@pytest.mark.parametrize("args", [[], ["--species", str(observer.DEFAULT_MANIFEST)]])
def test_observer_grows_the_default_flower_or_the_manifest_it_was_given(monkeypatch, args):
    created = []
    original = layered.LayeredRuntime

    def create(manifest, **options):
        runtime = original(manifest, **options)
        created.append((manifest, runtime))
        return runtime

    def run(command, **kwargs):
        assert command == ["example-shell"]
        assert created[0][0] == observer.DEFAULT_MANIFEST
        kwargs["on_cursor"](CursorCell(4, 8))
        assert created[0][1].active is not None
        assert kwargs["hide_cursor"]
        return 0

    monkeypatch.setattr(layered, "LayeredRuntime", create)
    monkeypatch.setattr(observer, "run", run)
    assert observer.main([*args, "--", "example-shell"]) == 0


def test_the_console_entrypoint_passes_its_arguments_through_untouched(monkeypatch):
    seen = []
    monkeypatch.setattr(observer.sys, "argv", ["flower-cursor", "--", "zsh", "-l"])
    monkeypatch.setattr(observer, "main", lambda args: seen.append(args) or 7)
    assert observer.shell_main() == 7
    assert seen == [["--", "zsh", "-l"]]
