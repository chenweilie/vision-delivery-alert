# System Design Document
## Amazon Rekognition Delivery Monitor

---

## 1. Design Goals

| Goal | Rationale |
|------|-----------|
| **Low latency** | Alert within 15s of delivery event |
| **Low false positives** | Don't spam users with spurious alerts |
| **Zero-infra option** | Run on a Raspberry Pi or Lambda |
| **Maintainability** | Each component independently testable |
| **Extensibility** | Add cameras, channels, or models easily |

---

## 2. Key Design Decisions

### 2.1 Why Amazon Rekognition over a custom model?

**Decision:** Use Rekognition `DetectLabels` instead of a fine-tuned YOLO/SSD model.

**Rationale:**

| | Custom Model | Rekognition |
|--|--|--|
| Training data required | 500–5000 labeled images | None |
| Infrastructure | Model server, GPU (optional) | AWS API call |
| Accuracy (general objects) | High (if enough data) | High out-of-box |
| Operational overhead | High (versioning, serving, monitoring) | Zero |
| Cost | High (infra) | ~$0.001/image |
| Time to production | Weeks | Hours |

**Verdict:** For a monitoring system where the *business logic* (what makes a delivery) is the core IP, not the model weights, Rekognition is the correct choice. A custom model would add complexity without proportional benefit.

---

### 2.2 Why a state machine?

**Problem:** Native object detection will trigger on any frame containing a person or a package. This causes 10–50x alert spam (person walks by, FedEx truck visible, etc.).

**Solution:** 5-state machine with multi-frame confirmation and cooldown.

```
IDLE ──(person detected)──▶ CANDIDATE
CANDIDATE ──(person + package × N)──▶ CONFIRMED
CONFIRMED ──(event created)──▶ ALERTED
ALERTED ──(alert sent)──▶ COOLDOWN
COOLDOWN ──(30min elapsed)──▶ IDLE
CANDIDATE ──(timeout / person gone)──▶ IDLE
```

**Effect:**
- Requires N consecutive frames with both person AND package → eliminates single-frame false positives
- 30-minute cooldown → eliminates duplicate alerts for same delivery
- Candidate timeout → prevents stale state accumulation

**Trade-off:** +5–10 second detection delay (acceptable for this use case).

---

### 2.3 Configuration Strategy

Following **Twelve-Factor App** methodology:
- Config lives in `config.yaml` (version-controlled defaults)
- Secrets live in `.env` (gitignored) or environment variables
- Environment variables **always override** YAML values
- Works identically in local dev, Docker, and Lambda

---

### 2.4 Notification Architecture — Fan-out Pattern

```
confirmed_event
      │
      ├──▶ EmailSender (async-safe) 
      ├──▶ SlackWebhook (async-safe)
      └──▶ GenericWebhook (async-safe)
```

Each channel:
- Is **independent** — one channel failure never blocks others
- Has its own **timeout** (10s)
- **Logs** its own success/failure
- Is conditionally enabled via config

---

### 2.5 Storage Design

**Local (default):** SQLite + JSONL

| Format | Use Case |
|--------|---------|
| SQLite `events.db` | Query, aggregate, dashboard API |
| `events.jsonl` | Easy grep, export, demo presentation |
| JPEG frames | Visual audit trail |

**Cloud (Lambda):** DynamoDB + S3

| Service | Data |
|---------|------|
| S3 | JPEG frames (camera → S3 → Rekognition) |
| DynamoDB | Structured events (EventItem schema) |
| CloudWatch Logs | Application logs |

---

## 3. Failure Modes and Mitigations

| Failure | Detection | Mitigation |
|---------|-----------|------------|
| Camera stream lost | `CaptureError` raised | Auto-reconnect with exponential backoff |
| Rekognition throttling | `ThrottlingException` | Retry with exponential backoff (3 attempts) |
| Notification failure | Exception caught per-channel | Log failure, continue other channels |
| AWS credentials missing | `NoCredentialsError` | Fast-fail at startup with clear error message |
| State machine stale | Candidate timeout | Auto-reset to IDLE after `state_timeout_seconds` |

---

## 4. Scalability Path

| Scale | Approach |
|-------|---------|
| 1 camera (current) | Single process, local SQLite |
| 2–10 cameras | Multiprocessing pool, one detector per camera |
| 10–100 cameras | Lambda per camera, DynamoDB event store |
| Enterprise | Kinesis Video Streams → Rekognition Video |

---

## 5. Cost Estimate

At 10 frames/minute (1 frame per 6 sec), 24/7:

| | Volume | Cost |
|--|--------|------|
| Rekognition API calls | 14,400/day | ~$0.014/day ($0.42/month) |
| S3 storage (frames, 30 days) | ~200MB | ~$0.005/month |
| Lambda executions (30s schedule) | 2,880/day | Free tier |
| **Total** | | **~$0.50/month** |
