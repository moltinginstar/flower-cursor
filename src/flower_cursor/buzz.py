"""Optional wingbeat audio for the idle bee.

The buzz is synthesized rather than sampled: the visit is improvised - a
different entry edge, curve and rest length every time - so a loop file could
only be faded in and out over motion it knows nothing about. A wavetable
oscillator driven from the bee's own state gets the sound the bee is actually
making: louder as it closes on the bloom, pitch rising with airspeed, and
silent while it sits there with its wings tucked.

Sound is off unless asked for, and the audio backend is an optional extra, so
everything here has to be missing-able. `BeeBuzz.start` returns whether the
backend is there; every other method is a no-op when it is not, or when the
device it opens later refuses. Nothing in this module
writes to stdout - that file descriptor is the terminal transport.
"""

from __future__ import annotations

import importlib.util
import math
import queue
import random
import threading
import time
from array import array
from collections.abc import Callable, Generator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from .easing import clamp01, smoothstep

# 22.05 kHz is ample for a ~230 Hz buzz and its first few harmonics, and halves
# what the synthesis loop has to do between audio callbacks.
SAMPLE_RATE = 22050
# Honeybee wingbeat. The pitch moves around this with airspeed.
WINGBEAT = 230.0
_TABLE_BITS = 11
_TABLE_SIZE = 1 << _TABLE_BITS
_MASK = _TABLE_SIZE - 1
# Pitch follows its target over roughly this long, which is what keeps every
# start, stop and abort free of clicks.
_GLIDE = 0.035
# Loudness gets its own, much slower constant. The wings tuck in about a
# quarter of a second and come back out in under a fifth, which is right for
# the picture and far too fast for the sound - followed literally it switches
# the buzz off and on. A settling bee winds down; a launching one spins up.
_SETTLE = 0.22
# Peak amplitude of a full-gain buzz, before the user's own volume. A bee in
# the room is a small sound, and this one is a decoration on a terminal: even
# at `--buzz 1` it should read as a faint buzz somewhere nearby rather
# than as something playing at you.
_HEADROOM = 0.011
# How far from the bloom the bee is still audible, as a fraction of the
# viewport diagonal.
_FALLOFF = 0.30
# Airspeed, in bee-widths per second, that reaches full pitch.
_CRUISE = 11.0
# What the wingbeat falls to as the wings tuck. Wings that are on their way to
# folded are not beating at cruise, and the pitch sliding down into the landing
# (and back up out of it) is most of what makes it a landing rather than a cut.
_SPIN = 0.55
# The bee crosses the viewport edge at whatever speed it is flying, and its
# exit edge is sampled per visit, so it can leave from right beside the bloom
# still at volume. Fading over this many of its own radii from the border lets
# it arrive and depart through the edge rather than being cut off at it.
_EDGE_FADE = 2.5
# Long enough for `_SETTLE` to actually reach silence before the device goes.
_FADE_OUT = 0.35


@dataclass(frozen=True, slots=True)
class Timbre:
    """What the buzz is made of.

    A bee is not a tone with vibrato on it. Three things separate the sound
    from a synthesizer, and each one is a field here:

    * the wing tone is shaped by body resonance rather than a plain falling
      harmonic series, so it is built from formants;
    * every stroke throws air around, which is broadband noise pulsing once per
      wingbeat, not a pitched partial;
    * the wingbeat rate is never steady - it wanders irregularly, which is what
      periodic vibrato fails to imitate.
    """

    # (centre Hz, width Hz, level) resonances the wing tone is shaped by.
    formants: tuple[tuple[float, float, float], ...] = (
        (520.0, 380.0, 1.0),
        (1350.0, 900.0, 0.42),
        (2600.0, 1400.0, 0.12),
    )
    partials: int = 16
    # Turbulence: bandpassed noise, pulsed by the stroke.
    noise_mix: float = 0.55
    noise_centre: float = 1700.0
    noise_q: float = 1.1
    # How sharply the stroke pulses the noise. 1 is a gentle swell.
    stroke: float = 2.4
    # Irregular wander of the wingbeat rate: a slow drift and a rough flutter,
    # both filtered noise rather than a sine.
    drift_depth: float = 0.035
    drift_hz: float = 7.0
    flutter_depth: float = 0.022
    flutter_hz: float = 70.0
    # Slower than either, and the reason a held buzz never repeats: the bee
    # leans on its wings and eases off again over about two seconds, moving
    # pitch and loudness together the way a real one does when it is holding
    # station. Without this the sound is correct and still obviously a loop.
    sway_hz: float = 0.45
    sway_depth: float = 0.06
    swell_depth: float = 0.35


