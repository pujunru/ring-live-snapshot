"""End-to-end against real Home Assistant with a fake WebRTC camera.

The fake camera answers offers with a real aiortc peer that streams solid
red frames, so the grabber goes through actual ICE, DTLS and video decoding.
"""

import base64
import io
from pathlib import Path

import av
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
import numpy as np
from PIL import Image
import pytest
import yaml
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
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.ring_live_snapshot.const import DOMAIN
from custom_components.ring_live_snapshot.grabber import _ice_servers, async_grab_frame
from custom_components.ring_live_snapshot.roboflow import confirm

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


async def test_draw_boxes_outlines_gemini_box(hass: HomeAssistant, tmp_path) -> None:
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await async_setup_component(hass, DOMAIN, {})
    source, target = tmp_path / "live.jpg", tmp_path / "out" / "boxed.jpg"
    Image.new("RGB", (1000, 500), (128, 128, 128)).save(source)

    response = await hass.services.async_call(
        DOMAIN,
        "draw_boxes",
        {
            "source": str(source),
            "filename": str(target),
            # [ymin, xmin, ymax, xmax] on a 0-1000 scale; second box is degenerate.
            "objects": [{"label": "package", "box_2d": [400, 200, 800, 600]}, {"box_2d": [5, 5, 5, 5]}],
        },
        blocking=True,
        return_response=True,
    )

    assert response == {"filename": str(target), "boxes": 1}
    img = Image.open(target).convert("RGB")
    edge = img.getpixel((400, 399))  # bottom edge of the box at y=0.8*500
    inside = img.getpixel((400, 300))
    assert edge != (128, 128, 128) and max(abs(a - b) for a, b in zip(inside, (128, 128, 128))) < 8


async def test_detect_converts_roboflow_boxes(hass: HomeAssistant, tmp_path, aioclient_mock) -> None:
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {"roboflow_api_key": "test-key"}})
    source, target = tmp_path / "live.jpg", tmp_path / "rf.jpg"
    Image.new("RGB", (1000, 500), (128, 128, 128)).save(source)
    aioclient_mock.post(
        "https://serverless.roboflow.com/packages/2",
        json={
            "image": {"width": 1000, "height": 500},
            "predictions": [
                {"x": 300, "y": 200, "width": 100, "height": 50, "confidence": 0.41, "class": "box", "class_id": 0},
                {"x": 500, "y": 300, "width": 200, "height": 100, "confidence": 0.9, "class": "package", "class_id": 1},
            ],
        },
    )

    response = await hass.services.async_call(
        DOMAIN,
        "detect",
        {"source": str(source), "model_id": "packages/2", "confidence": 0.3, "filename": str(target)},
        blocking=True,
        return_response=True,
    )

    assert response == {
        "objects": [
            {"label": "package", "confidence": 0.9, "box_2d": [500, 400, 700, 600]},
            {"label": "box", "confidence": 0.41, "box_2d": [350, 250, 450, 350]},
        ],
        "count": 2,
        "filename": str(target),
    }
    method, url, data, headers = aioclient_mock.mock_calls[0]
    assert url.query["confidence"] == "0.3"
    assert headers["Authorization"] == "Bearer test-key" and "test-key" not in str(url)
    assert base64.b64decode(data) == source.read_bytes()
    assert target.exists()


async def test_detect_requires_api_key(hass: HomeAssistant, tmp_path) -> None:
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(HomeAssistantError, match="roboflow_api_key"):
        await hass.services.async_call(
            DOMAIN, "detect", {"source": str(tmp_path / "live.jpg"), "model_id": "packages/2"}, blocking=True
        )


async def test_detect_reports_api_error(hass: HomeAssistant, tmp_path, aioclient_mock) -> None:
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {"roboflow_api_key": "bad"}})
    Image.new("RGB", (10, 10)).save(tmp_path / "live.jpg")
    aioclient_mock.post("https://serverless.roboflow.com/packages/2", status=401, text="Unauthorized")
    with pytest.raises(HomeAssistantError, match="401"):
        await hass.services.async_call(
            DOMAIN, "detect", {"source": str(tmp_path / "live.jpg"), "model_id": "packages/2"}, blocking=True
        )


EXAMPLE = Path(__file__).parent.parent / "examples" / "garage_front_package_watch.yaml"
GEMINI_BOX = [400, 400, 600, 600]  # on the fake camera's 320x240 frame: x 128-192, y 96-144


async def run_example(hass: HomeAssistant, tmp_path, aioclient_mock, roboflow_xy: tuple[int, int]):
    """Run the example automation once with Gemini mocked to see one package.

    Roboflow (mocked) sees a 64x48 box centred on `roboflow_xy`. The 3-minute
    wait is cut to zero; everything else, including captures from the fake
    camera and box drawing, runs for real.
    """
    text = (
        EXAMPLE.read_text()
        .replace("/media/garage_front", str(tmp_path))
        .replace("camera.garage_front_live_view", CAMERA)
        .replace('delay: "00:03:00"', 'delay: "00:00:00"')
    )
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {"roboflow_api_key": "test-key"}})
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"package_at_garage": {}}})
    async_mock_service(
        hass,
        "ai_task",
        "generate_data",
        response={"data": {"objects_json": f'[{{"label": "package", "box_2d": {GEMINI_BOX}}}]'}},
        supports_response=SupportsResponse.ONLY,
    )
    notified = async_mock_service(hass, "notify", "mobile_app_your_phone")
    x, y = roboflow_xy
    aioclient_mock.post(
        "https://serverless.roboflow.com/package-box/1",
        json={
            "image": {"width": 320, "height": 240},
            "predictions": [{"x": x, "y": y, "width": 64, "height": 48, "confidence": 0.7, "class": "1", "class_id": 1}],
        },
    )
    assert await async_setup_component(hass, "automation", {"automation": yaml.safe_load(text)})
    await hass.services.async_call(
        "automation", "trigger", {"entity_id": "automation.garage_front_package_scan"}, blocking=True
    )
    await hass.async_block_till_done()
    return notified


async def test_example_alerts_when_roboflow_agrees(hass: HomeAssistant, camera, tmp_path, aioclient_mock) -> None:
    notified = await run_example(hass, tmp_path, aioclient_mock, roboflow_xy=(160, 120))

    assert aioclient_mock.call_count == 2  # once per frame
    assert hass.states.get("input_boolean.package_at_garage").state == "on"
    assert len(notified) == 1 and notified[0].data["message"].startswith("1 item(s)")
    assert (tmp_path / "confirm_boxed.jpg").exists()


async def test_example_ignores_package_roboflow_does_not_see(
    hass: HomeAssistant, camera, tmp_path, aioclient_mock
) -> None:
    notified = await run_example(hass, tmp_path, aioclient_mock, roboflow_xy=(40, 210))  # the "ledge"

    assert aioclient_mock.call_count == 1  # stopped before the second frame
    assert hass.states.get("input_boolean.package_at_garage").state == "off"
    assert notified == []
    assert not (tmp_path / "confirm.jpg").exists()


def test_confirm_matches_by_overlap() -> None:
    detections = [{"label": "1", "confidence": 0.6, "box_2d": [800, 520, 1000, 990]}]  # the ledge
    on_ledge = {"label": "package", "box_2d": [880, 600, 990, 760]}
    on_concrete = {"label": "package", "box_2d": [561, 428, 750, 555]}
    assert confirm([on_concrete, on_ledge], detections, 0.3) == []
    assert confirm([on_ledge], detections, 0.15) == [{**on_ledge, "iou": 0.19, "roboflow_confidence": 0.6}]
