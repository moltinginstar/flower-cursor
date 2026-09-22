import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path


def test_wheel_contains_identical_artwork_and_runs_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    manifest = json.loads((root / "src/flower_cursor/assets/bee/manifest.json").read_text())
    suffixes = [f"_{heading:+03d}" if heading else "" for heading in manifest["headings"]]
    frames = {f"bee_{index:02d}{suffix}.png" for index in range(16) for suffix in suffixes}
    assert len(frames) == 160
    assert set(manifest["frames"]) == frames
    with zipfile.ZipFile(wheel) as archive:
        assert {
            Path(name).name
            for name in archive.namelist()
            if name.startswith("flower_cursor/assets/bee/") and name.endswith(".png")
        } == frames
        for kind in ("bee", "layered"):
            source = root / "src/flower_cursor/assets" / kind
            for path in source.iterdir():
                if path.is_file():
                    assert (
                        archive.read(f"flower_cursor/assets/{kind}/{path.name}")
                        == path.read_bytes()
                    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["FLOWER_CURSOR_CHECKOUT"] = str(root)
    subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--with",
            str(wheel),
            "python",
            "-c",
            """
import base64
import io
import json
import os
import random
import re
from dataclasses import replace
from pathlib import Path
from PIL import Image
import flower_cursor.geometry
from flower_cursor.bee import BeePose
from flower_cursor.bee_art import ASSETS, BeeCompositor
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.layered import LayeredRuntime, LayeredCompositor, BeeVisual
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST
checkout = Path(os.environ['FLOWER_CURSOR_CHECKOUT']).resolve()
assert not Path(flower_cursor.geometry.__file__).resolve().is_relative_to(checkout)
assert not DEFAULT_MANIFEST.resolve().is_relative_to(checkout)
assert not ASSETS.resolve().is_relative_to(checkout)
manifest = json.loads((ASSETS / 'manifest.json').read_text())
suffixes = [f'_{heading:+03d}' if heading else '' for heading in manifest['headings']]
expected_frames = {f'bee_{index:02d}{suffix}.png'
                   for index in range(16) for suffix in suffixes}
assert set(manifest['frames']) == expected_frames
for name in expected_frames:
    with Image.open(ASSETS / name) as image:
        image.load()
        assert image.mode == 'RGBA' and image.getchannel('A').getbbox()
art = BeeCompositor()
for folded, phase, index in ((0, 0, 0), (0, 0.375, 3), (0.375, 0.125, 10), (1, 0, 15)):
    pose = BeePose(0, 0, (50, 45), 0, folded, phase)
    pixels = set()
    for heading, suffix in zip(manifest['headings'], suffixes):
        rendered = art.frame(replace(pose, heading=heading))
        with Image.open(ASSETS / f'bee_{index:02d}{suffix}.png') as image:
            expected = (image.convert('RGBa')
                        .resize(pose.size, Image.Resampling.LANCZOS).convert('RGBA'))
        assert rendered.tobytes() == expected.tobytes()
        pixels.add(rendered.tobytes())
    assert len(pixels) == len(suffixes)
r = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(31))
c = LayeredCompositor(DEFAULT_MANIFEST)
writes = []
renderer = FlowerRenderer(r.pose_library, writes.append, compositor=c, canvas_scale=2)
viewport = ViewportGeometry(30, 100, 1600, 990)
r.set_geometry(viewport, renderer.layout)
cell = CursorCell(8, 12)
r.move_to(cell)
r.bee.interval = r.bee.wait = 10.0
for _ in range(600):
    r.update(1/60)
v = next(v for v in r.visuals() if isinstance(v, BeeVisual))
assert v.bee.size == (50, 45)
assert r.bee.offscreen
renderer.render((v,), cell, viewport=viewport)
assert not writes
for _ in range(300):
    r.update(1/60)
v = next(v for v in r.visuals() if isinstance(v, BeeVisual))
full = c.frame(v)
assert full.getchannel('A').getbbox()
anchor = manifest['contacts']['0']['anchor']
contact = tuple(center + (value - 0.5) * size
                for center, value, size in zip((v.bee.x, v.bee.y), anchor, v.bee.size))
target = tuple(h + offset for h, offset in zip(r.bee.head, r.bee.landing_offset))
assert all(abs(actual - expected) < 0.5 for actual, expected in zip(contact, target))
renderer.render((v,), cell, viewport=viewport)
output = b''.join(writes)
parts = re.findall(rb'\x1b_G([^;]*);(.*?)\x1b\\\\', output, re.S)
transfers = [(dict(item.split('=', 1) for item in p.decode().split(',')), data)
             for p, data in parts]
payload = b''.join(data for p, data in transfers if p.get('a') == 'T' or 'a' not in p)
image = Image.open(io.BytesIO(base64.b64decode(payload)))
expected = full.crop(full.getchannel('A').getbbox())
assert image.size == expected.size
assert image.tobytes() == expected.tobytes()
assert max(image.size) < 100
assert transfers[0][0]['p'] == str(v.identifier)
assert transfers[0][0]['z'] == '2'
assert 'c' not in transfers[0][0] and 'r' not in transfers[0][0]
r.move_to(CursorCell(8, 22))
for _ in range(240):
    r.update(1/60)
assert not any(isinstance(v, BeeVisual) for v in r.visuals())
writes.clear()
renderer.render((), cell, viewport=viewport)
assert b'a=d,d=I' in b''.join(writes)
assert not renderer._image_bases

""",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
