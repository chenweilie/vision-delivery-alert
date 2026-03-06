# Architecture Documentation
## Amazon Rekognition Delivery Monitor

---

## Component Overview

```
vision-delivery-alert/
├── src/
│   ├── monitor.py           ← Orchestrator
│   ├── capture.py           ← Input Layer
│   ├── rekognition.py       ← AI Vision Layer  
│   ├── detection_logic.py   ← Decision Layer
│   ├── notifier.py          ← Action Layer
│   ├── logger.py            ← Persistence Layer
│   └── config.py            ← Config Layer
├── lambda/handler.py        ← Serverless Variant
└── dashboard/               ← Observability Layer
```

---

## Component Details

### `capture.py` — Image Input Layer

**Responsibility:** Acquire frames from any camera source.

**Supported sources:**
- `webcam` — OpenCV VideoCapture with index (e.g., `"0"`)
- `ip_camera` — RTSP/HTTP stream (auto-detected from URL prefix)
- `static_image` — JPEG file path (testing, demo)

**Key behaviors:**
- Source type auto-detected from input string
- Auto-reconnect on stream loss (configurable attempts + delay)
- JPEG encoding at 90% quality before sending to Rekognition
- Optional frame saving to disk for audit trail

**Interface:**
```python
capture = ImageCapture(source="0", save_dir="frames/")
jpeg_bytes, frame_path = capture.capture_frame()
```

---

### `rekognition.py` — Computer Vision Layer

**Responsibility:** Call Amazon Rekognition and return structured results.

**Key behaviors:**
- Thin wrapper over `boto3.client("rekognition")`
- Labels sorted by confidence (descending)
- Exponential backoff on `ThrottlingException` (3 attempts by default)
- Fast-fail on non-retriable errors (`InvalidImageException`, etc.)
- `hit S3` variant for Lambda deployment (no bytes transfer)

**Interface:**
```python
client = RekognitionClient(region="us-east-1", min_confidence=70.0)
result = client.detect_labels_from_bytes(jpeg_bytes)

result.has_label("Person", min_confidence=80.0)  # → True/False
result.has_any_label(["Package", "Box"], min_confidence=70.0)  # → (found, name, conf)
```

**Cost:** ~$0.001 per API call (Amazon Rekognition public pricing).

---

### `detection_logic.py` — Decision Engine

**Responsibility:** Determine whether detected labels constitute a delivery event.

This is the most business-critical component. See [System Design](system_design.md#22-why-a-state-machine) for the case for state machines.

**State Machine:**
```
IDLE ──▶ CANDIDATE ──▶ CONFIRMED ──▶ ALERTED ──▶ COOLDOWN ──▶ IDLE
              ↑___________timeout_______________↑
```

**Configurable parameters:**
- `confirmation_frames` — frames required to confirm (default: 2)
- `cooldown_minutes` — dedup window (default: 30)
- `state_timeout_seconds` — CANDIDATE state reset (default: 60)
- `person_confidence` — minimum person confidence (default: 80%)
- `package_confidence` — minimum package confidence (default: 70%)

---

### `notifier.py` — Automation Layer

**Responsibility:** Dispatch alerts to all configured channels.

**Channels:**
| Channel | Protocol | Auth |
|---------|---------|------|
| Email | SMTP + STARTTLS | Username + App Password |
| Slack | HTTPS POST | Incoming Webhook URL |
| Generic Webhook | HTTPS POST | Optional HMAC secret |

**Design:** Fan-out pattern. Each channel is independent. Failure in one never blocks others.

---

### `logger.py` — Persistence Layer

**Dual-format storage:**
1. **SQLite** (`events.db`) — Structured, queryable event history
2. **JSONL** (`events.jsonl`) — Append-only line-delimited JSON for export and grep

**Schema:**
```sql
CREATE TABLE detection_events (
    event_id           TEXT PRIMARY KEY,
    timestamp          TEXT,
    frame_path         TEXT,
    labels_json        TEXT,   -- JSON array of label dicts
    delivery_detected  INTEGER,
    alert_sent         INTEGER,
    alert_channels     TEXT,   -- JSON array of channel names
    state_transition   TEXT,
    processing_time_ms INTEGER,
    person_confidence  REAL,
    package_confidence REAL
)
```

---

### `lambda/handler.py` — Serverless Variant

**Key design:** The `DeliveryDetector` instance is created at module level (outside the handler function). This allows the 5-state machine to **persist across Lambda warm invocations**, correctly maintaining multi-frame confirmation even in a serverless environment.

**Trigger:** EventBridge Scheduler rule, every 5–10 seconds.

**Image source:** S3 object written by a separate camera agent (e.g., a Raspberry Pi daemon uploading latest frame every 5 seconds).

---

## Data Flow Diagram

```
[Camera] ─JPEG─▶ [capture.py]
                      │
                   jpeg_bytes
                      │
                      ▼
             [rekognition.py] ──boto3──▶ [AWS Rekognition]
                      │                        │
                 DetectionResult    ◀──labels──┘
                      │
                      ▼
           [detection_logic.py]
              (State Machine)
                      │
               DeliveryEvent?
              ┌───────┴───────┐
             Yes              No
              │               │
              ▼               ▼
        [notifier.py]    [logger.py]
       Email/Slack/Hook  SQLite + JSONL
              │
              ▼
        [logger.py]
       (alert=true)
```
