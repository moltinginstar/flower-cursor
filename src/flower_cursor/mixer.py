"""One audio device for every window's bee.

The shared renderer runs a `BeeBuzz` per window, and each of them would open
its own device. Given a `Mixer.channel` as its `open_device` instead, each gets
a device-shaped channel, and the mixer runs the one real device while any
channel is playing, summing what they play.

The device's callback reads the playing voices from a tuple that is replaced,
never mutated, and takes no lock. Stopping a device waits for its callback to
return, so a callback that waited on the lock held around `stop` would never
return.
"""

from __future__ import annotations

import threading
from array import array
from collections.abc import Callable, Generator

from .buzz import SAMPLE_RATE, Device, _open_miniaudio


class Mixer:
    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        open_device: Callable[[int], Device] = _open_miniaudio,
    ) -> None:
        self.sample_rate = sample_rate
        self._open_device = open_device
        self._lock = threading.Lock()
        self._device: Device | None = None
        self._running = False
        self._voices: tuple[Generator[bytes, int], ...] = ()

    def channel(self, sample_rate: int) -> Device:
        """A device for one voice. Raises whatever opening the real one raises."""
        if sample_rate != self.sample_rate:
            raise ValueError(f"the mixer plays at {self.sample_rate} Hz, not {sample_rate}")
        with self._lock:
            if self._device is None:
                self._device = self._open_device(self.sample_rate)
        return _Channel(self)

    @property
    def playing(self) -> int:
        """How many voices are being played."""
        return len(self._voices)

    def _add(self, stream: Generator[bytes, int]) -> None:
        with self._lock:
            if any(voice is stream for voice in self._voices):
                return
            self._voices = (*self._voices, stream)
            if not self._running and self._device is not None:
                mix = self._mix()
                next(mix)
                self._device.start(mix)
                self._running = True

    def _remove(self, stream: Generator[bytes, int]) -> None:
        with self._lock:
            self._voices = tuple(voice for voice in self._voices if voice is not stream)
            if not self._voices and self._running and self._device is not None:
                self._running = False
                self._device.stop()

    def _mix(self) -> Generator[bytes, int]:
        frames = yield b""
        while True:
            voices = self._voices
            if not voices:
                block = bytes(2 * frames)
            elif len(voices) == 1:
                block = voices[0].send(frames)
            else:
                total = [0] * frames
                for voice in voices:
                    for index, sample in enumerate(array("h", voice.send(frames))):
                        total[index] += sample
                block = array(
                    "h", (-32768 if value < -32768 else min(value, 32767) for value in total)
                ).tobytes()
            frames = yield block

    def close(self) -> None:
        with self._lock:
            device, self._device = self._device, None
            self._voices = ()
            self._running = False
        if device is not None:
            for method in ("stop", "close"):
                try:
                    getattr(device, method)()
                except Exception:
                    pass


class _Channel:
    """What one `BeeBuzz` takes for its device."""

    def __init__(self, mixer: Mixer) -> None:
        self._mixer = mixer
        self._stream: Generator[bytes, int] | None = None

    def start(self, stream: Generator[bytes, int], /) -> None:
        self._stream = stream
        self._mixer._add(stream)

    def stop(self) -> None:
        if self._stream is not None:
            self._mixer._remove(self._stream)

    def close(self) -> None:
        self.stop()
        self._stream = None