DEFAULT_TIMBRE = Timbre()


@lru_cache(maxsize=8)
def _tables(timbre: Timbre) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """One cycle of the wing tone, and the stroke envelope that rides with it."""
    wave = []
    for index in range(_TABLE_SIZE):
        angle = 2 * math.pi * index / _TABLE_SIZE
        total = 0.0
        for partial in range(1, timbre.partials + 1):
            frequency = partial * WINGBEAT
            level = sum(
                weight * math.exp(-0.5 * ((frequency - centre) / width) ** 2)
                for centre, width, weight in timbre.formants
            )
            # Offset phases keep the partials from piling into one spike, which
            # would spend the headroom on a click rather than on the buzz.
            total += level * math.sin(partial * angle + partial * 1.7)
        wave.append(total)
    peak = max(abs(v) for v in wave) or 1.0
    stroke = [
        (0.5 + 0.5 * math.sin(2 * math.pi * i / _TABLE_SIZE)) ** timbre.stroke
        for i in range(_TABLE_SIZE)
    ]
    return tuple(v / peak for v in wave), tuple(stroke)


@dataclass(frozen=True, slots=True)
class BuzzState:
    """What the bee sounds like right now."""

    gain: float
    rate: float


def buzz_state(bee, *, audible: bool = True) -> BuzzState:
    """Read a `IdleBee` as loudness and wingbeat pitch.

    Silent unless the bee is on screen in a pane we are drawing. The two
    physical cues are distance from the flower it is visiting, and its wings:
    `folded` runs to 1 as they tuck for landing and comes back on departure, so
    resting on the bloom is silent without needing a rest state of its own.
    """
    viewport = bee.viewport
    if not audible or not bee.visible or viewport is None:
        return BuzzState(0.0, WINGBEAT)
    wings = max(0.0, 1.0 - bee.folded)
    distance = math.dist((bee.x, bee.y), bee.head)
    falloff = _FALLOFF * math.hypot(viewport.width, viewport.height)
    near = 1.0 / (1.0 + (distance / falloff) ** 2)
    border = min(bee.x, viewport.width - bee.x, bee.y, viewport.height - bee.y)
    near *= smoothstep(border / (_EDGE_FADE * bee.radius))
    speed = math.hypot(bee.vx, bee.vy) / max(1.0, bee.size[0])
    rate = WINGBEAT * (0.9 + 0.2 * min(1.0, speed / _CRUISE))
    rate *= _SPIN + (1.0 - _SPIN) * wings
    return BuzzState(clamp01(wings * near), rate)


