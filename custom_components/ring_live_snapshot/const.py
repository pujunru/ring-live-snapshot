DOMAIN = "ring_live_snapshot"

SERVICE_CAPTURE = "capture"
SERVICE_DRAW_BOXES = "draw_boxes"
SERVICE_DETECT = "detect"
CONF_ROBOFLOW_API_KEY = "roboflow_api_key"
ATTR_MODEL_ID = "model_id"
ATTR_CONFIDENCE = "confidence"
ATTR_CONFIRM = "confirm"
ATTR_MIN_IOU = "min_iou"
ATTR_FILENAME = "filename"
ATTR_SETTLE = "settle"
ATTR_TIMEOUT = "timeout"
ATTR_SOURCE = "source"
ATTR_OBJECTS = "objects"

DEFAULT_SETTLE = 1.5  # seconds of video to read past the first (often blocky) frame
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONFIDENCE = 0.4
DEFAULT_MIN_IOU = 0.3  # how much a Roboflow box must overlap a box passed in `confirm`
