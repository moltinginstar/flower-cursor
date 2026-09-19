import os
import pty
import select
import subprocess
import sys
import time


def test_small_child_output_is_visible_before_observer_child_exits() -> None:
    child_code = "import os,time; os.write(1,b'READY'); time.sleep(2.0)"
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "flower_cursor.observer",
            "--render",
            "--",
            sys.executable,
            "-c",
            child_code,
        ],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    os.close(slave_fd)
    output = bytearray()
    deadline = time.monotonic() + 1.5
    try:
        while b"READY" not in output and time.monotonic() < deadline:
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            if readable:
                output.extend(os.read(master_fd, 65536))

        assert process.poll() is None
        assert b"READY" in output
        while process.poll() is None:
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    os.read(master_fd, 65536)
                except OSError:
                    break
        returncode = process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr is not None else b""
        assert returncode == 0, stderr.decode()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        os.close(master_fd)
