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
    with zipfile.ZipFile(wheel) as archive:
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
from flower_cursor.layered import LayeredRuntime, LayeredCompositor, BeeVisual
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST
r = LayeredRuntime(DEFAULT_MANIFEST)
c = LayeredCompositor(DEFAULT_MANIFEST)
r.move_to(CursorCell(8, 12))
for _ in range(850):
    r.update(1/60)
v = next(v for v in r.visuals() if isinstance(v, BeeVisual))
assert c.frame(v).getchannel('A').getbbox()
r.move_to(CursorCell(8, 22))
for _ in range(120):
    r.update(1/60)
assert not any(isinstance(v, BeeVisual) for v in r.visuals())
""",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
