"""Run a Roboflow object detection model on a saved frame.

Uses the serverless hosted API: the JPEG goes up base64-encoded and the key
travels in the Authorization header so it stays out of URLs and logs.
Predictions come back as pixel centres and sizes; they are converted to
`box_2d` ([ymin, xmin, ymax, xmax] normalized to 0-1000) so the result can
be passed straight to draw_boxes, the same as a Gemini answer.
"""

from __future__ import annotations

import base64
from typing import Any

import aiohttp

API_URL = "https://serverless.roboflow.com"


class RoboflowError(Exception):
    """The API rejected the request or answered with something unexpected."""


async def async_detect(
    session: aiohttp.ClientSession,
    api_key: str,
    model_id: str,
    jpeg: bytes,
    confidence: float,
    timeout: float = 30,
) -> list[dict[str, Any]]:
    """Return [{label, confidence, box_2d}] for `jpeg`, best first."""
    try:
        async with session.post(
            f"{API_URL}/{model_id.strip('/')}",
            params={"confidence": confidence},
            data=base64.b64encode(jpeg),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise RoboflowError(f"Roboflow returned {resp.status}: {(await resp.text())[:200]}")
            result = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise RoboflowError(f"Roboflow request failed: {err}") from err
    return _to_objects(result)


def _to_objects(result: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        width, height = float(result["image"]["width"]), float(result["image"]["height"])
        predictions = result["predictions"]
    except (KeyError, TypeError, ValueError) as err:
        raise RoboflowError(f"Unexpected Roboflow response: {str(result)[:200]}") from err
    objects = []
    for p in predictions:
        x, y, w, h = (float(p[k]) for k in ("x", "y", "width", "height"))
        box = [(y - h / 2) / height, (x - w / 2) / width, (y + h / 2) / height, (x + w / 2) / width]
        objects.append(
            {
                "label": str(p.get("class", "")),
                "confidence": round(float(p.get("confidence", 0)), 3),
                "box_2d": [round(min(max(v, 0), 1) * 1000) for v in box],
            }
        )
    return sorted(objects, key=lambda o: o["confidence"], reverse=True)


def iou(a: list[float], b: list[float]) -> float:
    """Intersection over union of two box_2d boxes."""
    ymin, xmin = max(a[0], b[0]), max(a[1], b[1])
    ymax, xmax = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ymax - ymin) * max(0, xmax - xmin)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


def confirm(candidates: list[dict[str, Any]], detections: list[dict[str, Any]], min_iou: float) -> list[dict[str, Any]]:
    """Keep the candidates (e.g. a vision LLM's boxes) that some detection overlaps.

    Matching is by position only: community models often have meaningless
    class names, and a second model's mistakes rarely land where the first
    one's do.
    """
    confirmed = []
    for cand in candidates:
        best = max(detections, key=lambda d: iou(cand["box_2d"], d["box_2d"]), default=None)
        if best and (score := iou(cand["box_2d"], best["box_2d"])) >= min_iou:
            confirmed.append({**cand, "iou": round(score, 2), "roboflow_confidence": best["confidence"]})
    return confirmed
