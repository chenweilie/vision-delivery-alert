# API Flow — Sequence Diagrams
## Amazon Rekognition Delivery Monitor

---

## 1. Normal Detection Flow (No Delivery)

```
Camera      Capture     Rekognition    Detector    Logger
  │            │              │             │          │
  │──frame──▶ │              │             │          │
  │           │──bytes──────▶│             │          │
  │           │              │──labels────▶│          │
  │           │              │             │──idle────▶│
  │           │              │             │          │ log(delivery=false)
  │           │              │             │          │
  [wait 5s]
```

---

## 2. Delivery Confirmation Flow (Person → Package → Alert)

```
Camera     Capture   Rekognition   Detector  Notifier   Logger
  │           │            │           │         │         │
  │──frame──▶│            │           │         │         │
  │          │──bytes────▶│           │         │         │
  │          │            │──Person──▶│         │         │
  │          │            │           │─CAND.──▶│         │
  │          │            │           │         │ log(state=CANDIDATE)
  │          │            │           │         │         │
  [5s later]
  │──frame──▶│            │           │         │         │
  │          │──bytes────▶│           │         │         │
  │          │            │──Person──▶│         │         │
  │          │            │──Package──│         │         │
  │          │            │           │─frame+1▶│         │
  │          │            │           │         │         │
  [5s later — CONFIRMATION FRAME]
  │──frame──▶│            │           │         │         │
  │          │──bytes────▶│           │         │         │
  │          │            │──Person──▶│         │         │
  │          │            │──Package──│         │         │
  │          │            │           │CONFIRMED│         │
  │          │            │           │─event──▶│         │
  │          │            │           │         │─email──▶│
  │          │            │           │         │─slack──▶│
  │          │            │           │         │         │ log(delivery=true, alert=true)
  │          │            │           │─COOLDOWN│         │
```

---

## 3. AWS Rekognition API Call Detail

### Request
```
POST https://rekognition.us-east-1.amazonaws.com/
Authorization: AWS4-HMAC-SHA256 ...
X-Amz-Target: RekognitionService.DetectLabels

{
  "Image": {
    "Bytes": "<base64-encoded-jpeg>"
  },
  "MaxLabels": 20,
  "MinConfidence": 70.0
}
```

### Response
```json
{
  "Labels": [
    {
      "Name": "Person",
      "Confidence": 92.4,
      "Instances": [
        {
          "BoundingBox": {
            "Width": 0.18, "Height": 0.72,
            "Left": 0.41, "Top": 0.15
          },
          "Confidence": 92.4
        }
      ],
      "Parents": []
    },
    {
      "Name": "Package",
      "Confidence": 87.1,
      "Instances": [],
      "Parents": []
    }
  ],
  "LabelModelVersion": "3.0",
  "ResponseMetadata": { "HTTPStatusCode": 200 }
}
```

---

## 4. Slack Webhook Payload

```json
{
  "text": "📦 *Delivery Detected!*",
  "attachments": [
    {
      "color": "#FF9900",
      "blocks": [
        {
          "type": "section",
          "text": {
            "type": "mrkdwn",
            "text": "*📦 Package Delivery Detected*\n*Time:* 2024-01-15 14:30:01 UTC\n*Person:* 92.4%\n*Package:* 87.1%"
          }
        }
      ]
    }
  ]
}
```

---

## 5. Lambda Invocation (EventBridge Schedule)

```
EventBridge (every 5s)
        │
        ▼
  Lambda Function
  handler.lambda_handler(event, context)
        │
        ├── event.get("bucket") → S3 bucket name
        ├── event.get("key")    → "live/latest.jpg"
        │
        ▼
  RekognitionClient.detect_labels_from_s3(bucket, key)
        │
        ▼
  DeliveryDetector.process_frame(result, frame_path)
  (detector is module-level — persists across warm invocations)
        │
        ▼
  [if delivery_event] → NotificationService.send_alert(event)
        │
        ▼
  Return JSON response to EventBridge
```

---

## 6. Email Alert Payload

```
From: monitor@yourdomain.com
To: you@email.com
Subject: 📦 Delivery Detected — 2024-01-15 14:30:01 UTC

[HTML Email Body]
- Timestamp
- Person confidence
- Package confidence
- Detected labels
- Attached: frame.jpg (captured JPEG)
```
