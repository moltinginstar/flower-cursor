import array
import math
import random

import pytest

from flower_cursor.bee import IdleBee
from flower_cursor.buzz import (
    MISSING_BACKEND,
    SAMPLE_RATE,
    WINGBEAT,
    BeeBuzz,
    BuzzState,
    Voice,
    buzz_state,
)
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.graphics_renderer import FlowerRenderer
from flower_cursor.layered import LayeredRuntime
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import DEFAULT_MANIFEST

VIEWPORT = ViewportGeometry(30, 100, 1600, 990)
HEAD = (192.0, 250.0)


def samples(data):
    block = array.array("h")
    block.frombytes(data)
    return block


def visiting(seconds, *, seed=9):
    """A bee that has actually flown in, rather than a hand-set position."""
    bee = IdleBee(random.Random(seed))
    bee.set_geometry(VIEWPORT, (50, 45))
    bee.move_to(CursorCell(8, 12))
    # The wait is randomised per visit; these measure the flight, not the wait.
    bee.wait = 10.0
    remaining = seconds
    while remaining > 1e-9:
        step = min(1 / 120, remaining)
        bee.update(step, HEAD)
        remaining -= step
    return bee


def test_a_bee_that_is_not_there_is_silent():
    bee = visiting(1.0)
    assert not bee.visible
    assert buzz_state(bee).gain == 0


def test_the_buzz_grows_louder_as_the_bee_closes_on_the_flower():
    far = visiting(11.0)
    near = visiting(12.5)
    assert far.visible and near.visible
    assert math.dist((near.x, near.y), near.head) < math.dist((far.x, far.y), far.head)
    assert 0 < buzz_state(far).gain < buzz_state(near).gain


def test_a_resting_bee_with_tucked_wings_makes_no_sound():
    resting = visiting(10.0 + 5.0)
    assert resting.visible and resting.folded > 0.99
    assert buzz_state(resting).gain == pytest.approx(0, abs=1e-6)


def test_departure_brings_the_wings_and_the_sound_back():
    bee = visiting(10.0 + 5.0)
    bee.leave()
    bee.update(0.4, HEAD)
    assert buzz_state(bee).gain > 0


@pytest.mark.parametrize("seed", [9, 4, 17, 23])
def test_the_bee_fades_out_through_the_edge_rather_than_being_cut_off(seed):
    """The exit edge is sampled per visit, so it can leave from beside the bloom."""
    bee = IdleBee(random.Random(seed))
    bee.set_geometry(VIEWPORT, (50, 45))
    bee.move_to(CursorCell(8, 12))
    # The wait is randomised per visit; these measure the flight, not the wait.
    bee.wait = 10.0
    last, elapsed, seen = 0.0, 0.0, False
    while elapsed < 60:
        bee.update(1 / 60, HEAD)
        if bee.visible:
            seen, last = True, buzz_state(bee).gain
        elif seen:
            break
        elapsed += 1 / 60
    assert seen
    assert last == pytest.approx(0, abs=0.02)


def test_the_bee_fades_in_through_the_edge_too():
    bee = visiting(10.0 + 1 / 60)
    assert bee.visible
    assert buzz_state(bee).gain == pytest.approx(0, abs=0.02)


def test_an_unfocused_pane_hears_nothing():
    bee = visiting(12.5)
    assert buzz_state(bee, audible=False).gain == 0


def test_pitch_rises_with_airspeed():
    bee = visiting(12.5)
    slow = buzz_state(bee).rate
    bee.vx, bee.vy = bee.vx * 40, bee.vy * 40
    assert buzz_state(bee).rate > slow
    assert 0.8 * WINGBEAT <= slow <= 1.3 * WINGBEAT


def test_the_voice_renders_the_frames_it_was_asked_for():
    voice = Voice()
    assert len(voice.render(0)) == 0
    assert len(samples(voice.render(512))) == 512
    with pytest.raises(ValueError):
        voice.render(-1)


def test_silence_in_means_silence_out():
    voice = Voice()
    assert set(samples(voice.render(4096))) == {0}
    assert voice.silent()


def test_gain_glides_rather_than_jumping():
    """No click: a full-gain target still starts from nothing."""
    voice = Voice(rng=random.Random(3))
    voice.target = BuzzState(1.0, WINGBEAT)
    opening = max(abs(v) for v in samples(voice.render(64)))
    voice.render(SAMPLE_RATE)
    assert voice.gain > 0.9
    settled = max(abs(v) for v in samples(voice.render(2048)))
    assert opening < 0.2 * settled