class Voice:
    """The oscillator itself: tone, turbulence, wander, and no audio device.

    Kept separate from the device so the sound can be rendered and inspected
    without one - the tests run on machines with no audio at all.
    """

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        volume: float = 1.0,
        timbre: Timbre = DEFAULT_TIMBRE,
        rng: random.Random | None = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self.sample_rate = sample_rate
        self.volume = clamp01(volume)
        self.timbre = timbre
        self.target = BuzzState(0.0, WINGBEAT)
        self._random = (rng or random).random
        self._wave, self._stroke = _tables(timbre)
        self._gain = 0.0
        self._rate = WINGBEAT
        self._phase = 0.0
        self._drift = 0.0
        self._flutter = 0.0
        self._sway = 0.0
        self._low = 0.0
        self._band = 0.0
        self._glide = 1 - math.exp(-1 / (_GLIDE * sample_rate))
        self._settle = 1 - math.exp(-1 / (_SETTLE * sample_rate))
        # One-pole smoothers turn white noise into wander at the rate we want.
        self._drift_pole = 1 - math.exp(-2 * math.pi * timbre.drift_hz / sample_rate)
        self._flutter_pole = 1 - math.exp(-2 * math.pi * timbre.flutter_hz / sample_rate)
        self._sway_pole = 1 - math.exp(-2 * math.pi * timbre.sway_hz / sample_rate)
        # State-variable bandpass for the turbulence.
        self._noise_f = 2 * math.sin(math.pi * min(0.45, timbre.noise_centre / sample_rate))
        self._noise_q = 1 / timbre.noise_q
        # Smoothed noise loses most of its amplitude; these put the wander back
        # to roughly unit scale so the depths mean what they say.
        self._drift_scale = 1 / math.sqrt(self._drift_pole / 2)
        self._flutter_scale = 1 / math.sqrt(self._flutter_pole / 2)
        self._sway_scale = 1 / math.sqrt(self._sway_pole / 2)

    @property
    def gain(self) -> float:
        """The amplitude actually being emitted, after the glide."""
        return self._gain

    def silent(self) -> bool:
        return self._gain < 1e-4 and self.target.gain < 1e-4

    def render(self, frames: int) -> bytes:
        """Signed 16-bit mono samples, continuing the state of the last call."""
        if frames < 0:
            raise ValueError("frames must be non-negative")
        block = array("h", bytes(2 * frames))
        gain, rate, phase = self._gain, self._rate, self._phase
        drift, flutter, low, band = self._drift, self._flutter, self._low, self._band
        sway = self._sway
        wave, stroke, timbre = self._wave, self._stroke, self.timbre
        target, glide, settle = self.target, self._glide, self._settle
        drift_pole, flutter_pole = self._drift_pole, self._flutter_pole
        sway_pole = self._sway_pole
        drift_depth = timbre.drift_depth * self._drift_scale
        flutter_depth = timbre.flutter_depth * self._flutter_scale
        sway_depth = timbre.sway_depth * self._sway_scale
        swell_depth = timbre.swell_depth * self._sway_scale
        noise_f, noise_q, noise_mix = self._noise_f, self._noise_q, timbre.noise_mix
        inverse_rate = 1 / self.sample_rate
        scale = _HEADROOM * self.volume * 32767
        white = self._random
        for index in range(frames):
            gain += (target.gain - gain) * settle
            rate += (target.rate - rate) * glide
            # Two bands of filtered noise: a slow drift in how fast the wings
            # are going, and a rougher flutter on top of it.
            drift += (white() - 0.5 - drift) * drift_pole
            flutter += (white() - 0.5 - flutter) * flutter_pole
            sway += (white() - 0.5 - sway) * sway_pole
            wander = 1 + sway_depth * sway + drift_depth * drift + flutter_depth * flutter
            phase += rate * wander * inverse_rate
            if phase >= 1.0:
                phase -= int(phase)
            position = phase * _TABLE_SIZE
            low_index = int(position)
            fraction = position - low_index
            low_index &= _MASK
            high_index = (low_index + 1) & _MASK
            tone = wave[low_index] + (wave[high_index] - wave[low_index]) * fraction
            # Turbulence: white noise through a bandpass, opened once per
            # stroke. This is most of what makes it a bee and not a tone.
            source = white() * 2 - 1
            low += noise_f * band
            band += noise_f * (source - low - noise_q * band)
            # The same sway swells the loudness, so effort and volume move
            # together instead of a pitch slide under a flat tone.
            swell = 1 + swell_depth * sway
            value = (tone + noise_mix * band * stroke[low_index]) * gain * swell * scale
            block[index] = -32768 if value < -32768 else (32767 if value > 32767 else int(value))
        self._gain, self._rate, self._phase = gain, rate, phase
        self._drift, self._flutter, self._low, self._band = drift, flutter, low, band
        self._sway = sway
        return block.tobytes()


MISSING_BACKEND = (
    "flower-cursor: buzzing needs the optional sound extra "
    "(uv sync --extra sound, or pip install 'flower-cursor[sound]'); "
    "the bee stays silent"
)


class Device(Protocol):
    """The part of a backend's playback device this module uses."""

    def start(self, stream: Generator[bytes, int], /) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


def _open_miniaudio(sample_rate: int) -> Device:
    """Open a mono playback device, importing the optional backend lazily."""
    import miniaudio

    return miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=sample_rate,
    )


def _have_miniaudio() -> bool:
    """Whether the optional backend is installed, without paying to import it."""
    return importlib.util.find_spec("miniaudio") is not None


