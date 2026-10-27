"""End-to-end against real Home Assistant with a fake WebRTC camera.

The fake camera answers offers with a real aiortc peer that streams solid
red frames, so the grabber goes through actual ICE, DTLS and video decoding.
"""

import io

import av
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
import numpy as np
from PIL import Image
import pytest
from webrtc_models import RTCConfiguration, RTCIceServer

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.const import DATA_COMPONENT
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ring_live_snapshot.const import DOMAIN
from custom_components.ring_live_snapshot.grabber import _ice_servers, async_grab_frame

CAMERA = "camera.fake_garage"
PLAIN_CAMERA = "camera.fake_plain"


class RedTrack(VideoStreamTrack):
    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame.from_ndarray(np.full((240, 320, 3), (220, 20, 20), np.uint8), format="rgb24")
        frame.pts, frame.time_base = pts, time_base
        return frame


class FakeWebRTCCamera(Camera):
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, name: str, mode: str = "stream") -> None:
        super().__init__()
        self._attr_name = name
        self.mode = mode
        self.sessions: dict[str, RTCPeerConnection] = {}
        self.closed: list[str] = []

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        if self.mode == "error":
            send_message(WebRTCError("bad", "camera offline"))
            return
        if self.mode == "silent":
            return
        pc = self.sessions[session_id] = RTCPeerConnection()
        await pc.setRemoteDescription(RTCSessionDescription(offer_sdp, "offer"))
        for transceiver in pc.getTransceivers():
            if transceiver.kind == "video":
                transceiver.sender.replaceTrack(RedTrack())
                transceiver.direction = "sendonly"
        await pc.setLocalDescription(await pc.createAnswer())
        send_message(WebRTCAnswer(pc.localDescription.sdp))

    async def async_on_webrtc_candidate(self, session_id, candidate) -> None:
        pass

    def close_webrtc_session(self, session_id: str) -> None:
        self.closed.append(session_id)
        if pc := self.sessions.pop(session_id, None):
            self.hass.async_create_task(pc.close())


class PlainCamera(Camera):
    _attr_name = "Fake plain"

    async def async_camera_image(self, width=None, height=None):
        return b""


@pytest.fixture
async def camera(hass: HomeAssistant) -> FakeWebRTCCamera:
    assert await async_setup_component(hass, "camera", {})
    cam = FakeWebRTCCamera("Fake garage")
    await hass.data[DATA_COMPONENT].async_add_entities([cam, PlainCamera()])
    await hass.async_block_till_done()
    yield cam
    for pc in list(cam.sessions.values()):
        await pc.close()


def assert_red_jpeg(data: bytes, size=(320, 240)) -> None:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert img.size == size
    r, g, b = img.getpixel((160, 120))
    assert r > 180 and g < 70 and b < 70


async def test_capture_action_saves_file_and_responds(hass: HomeAssistant, camera, tmp_path) -> None:
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await async_setup_component(hass, DOMAIN, {})
    target = tmp_path / "garage" / "live.jpg"

    response = await hass.services.async_call(
        DOMAIN,
        "capture",
        {"entity_id": CAMERA, "filename": str(target), "settle": 0.3},
        blocking=True,
        return_response=True,
    )

    assert response["width"] == 320 and response["height"] == 240
    assert response["filename"] == str(target)
    assert 0 < response["seconds"] < 30
    assert_red_jpeg(target.read_bytes())
    assert len(camera.closed) == 1 and not camera.sessions


async def test_capture_rejects_path_outside_allowlist(hass: HomeAssistant, camera) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(HomeAssistantError, match="allowlist"):
        await hass.services.async_call(
            DOMAIN, "capture", {"entity_id": CAMERA, "filename": "/etc/x.jpg"}, blocking=True
        )


async def test_camera_error_is_raised(hass: HomeAssistant, camera) -> None:
    camera.mode = "error"
    with pytest.raises(HomeAssistantError, match="camera offline"):
        await async_grab_frame(camera, settle=0.1, timeout=5)
    assert camera.closed


async def test_silent_camera_times_out(hass: HomeAssistant, camera) -> None:
    camera.mode = "silent"
    with pytest.raises(HomeAssistantError, match="No frame"):
        await async_grab_frame(camera, settle=0.1, timeout=1)
    assert camera.closed


async def test_config_flow_rejects_camera_without_webrtc(hass: HomeAssistant, camera) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITY_ID: PLAIN_CAMERA})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_webrtc"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITY_ID: CAMERA})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fake garage"
    assert result["data"] == {CONF_ENTITY_ID: CAMERA}


async def test_button_updates_image_entity(hass: HomeAssistant, camera, hass_client) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=CAMERA, title="Fake garage", data={CONF_ENTITY_ID: CAMERA}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    buttons = [s.entity_id for s in hass.states.async_all("button")]
    images = [s.entity_id for s in hass.states.async_all("image")]
    assert len(buttons) == 1 and len(images) == 1
    assert hass.states.get(images[0]).state == "unknown"

    await hass.services.async_call("button", "press", {"entity_id": buttons[0]}, blocking=True)
    await hass.async_block_till_done()

    state = hass.states.get(images[0])
    assert state.state != "unknown"
    assert state.attributes["width"] == 320
    client = await hass_client()
    resp = await client.get(state.attributes["entity_picture"])
    assert resp.status == 200
    assert_red_jpeg(await resp.read())


async def test_ice_servers_keep_turn_only(hass: HomeAssistant, camera) -> None:
    camera._async_get_webrtc_client_configuration = lambda: WebRTCClientConfiguration(
        configuration=RTCConfiguration(
            ice_servers=[
                RTCIceServer(urls="stun:stun.example.com:3478"),
                RTCIceServer(
                    urls=["stun:turn.example.com", "turn:turn.example.com?transport=udp"],
                    username="u",
                    credential="p",
                ),
            ]
        )
    )
    servers = _ice_servers(camera)
    assert [(s.urls, s.username, s.credential) for s in servers] == [
        (["turn:turn.example.com?transport=udp"], "u", "p")
    ]
