"""Pull one fresh frame from a camera's WebRTC live view.

Ring cameras only hand Home Assistant a cached image: the first frame of the
last recorded event, which can be hours old. The live view is the only way to
see the scene now, and Ring serves it over WebRTC alone. This module plays
the browser's part: it offers a receive-only session to the camera entity,
waits for video, and keeps the newest frame once the stream has settled.

The first frames arrive as a buffered burst that starts on a keyframe and is
often blocky, so the grabber keeps reading for `settle` seconds after the
first frame and returns the last one it saw.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
import logging
import time

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp
import av

from homeassistant.components.camera import Camera, StreamType
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
    WebRTCMessage,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.ulid import ulid_now

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    width: int
    height: int
    seconds: float  # from sending the offer to keeping this frame


def supports_webrtc(camera: Camera) -> bool:
    return StreamType.WEB_RTC in camera.camera_capabilities.frontend_stream_types


def _ice_servers(camera: Camera) -> list[RTCIceServer]:
    """TURN servers only.

    aiortc queries STUN on every interface (Docker bridges, Tailscale, IPv6)
    before the offer goes out, which cost ~5s on HAOS. Ring's media servers
    are public, so our host candidates reach them and learn the NAT mapping
    as peer-reflexive candidates; STUN adds nothing.
    """
    config = camera.async_get_webrtc_client_configuration().configuration
    servers = []
    for server in config.ice_servers:
        urls = [server.urls] if isinstance(server.urls, str) else list(server.urls)
        if urls := [url for url in urls if url.startswith(("turn:", "turns:"))]:
            servers.append(RTCIceServer(urls=urls, username=server.username, credential=server.credential))
    return servers


def _encode_jpeg(frame: av.VideoFrame, quality: int) -> bytes:
    buf = io.BytesIO()
    frame.to_image().save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


async def async_grab_frame(camera: Camera, *, settle: float, timeout: float, quality: int = 90) -> Frame:
    """Open the live view, return the newest frame after `settle` seconds, close it."""
    if not supports_webrtc(camera):
        raise HomeAssistantError(f"{camera.entity_id} has no WebRTC live view")

    hass = camera.hass
    session_id = ulid_now()
    started = time.monotonic()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=_ice_servers(camera)))
    pc.addTransceiver("video", direction="recvonly")
    # Ring rejects offers without an audio section.
    pc.addTransceiver("audio", direction="recvonly")

    messages: asyncio.Queue[WebRTCMessage] = asyncio.Queue()
    frame_ready: asyncio.Future[av.VideoFrame] = hass.loop.create_future()
    tasks: list[asyncio.Task] = []

    @pc.on("track")
    def on_track(track) -> None:
        if track.kind == "video":
            tasks.append(hass.async_create_background_task(_read_video(track), "ring_live_snapshot_video"))
        else:
            tasks.append(hass.async_create_background_task(_drain(track), "ring_live_snapshot_audio"))

    async def _drain(track) -> None:
        try:
            while True:
                await track.recv()
        except MediaStreamError:
            pass

    async def _read_video(track) -> None:
        latest: av.VideoFrame | None = None
        deadline: float | None = None
        try:
            while deadline is None or time.monotonic() < deadline:
                latest = await track.recv()
                if deadline is None:
                    _LOGGER.debug("%s first frame after %.2fs", camera.entity_id, time.monotonic() - started)
                    deadline = time.monotonic() + settle
        except MediaStreamError:
            pass  # stream ended early; keep what we have
        if not frame_ready.done():
            if latest is None:
                frame_ready.set_exception(HomeAssistantError(f"{camera.entity_id} sent no video"))
            else:
                frame_ready.set_result(latest)

    async def _signal() -> None:
        while True:
            message = await messages.get()
            if isinstance(message, WebRTCAnswer):
                await pc.setRemoteDescription(RTCSessionDescription(message.answer, "answer"))
            elif isinstance(message, WebRTCCandidate):
                init = message.candidate
                sdp = init.candidate.removeprefix("candidate:")
                if not sdp:
                    continue  # end-of-candidates
                candidate = candidate_from_sdp(sdp)
                candidate.sdpMid = getattr(init, "sdp_mid", None)
                candidate.sdpMLineIndex = getattr(init, "sdp_m_line_index", None)
                if candidate.sdpMid is None and candidate.sdpMLineIndex is None:
                    candidate.sdpMLineIndex = 0
                await pc.addIceCandidate(candidate)
            elif isinstance(message, WebRTCError) and not frame_ready.done():
                frame_ready.set_exception(
                    HomeAssistantError(f"{camera.entity_id} live view failed: {message.message}")
                )

    try:
        async with asyncio.timeout(timeout):
            # aiortc gathers every local candidate into the offer, so no
            # trickle ICE is needed in this direction.
            await pc.setLocalDescription(await pc.createOffer())
            tasks.append(hass.async_create_background_task(_signal(), "ring_live_snapshot_signal"))
            await camera.async_handle_async_webrtc_offer(
                pc.localDescription.sdp, session_id, messages.put_nowait
            )
            frame = await frame_ready
    except TimeoutError as err:
        raise HomeAssistantError(f"No frame from {camera.entity_id} within {timeout:.0f}s") from err
    finally:
        camera.close_webrtc_session(session_id)
        for task in tasks:
            task.cancel()
        await pc.close()

    seconds = time.monotonic() - started
    jpeg = await hass.async_add_executor_job(_encode_jpeg, frame, quality)
    return Frame(jpeg=jpeg, width=frame.width, height=frame.height, seconds=round(seconds, 2))
