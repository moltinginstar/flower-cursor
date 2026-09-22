import base64
import fcntl
import io
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pytest
from PIL import Image

_CHILD = """
import fcntl, os, select, struct, termios, time, tty
tty.setraw(0)
size = struct.unpack('HHHH', fcntl.ioctl(1, termios.TIOCGWINSZ, b'\\0' * 8))
os.write(1, ('SIZE' + repr(size)).encode())
deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    if select.select([0], [], [], 0.1)[0] and b'q' in os.read(0, 256):
        break
"""
_RUNNER = f"""
import sys
from flower_cursor.observer import main
raise SystemExit(main(['--bee', '0.05', '--', sys.executable, '-c', {_CHILD!r}]))
"""


def placements(output):
    result = []
    for match in re.finditer(rb"\x1b\[s\x1b\[(\d+);(\d+)H(.*?)\x1b\[u", output, re.S):
        row, column, graphics = match.groups()
        parts = [
            (dict(item.split("=", 1) for item in parameters.decode().split(",")), payload)
            for parameters, payload in re.findall(rb"\x1b_G([^;]*);(.*?)\x1b\\", graphics, re.S)
        ]
        transfers = [(p, payload) for p, payload in parts if p.get("a") == "T" or "a" not in p]
        if not transfers:
            continue
        image = Image.open(io.BytesIO(base64.b64decode(b"".join(data for _, data in transfers))))
        image.load()
        result.append((int(row) - 1, int(column) - 1, transfers[0][0], image))
    return result


def drive_observer(dimensions, *, lose_geometry=False):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", *dimensions))
    process = subprocess.Popen(
        [sys.executable, "-c", _RUNNER], stdin=slave, stdout=slave, stderr=subprocess.PIPE
    )
    os.close(slave)
    output = bytearray()
    query_tail = b""
    started = time.monotonic()
    lost_at = None
    cutoff = None
    settled_cutoff = None
    stop_sent = False
    try:
        while time.monotonic() - started < 10:
            readable, _, _ = select.select([master], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                queries = query_tail + chunk
                for _ in range(queries.count(b"\x1b[?6n")):
                    os.write(master, b"\x1b[?9;13R")
                query_tail = queries[-4:]
            now = time.monotonic()
            bee_drawn = re.search(rb"a=T,[^;]*,p=2[,;].*?\x1b\[u", output, re.S)
            if lose_geometry and lost_at is None and bee_drawn:
                cutoff = len(output)
                lost_at = now
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
                process.send_signal(signal.SIGWINCH)
            if lost_at is not None and settled_cutoff is None and now - lost_at >= 0.3:
                settled_cutoff = len(output)
            finished = now - started >= 3 if lost_at is None else now - lost_at >= 0.6
            if finished and not stop_sent:
                os.write(master, b"q")
                stop_sent = True
            if process.poll() is not None and not readable:
                break
        returncode = process.wait(timeout=3)
        stderr = process.stderr.read()
        assert returncode == 0, stderr.decode()
        if lose_geometry:
            assert cutoff is not None, "observer never emitted a measured-viewport bee"
        return bytes(output), cutoff, settled_cutoff
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        os.close(master)


@pytest.mark.parametrize("measured", [True, False])
def test_real_observer_passes_winsize_to_runtime_and_independent_renderer(measured):
    dimensions = (30, 100, 1600, 990) if measured else (30, 100, 0, 0)
    output, _, _ = drive_observer(dimensions)
    assert ("SIZE" + repr(dimensions)).encode() in output
    images = placements(output)
    flowers = [image for _, _, p, image in images if p["p"] == "1"]
    assert flowers and flowers[0].size == ((144, 128) if measured else (66, 60))
    visitors = [(row, col, p, image) for row, col, p, image in images if p["p"] == "2"]
    if not measured:
        assert not visitors
        return
    assert visitors
    row, col, parameters, image = visitors[0]
    origin = (col * 16 + int(parameters.get("X", 0)), row * 33 + int(parameters.get("Y", 0)))
    assert 0 < min(image.size) < 50
    assert 0 <= origin[0] <= 1600 - image.width
    assert 0 <= origin[1] <= 990 - image.height
    assert (
        origin[0] == 0
        or origin[1] == 0
        or origin[0] + image.width == 1600
        or origin[1] + image.height == 990
    )
    assert image.getchannel("A").getbbox()
    assert max(image.width for _, _, _, image in visitors) < 80
    assert b"a=d,d=I" in output


def test_real_observer_geometry_loss_cancels_and_deletes_existing_visitor():
    output, cutoff, settled_cutoff = drive_observer((30, 100, 1600, 990), lose_geometry=True)
    before = placements(output[:cutoff])
    bee_ids = {p["i"] for _, _, p, _ in before if p["p"] == "2"}
    assert bee_ids
    assert settled_cutoff is not None
    after = output[cutoff:settled_cutoff]
    deleted = {image_id.decode() for image_id in re.findall(rb"a=d,d=I,i=(\d+),p=2", after)}
    assert bee_ids.intersection(deleted)
    assert not [p for _, _, p, _ in placements(output[settled_cutoff:]) if p["p"] == "2"]
