import json
from pathlib import Path

import pytest
from PIL import Image

from flower_cursor import layered, observer
from flower_cursor.lifecycle import CursorCell


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
    for name in files:
        with Image.open(manifest.parent / name) as image:
            assert image.mode == "RGBA"
            assert image.size == (256, 256)
            image.verify()


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


@pytest.mark.parametrize(
    "args", [[], ["--render"], ["--shedding-manifest", str(observer.DEFAULT_MANIFEST)]]
)
def test_observer_rendering_defaults_and_manifest_override(monkeypatch, args):
    created = []
    original = layered.LayeredRuntime

    def create(manifest, **options):
        runtime = original(manifest, **options)
        created.append((manifest, runtime))
        return runtime

    def run(command, **kwargs):
        assert command == ["example-shell"]
        if args:
            assert created[0][0] == observer.DEFAULT_MANIFEST
            kwargs["on_cursor"](CursorCell(4, 8))
            assert created[0][1].active is not None
            assert kwargs["hide_cursor"]
        else:
            assert created == []
            assert kwargs["on_tick"] is None
            assert not kwargs["hide_cursor"]
        return 0

    monkeypatch.setattr(layered, "LayeredRuntime", create)
    monkeypatch.setattr(observer, "run", run)
    assert observer.main([*args, "--", "example-shell"]) == 0


def test_console_entrypoint_enables_rendering_and_preserves_arguments(monkeypatch):
    seen = []
    monkeypatch.setattr(observer.sys, "argv", ["flower-cursor", "--", "zsh", "-l"])
    monkeypatch.setattr(observer, "main", lambda args: seen.append(args) or 7)
    assert observer.shell_main() == 7
    assert seen == [["--render", "--", "zsh", "-l"]]
