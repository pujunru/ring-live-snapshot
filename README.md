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

- **Action `ring_live_snapshot.draw_boxes`** — draws labelled boxes on a saved
  frame. `box_2d` is `[ymin, xmin, ymax, xmax]` on a 0–1000 scale, the format
  Gemini returns, so an AI task's answer can be passed straight in:

  ```yaml
  action: ring_live_snapshot.draw_boxes
  data:
    source: /media/garage_front/live.jpg
    filename: /media/garage_front/live_boxed.jpg
    objects: [{label: package, box_2d: [561, 428, 750, 555]}]
  ```

- **Action `ring_live_snapshot.detect`** — runs a [Roboflow](https://roboflow.com)
  object detection model on a saved frame through the hosted API and returns
  boxes in the same format, so it can double-check a vision LLM's answer:

  ```yaml
  action: ring_live_snapshot.detect
  data:
    source: /media/garage_front/live.jpg
    model_id: package-detection/3                    # Roboflow model ID / version
    confidence: 0.4                                  # optional
    filename: /media/garage_front/live_roboflow.jpg  # optional, boxes drawn
    confirm: "{{ packages }}"                        # optional, e.g. Gemini's boxes
    min_iou: 0.3                                     # optional, overlap needed to confirm
  response_variable: rf  # {objects: [{label, confidence, box_2d}], count, confirmed: [...]}
  ```

  With `confirm`, the response's `confirmed` list keeps the boxes you passed in
  that a Roboflow box overlaps, matched by position only.

  It needs a Roboflow API key (the free plan is plenty for a few checks a day)
  in `configuration.yaml`, kept in `secrets.yaml`:

  ```yaml
  ring_live_snapshot:
    roboflow_api_key: !secret roboflow_api_key
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

`examples/garage_front_package_watch.yaml` scans the driveway hourly, after
motion and after the garage door moves, asks an AI task (Gemini) what is new
compared with a photo of the empty driveway, keeps only packages a Roboflow
model also boxes in the same spot, confirms with a second frame, and notifies
with the boxed photo.

## Development

```bash
uv venv -p 3.13 .venv-ha
VIRTUAL_ENV=.venv-ha uv pip install pytest-homeassistant-custom-component aiortc==1.15.0 pillow PyTurboJPEG numpy
.venv-ha/bin/pytest -c tests_ha/pytest.ini tests_ha
```

The tests run a real aiortc peer as a fake camera over loopback.
