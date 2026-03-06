# Amazon Rekognition + UPS Delivery Monitor
## Showcase-Level Project Requirements

> **Document Purpose:** This file defines the engineering specification for a production-grade, showcase-level AI monitoring system. It is written to demonstrate the kind of structured, requirements-driven thinking expected of an Applied AI Engineer.

---

## 1. Project Goal

Build a **computer vision pipeline** that detects UPS/FedEx delivery events from a camera feed and triggers automated multi-channel alerts — all within **< 15 seconds** of the delivery event.

**What this demonstrates:**
- Applied AI & computer vision API integration  
- Automation pipeline design  
- Event-driven system architecture  
- Measurable performance engineering  

---

## 2. Business Problem

| Problem | Impact |
|---------|--------|
| Manual camera monitoring | High labor cost, human error |
| Delayed discovery of deliveries | Package theft risk (porch piracy) |
| No audit trail | No accountability for missed deliveries |
| No notification system | Workflow disruption |

**Solution:** An autonomous AI detection pipeline that monitors, detects, and notifies — zero human intervention required.

---

## 3. Functional Requirements

### 3.1 Camera Monitoring

| Requirement | Specification |
|-------------|--------------|
| Input sources | IP camera (RTSP), local webcam (OpenCV), static image (test mode) |
| Capture interval | 5–10 seconds per frame (configurable) |
| Resolution | Minimum 640×480; recommends 1080p |
| Failure handling | Auto-reconnect on stream loss |

### 3.2 Vision Detection (Amazon Rekognition)

Detection uses `DetectLabels` API with confidence thresholding:

```
Trigger Condition:
  Person detected (confidence > 80%)
  AND Package/Luggage detected (confidence > 70%)
  AND Person location near entry point
```

**Required Labels:**
- `Person` — human presence
- `Package`, `Cardboard`, `Box` — delivery item
- `Door`, `Porch` — location context (optional enhancement)

### 3.3 Event Trigger Logic

```
State Machine:
  IDLE → CANDIDATE (person detected)
  CANDIDATE → CONFIRMED (package + person ≥ 2 consecutive frames)
  CONFIRMED → ALERTED (notification sent)
  ALERTED → COOLDOWN (30-min dedup window)
  COOLDOWN → IDLE (after cooldown expires)
```

**Deduplication:** Minimum 30-minute gap between alerts for the same zone to prevent spam.

### 3.4 Notification System

| Channel | Trigger | Content |
|---------|---------|---------|
| Email (SES/SMTP) | Confirmed delivery | Image + timestamp + labels |
| Slack Webhook | Confirmed delivery | Rich message with attachment |
| SNS Topic | All events | Raw JSON payload |
| Local log | Every detection | Full label JSON |

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Camera Input Layer                 │
│         IP Camera (RTSP) / Webcam / Static           │
└──────────────────────┬──────────────────────────────┘
                       │ Frame (JPEG)
                       ▼
┌─────────────────────────────────────────────────────┐
│              Image Capture Service                   │
│         capture.py — OpenCV / requests               │
└──────────────────────┬──────────────────────────────┘
                       │ Base64 / Bytes
                       ▼
┌─────────────────────────────────────────────────────┐
│           Amazon Rekognition API Layer               │
│     rekognition.py — DetectLabels / DetectFaces      │
└──────────────────────┬──────────────────────────────┘
                       │ Label Results
                       ▼
┌─────────────────────────────────────────────────────┐
│            Detection Logic Engine                    │
│   detection_logic.py — State machine + dedup         │
└──────────┬───────────────────────────┬──────────────┘
           │ CONFIRMED event            │ All events
           ▼                           ▼
