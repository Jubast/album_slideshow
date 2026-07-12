from __future__ import annotations

import asyncio

from custom_components.album_slideshow import camera


def _make_cam():
    """A bare camera instance with just the render-loop interrupt state.

    Bypasses ``__init__`` (which needs hass/coordinator/store) since these
    tests only exercise ``_wait_or_interrupt``.
    """
    cam = camera.AlbumSlideshowCamera.__new__(camera.AlbumSlideshowCamera)
    cam._interrupt_event = asyncio.Event()
    cam._force_next = False
    return cam


def test_wait_returns_immediately_when_force_next_pending():
    """A "next slide" press that landed before the wait must not sleep.

    Regression for the race where a press during the previous render was
    swallowed and the user waited the full slide interval. ``_wait_or_interrupt``
    no longer clears the event, and honors a pending force-next at once.
    """
    cam = _make_cam()
    cam._force_next = True

    async def run():
        # A long timeout would hang if the pending force-next were ignored.
        return await cam._wait_or_interrupt(timeout=30)

    assert asyncio.run(run()) is True


def test_wait_wakes_on_event_without_clearing_first():
    cam = _make_cam()

    async def run():
        cam._interrupt_event.set()  # e.g. a coordinator/store change
        return await cam._wait_or_interrupt(timeout=30)

    assert asyncio.run(run()) is True


def test_wait_times_out_when_idle():
    cam = _make_cam()

    async def run():
        return await cam._wait_or_interrupt(timeout=0.01)

    assert asyncio.run(run()) is False


def test_signal_set_during_render_survives_until_wait():
    """The loop clears the event before rendering, so a signal raised while
    rendering is still pending when the wait runs."""
    cam = _make_cam()

    async def run():
        # Simulate the loop: clear before "render", raise a force-next
        # mid-render, then reach the wait.
        cam._interrupt_event.clear()
        cam._force_next = True  # pressed during render
        cam._interrupt_event.set()
        return await cam._wait_or_interrupt(timeout=30)

    assert asyncio.run(run()) is True
