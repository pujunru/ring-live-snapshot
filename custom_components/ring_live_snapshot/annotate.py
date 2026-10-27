"""Draw labelled boxes on a saved frame.

Boxes use the convention vision LLMs such as Gemini emit: `box_2d` is
[ymin, xmin, ymax, xmax] normalized to 0-1000, so a model's answer can be
passed straight through from an AI task.
"""

from __future__ import annotations

import os
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PALETTE = ["#ff3b30", "#0a84ff", "#30d158", "#ffd60a", "#bf5af2", "#ff9f0a", "#64d2ff"]


def draw_boxes(source: str, target: str, objects: list[dict[str, Any]]) -> int:
    """Write `source` with boxes drawn to `target`; return how many were drawn."""
    image = Image.open(source).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    line = max(2, width // 320)
    font = ImageFont.load_default(size=max(14, width // 50))
    colors: dict[str, str] = {}
    drawn = 0
    for obj in objects:
        ymin, xmin, ymax, xmax = (min(max(float(v), 0), 1000) for v in obj["box_2d"])
        if xmax <= xmin or ymax <= ymin:
            continue
        box = (xmin * width / 1000, ymin * height / 1000, xmax * width / 1000, ymax * height / 1000)
        label = str(obj.get("label", ""))
        color = colors.setdefault(label, PALETTE[len(colors) % len(PALETTE)])
        draw.rectangle(box, outline=color, width=line)
        if label:
            text_y = box[1] - font.size - line * 2
            draw.text(
                (box[0] + line, text_y if text_y >= 0 else box[3] + line),
                label,
                fill=color,
                font=font,
                stroke_width=2,
                stroke_fill="black",
            )
        drawn += 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    image.save(target, format="JPEG", quality=90)
    return drawn