class BeeBuzz:
    """Drive `Voice` from a bee, out of whatever device the backend gives us.

    A missing extra, a machine with no sound card, and a backend that refuses
    to open all land in the same place: one line of report, and the flower
    cursor carries on exactly as it does without sound.

    The device runs only while there is something to hear. A running device
    asks for samples whether or not they are silent, and synthesizing them is
    most of what the buzz costs, so it is stopped each time the voice falls
    silent - a bee resting on the bloom, one that has left, a window that lost
    focus - and started again when the bee is next audible. It is not opened
    at all until then: importing the backend and opening a device cost a window
    ~20 MB and ~0.1 s, which a window no bee ever buzzes in should not pay, and
    that tenth of a second is spent on a worker thread so the flower does not
    stutter as the bee arrives.
    """

    def __init__(
        self,
        *,
        volume: float = 1.0,
        sample_rate: int = SAMPLE_RATE,
        open_device: Callable[[int], Device] = _open_miniaudio,
        backend_installed: Callable[[], bool] = _have_miniaudio,
        report: Callable[[str], None] | None = None,
    ) -> None:
        self.voice = Voice(sample_rate=sample_rate, volume=volume)
        self._open_device = open_device
        self._backend_installed = backend_installed
        self._report = report
        self.started = False
        # Whether the device should be running, as last asked of the worker.
        self.playing = False
        self._requests: queue.SimpleQueue[bool | None] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None
        # A failure on the worker, held until the owning thread can say it.
        self._failure: str | None = None

    def _say(self, message: str) -> None:
        if self._report is not None:
            self._report(message)

    def start(self) -> bool:
        """Get ready to buzz. False means the bee is silent and nothing broke.

        Only the backend's presence is checked here; the device itself opens
        the first time the bee is audible.
        """
        if self.started:
            return True
        if not self._backend_installed():
            self._say(MISSING_BACKEND)
            return False
        self._worker = threading.Thread(target=self._run, name="bee-buzz", daemon=True)
        self._worker.start()
        self.started = True
        return True

    def _run(self) -> None:
        """Own the device: open it when first wanted, start and stop it after."""
        device: Device | None = None
        stream = self._samples()
        next(stream)
        running = False
        while (wanted := self._requests.get()) is not None:
            if wanted == running:
                continue
            if device is None:
                try:
                    device = self._open_device(self.voice.sample_rate)
                except ImportError:
                    self._fail(MISSING_BACKEND)
                    return
                except Exception as error:  # no card, no server, no permission
                    self._fail(
                        f"flower-cursor: no audio device for the bee ({error}); it stays silent"
                    )
                    return
            try:
                if wanted:
                    device.start(stream)
                else:
                    device.stop()
            except Exception as error:
                self._fail(f"flower-cursor: could not play bee audio ({error}); it stays silent")
                self._shutdown(device)
                return
            running = wanted
        if device is not None:
            self._shutdown(device)

    def _fail(self, message: str) -> None:
        self._failure = message
        self.started = False

    def _say_failure(self) -> None:
        if self._failure is not None:
            message, self._failure = self._failure, None
            self.playing = False
            self._say(message)

    def _request(self, playing: bool) -> None:
        if playing != self.playing:
            self.playing = playing
            self._requests.put(playing)

    def _samples(self) -> Generator[bytes, int]:
        frames = yield b""
        while True:
            frames = yield self.voice.render(frames)

    def update(self, bee, *, audible: bool = True) -> None:
        """Point the voice at the bee's current state; cheap enough per tick."""
        self._say_failure()
        if not self.started:
            return
        self.voice.target = buzz_state(bee, audible=audible)
        if self.voice.target.gain > 0:
            self._request(True)
        elif self.voice.silent():
            self._request(False)

    def mute(self) -> None:
        if self.started:
            self.voice.target = BuzzState(0.0, self.voice.target.rate)

    def close(self) -> None:
        """Fade out, then let the device go. Called on the way out of a window."""
        if self.started:
            self.mute()
            if self.playing:
                deadline = time.monotonic() + _FADE_OUT
                while not self.voice.silent() and time.monotonic() < deadline:
                    time.sleep(0.005)
        if self._worker is not None:
            self._requests.put(None)
            self._worker.join(timeout=1.0)
            self._worker = None
        # Whatever went wrong on the worker is said once it can no longer race us.
        self._say_failure()
        self.started = self.playing = False

    @staticmethod
    def _shutdown(device: Device) -> None:
        for method in ("stop", "close"):
            try:
                getattr(device, method)()
            except Exception:
                pass