def test_the_wave_stays_inside_the_sixteen_bit_range():
    voice = Voice()
    voice.target = BuzzState(1.0, WINGBEAT)
    voice.render(SAMPLE_RATE)
    block = samples(voice.render(SAMPLE_RATE // 2))
    assert max(block) < 32767 and min(block) > -32768
    assert max(abs(v) for v in block) > 0.004 * 32767


def test_volume_scales_the_output():
    # The same seed both sides: the turbulence is random, so two voices left to
    # the global generator would not be rendering the same buzz.
    loud = Voice(volume=1.0, rng=random.Random(3))
    quiet = Voice(volume=0.25, rng=random.Random(3))
    for voice in (loud, quiet):
        voice.target = BuzzState(1.0, WINGBEAT)
        voice.render(SAMPLE_RATE)
    peak = [max(abs(v) for v in samples(v.render(2048))) for v in (loud, quiet)]
    assert peak[1] == pytest.approx(peak[0] * 0.25, rel=0.05)


def test_the_tone_is_the_pitch_it_claims_to_be():
    """Find the period by autocorrelation - turbulence defeats zero crossings."""
    voice = Voice(rng=random.Random(5))
    voice.target = BuzzState(1.0, 240.0)
    voice.render(SAMPLE_RATE)
    block = samples(voice.render(SAMPLE_RATE // 2))
    # Search lags spanning 150-400 Hz; the strongest is the wingbeat period.
    lags = range(SAMPLE_RATE // 400, SAMPLE_RATE // 150)
    best = max(lags, key=lambda lag: sum(a * b for a, b in zip(block, block[lag:])))
    assert SAMPLE_RATE / best == pytest.approx(240, rel=0.05)


def test_muting_fades_instead_of_cutting():
    voice = Voice()
    voice.target = BuzzState(1.0, WINGBEAT)
    voice.render(SAMPLE_RATE)
    last = samples(voice.render(256))[-1]
    voice.target = BuzzState(0.0, WINGBEAT)
    first = samples(voice.render(256))[0]
    assert abs(first - last) < 0.1 * 32767


class FakeDevice:
    def __init__(self):
        self.stream = None
        self.stopped = False

    def start(self, stream):
        self.stream = stream

    def stop(self):
        self.stopped = True

    def close(self):
        self.stopped = True


def test_a_missing_extra_says_so_once_and_stays_silent():
    said = []

    def missing(sample_rate):
        raise ImportError("No module named 'miniaudio'")

    buzz = BeeBuzz(open_device=missing, report=said.append)
    assert buzz.start() is False
    assert buzz.started is False
    assert said == [MISSING_BACKEND]
    # Every other entry point is a no-op rather than an error.
    buzz.update(visiting(12.5))
    buzz.mute()
    buzz.close()
    assert said == [MISSING_BACKEND]


def test_a_machine_with_no_sound_card_is_not_an_error():
    said = []

    def refused(sample_rate):
        raise OSError("no default output device")

    assert BeeBuzz(open_device=refused, report=said.append).start() is False
    assert len(said) == 1 and "stays silent" in said[0]


def test_a_started_buzz_feeds_the_device_from_the_bee():
    device = FakeDevice()
    buzz = BeeBuzz(open_device=lambda rate: device)
    assert buzz.start() is True
    assert buzz.start() is True
    buzz.update(visiting(12.5))
    assert buzz.voice.target.gain > 0
    assert len(samples(device.stream.send(512))) == 512
    buzz.mute()
    assert buzz.voice.target.gain == 0
    buzz.close()
    assert device.stopped and buzz.started is False


_CHILD = """
import fcntl, os, select, struct, termios, time, tty
tty.setraw(0)
struct.unpack('HHHH', fcntl.ioctl(1, termios.TIOCGWINSZ, b'\\0' * 8))
deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    if select.select([0], [], [], 0.1)[0] and b'q' in os.read(0, 256):
        break
"""
_RUNNER = f"""
import sys
from flower_cursor.observer import main
argv = ['--buzz', '1', '--bee', '0.05', '--', sys.executable, '-c', {_CHILD!r}]
raise SystemExit(main(argv))
"""
# A stand-in for the optional extra: the real `_open_miniaudio` asks for these
# names, so importing this exercises the same call the backend gets.
_STUB = """
import enum, pathlib


class SampleFormat(enum.Enum):
    SIGNED16 = 2


class PlaybackDevice:
    def __init__(self, output_format, nchannels, sample_rate):
        self.log = pathlib.Path("device.log")
        self.log.write_text(f"opened {output_format.name} {nchannels} {sample_rate}\\n")

    def start(self, stream):
        with self.log.open("a") as handle:
            for _ in range(3):
                handle.write(f"frames {len(stream.send(480))}\\n")

    def stop(self):
        with self.log.open("a") as handle:
            handle.write("stopped\\n")

    def close(self):
        pass
"""


def drive(tmp_path, *, backend: bool):
    """Run the real wrapper under a real PTY, with or without the extra."""
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import sys
    import termios
    import time

    environment = dict(os.environ, PYTHONPATH=str(tmp_path))
    if backend:
        (tmp_path / "miniaudio.py").write_text(_STUB)
    else:
        # Import machinery raises ImportError for a module bound to None.
        (tmp_path / "sitecustomize.py").write_text("import sys\nsys.modules['miniaudio'] = None\n")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 1600, 990))
    process = subprocess.Popen(
        [sys.executable, "-c", _RUNNER],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=environment,
    )
    os.close(slave)
    output = bytearray()
    tail = b""
    started = time.monotonic()
    stopped = False
    try:
        while time.monotonic() - started < 12:
            readable, _, _ = select.select([master], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                queries = tail + chunk
                for _ in range(queries.count(b"\x1b[?6n")):
                    os.write(master, b"\x1b[?9;13R")
                tail = queries[-4:]
            if not stopped and time.monotonic() - started >= 3:
                os.write(master, b"q")
                stopped = True
            if process.poll() is not None and not readable:
                break
        code = process.wait(timeout=5)
        return code, bytes(output), process.stderr.read().decode()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        os.close(master)


def test_the_real_wrapper_opens_the_backend_and_feeds_it_the_bee(tmp_path):
    code, output, stderr = drive(tmp_path, backend=True)
    assert code == 0, stderr
    # The bee was actually drawn, so there was something to hear.
    assert b"p=2" in output
    log = (tmp_path / "device.log").read_text().splitlines()
    assert log[0] == "opened SIGNED16 1 22050"
    assert [line for line in log if line == "frames 960"]
    assert log[-1] == "stopped"
    assert stderr == ""


def test_a_window_without_the_extra_still_gets_its_flower(tmp_path):
    code, output, stderr = drive(tmp_path, backend=False)
    assert code == 0, stderr
    assert b"p=1" in output and b"p=2" in output
    assert stderr.strip() == MISSING_BACKEND


def test_an_unfocused_tab_keeps_no_bee_and_falls_silent():
    """Losing focus is what a background tab is, so the bee has to go with it.

    Kitty sends focus-out to the active window of a tab that stops being the
    active one (`tabs.py`, `w.focus_changed(False)`), which is the same report
    an inactive split gets. `observer` answers it with `begin_withering`, so
    this covers the tab case as much as the split one.
    """
    runtime = LayeredRuntime(DEFAULT_MANIFEST, bee_rng=random.Random(31))
    renderer = FlowerRenderer(runtime.pose_library, lambda _: None, canvas_scale=2)
    runtime.set_geometry(VIEWPORT, renderer.layout)
    runtime.move_to(CursorCell(8, 12))
    runtime.bee.interval = runtime.bee.wait = 10.0
    # Fly on until it is both there and beating its wings - a landed bee has
    # them tucked, and a tucked bee is silent for reasons of its own.
    for _ in range(3600):
        runtime.update(1 / 60)
        if runtime.bee.visible and buzz_state(runtime.bee).gain > 0:
            break
    else:
        pytest.fail("the focused tab never produced an audible bee")

    runtime.begin_withering()
    # Long enough for the departure to finish and for several fresh visits to
    # have been scheduled had anything still been scheduling them.
    for _ in range(3600):
        runtime.update(1 / 60)
        assert buzz_state(runtime.bee, audible=False).gain == 0
    assert not runtime.bee.visible
    assert buzz_state(runtime.bee).gain == 0
