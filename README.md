# Ring Live Snapshot

Home Assistant integration that grabs a **fresh** frame from a Ring camera on demand.

The Ring integration's camera image (`camera.snapshot`, `camera_proxy`, dashboard
thumbnails) is the first frame of the last recorded event, often hours old. The
live view is the only source of what the camera sees now, and Ring serves it
over WebRTC only. This integration opens that live view inside Home Assistant,
keeps one frame, and closes the stream — about 3–5 seconds end to end.

It works with any camera entity that offers a WebRTC live view, not only Ring.

## What you get

- **Action `ring_live_snapshot.capture`** — for any WebRTC camera:

  ```yaml
  action: ring_live_snapshot.capture
  data:
    entity_id: camera.garage_front_live_view
    filename: /media/garage_front/live.jpg   # optional
    settle: 1.5                              # seconds of video to skip past the first frame
  response_variable: snap                    # {width, height, seconds, captured_at, filename}
  ```

- **Per camera you add in the UI** (Settings → Devices & services → Add → Ring Live
  Snapshot): a *Live snapshot* button and a *Live snapshot* image entity on the
  camera's device page. The image entity shows the last frame captured by the
  button or the action.

Each capture wakes a battery camera for a few seconds; requests for the same
camera queue rather than opening parallel sessions.

## Install

HACS → Custom repositories → `https://github.com/pujunru/ring-live-snapshot`
(Integration), or copy `custom_components/ring_live_snapshot` into
`/config/custom_components/` and restart. First start installs `aiortc`.

## Example

`examples/garage_front_package_watch.yaml` scans the driveway every 30 minutes
and after motion, asks the AI task whether a package is there, confirms with a
second frame, and notifies.

## Development

```bash
uv venv -p 3.13 .venv-ha
VIRTUAL_ENV=.venv-ha uv pip install pytest-homeassistant-custom-component aiortc==1.15.0 pillow PyTurboJPEG numpy
.venv-ha/bin/pytest -c tests_ha/pytest.ini tests_ha
```

The tests run a real aiortc peer as a fake camera over loopback.