┌────────────────────┐    ┌────────────────────────────┐
│  Notification Hub  │    │     Event Logger            │
│   notifier.py      │    │  logger.py — JSON/SQLite    │
│  Email/Slack/SNS   │    └────────────────────────────┘
└────────────────────┘
```

---

## 5. Tech Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| AI Vision | Amazon Rekognition | Production-grade CV API, no model training needed |
| Backend | Python 3.11+ | Ecosystem maturity, boto3 support |
| Camera | OpenCV, FFmpeg | Industry standard video processing |
| Notification | Amazon SES + Slack Webhooks | Multi-channel, production-grade |
| Infrastructure | AWS Lambda / Local daemon | Scales from dev to prod seamlessly |
| Storage | SQLite (local) / DynamoDB (cloud) | Flexible for demo and production |
| Container | Docker | Reproducible, deployment-ready |
| Config | YAML + dotenv | Twelve-Factor App compliant |
| CI | GitHub Actions | Real workflow validation |

---

## 6. Performance Targets

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Detection latency | < 15 seconds | Capture time → alert sent timestamp |
| Rekognition API latency | < 3 seconds per call | boto3 response timing |
| False positive rate | < 10% | Manual review of alert log |
| System uptime | Continuous (daemon) | Process supervisor / Lambda schedule |
| Alert dedup accuracy | 100% | No duplicate alerts within 30-min window |

---

## 7. Data Logging Schema

Each detection event is logged as:

```json
{
  "event_id": "uuid-v4",
  "timestamp": "2024-01-15T14:32:01Z",
  "frame_path": "frames/2024-01-15_14-32-01.jpg",
  "labels": [
    {"name": "Person", "confidence": 92.4},
    {"name": "Package", "confidence": 87.1},
    {"name": "Door", "confidence": 78.3}
  ],
  "delivery_detected": true,
  "alert_sent": true,
  "alert_channels": ["email", "slack"],
  "state_transition": "CANDIDATE → CONFIRMED → ALERTED",
  "processing_time_ms": 2840
}
```

---

## 8. Repository Structure

```
vision-delivery-alert/
├── README.md                    # Hiring-manager-facing overview
├── PROJECT_REQUIREMENTS.md      # This document
├── system_design.md             # Engineering design decisions
├── architecture.md              # Detailed component docs
├── api_flow.md                  # API sequence diagrams
├── demo_plan.md                 # Demo walkthrough
│
├── src/
│   ├── monitor.py               # Main orchestrator
│   ├── capture.py               # Image capture service
│   ├── rekognition.py           # AWS Rekognition wrapper
│   ├── detection_logic.py       # State machine + event logic
│   ├── notifier.py              # Multi-channel notifications
│   ├── logger.py                # Structured event logging
│   └── config.py                # Configuration loader
│
├── lambda/
│   └── handler.py               # AWS Lambda entry point
│
├── dashboard/
│   ├── index.html               # Real-time web dashboard
│   ├── app.js                   # Dashboard WebSocket logic
│   └── style.css                # Dark-mode premium UI
│
├── tests/
│   ├── test_detection_logic.py
│   └── test_rekognition_mock.py
│
├── docs/
│   └── architecture_diagram.png
│
├── demo/
│   ├── sample_detection_log.json
│   └── sample_alert.json
│
├── config.yaml                  # Configuration template
├── .env.example                 # Environment variables
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .github/
    └── workflows/
        └── ci.yml
```

---

## 9. Demo Requirements

The repository must communicate the following within 30 seconds:

- [x] Problem being solved (porch piracy / missed deliveries)  
- [x] Architecture diagram  
- [x] Tech stack with justification  
- [x] Live demo GIF showing end-to-end flow  
- [x] Sample detection output (logs + alert)  
- [x] Quick-start instructions (< 5 commands)  
- [x] Performance metrics achieved  

---

## 10. Optional Advanced Features

| Feature | Engineering Signal |
|---------|-------------------|
| Multi-camera support | Concurrent stream processing |
| Object tracking (SORT) | Inter-frame continuity |
| Confidence scoring | Calibrated AI outputs |
| Slack rich notifications | API integration depth |
| Web dashboard | Full-stack capability |
| AWS Lambda deployment | Serverless architecture |
| DynamoDB event store | Cloud-native persistence |

---

## 11. Success Criteria

A **hiring manager** reviewing this project should conclude:

> "This candidate can design, build, and deploy an AI-powered monitoring system against real-world requirements — not just call an API."
