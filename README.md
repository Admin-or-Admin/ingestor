# ingestor

The first service in the CyberControl pipeline. Pulls log events from external sources and streams them into Apache Kafka so the rest of the platform can process them.

The ingestor is deliberately source-agnostic. Each log source is implemented as an independent adapter that runs in its own thread. You enable the adapters you need via a single environment variable and the core service handles the rest — threading, shutdown, and startup coordination. Adding a new source means adding one new file.

---

## Table of contents

- [How it fits in the pipeline](#how-it-fits-in-the-pipeline)
- [Adapters](#adapters)
  - [Elasticsearch adapter](#elasticsearch-adapter)
  - [Mock logs adapter](#mock-logs-adapter)
  - [GNS3 adapter](#gns3-adapter)
- [Startup coordination with Ledger](#startup-coordination-with-ledger)
- [Log event format](#log-event-format)
- [Project structure](#project-structure)
- [Setup](#setup)
- [Environment variables](#environment-variables)
- [Running the ingestor](#running-the-ingestor)
- [Adding a new adapter](#adding-a-new-adapter)

---

## How it fits in the pipeline

```
Elasticsearch ──┐
Mock Generator ──┤── ingestor ──► logs.unfiltered ──► classifier
GNS3 / Syslog ──┘
```

The ingestor is the only service that reads from external systems. Everything downstream — classifier, analyst, responder, ledger — works exclusively from Kafka topics. The ingestor's sole job is to normalise events from whatever source format they arrive in and publish them to `logs.unfiltered` in a consistent schema.

---

## Adapters

### Elasticsearch adapter

**File:** `ingestor/adapters/elasticsearch_adapter.py`  
**Enabled by:** `elasticsearch` in `ENABLED_INGESTORS`

Polls an Elasticsearch index for new documents and publishes each one to `logs.unfiltered`. This is the primary production adapter — it reads real application logs that have been shipped to Elasticsearch by whatever means (Filebeat, Logstash, direct indexing, etc.).

**How it avoids duplicates:**

On startup, before entering the polling loop, the adapter queries the Ledger via the `actions` Kafka topic for the timestamp of the last log it previously processed. The Ledger responds with that timestamp and the adapter uses it as its starting point — so if the ingestor restarts it picks up exactly where it left off rather than replaying everything from the beginning.

If the Ledger does not respond within 10 seconds (e.g. it is not running yet, or this is the very first run), the adapter defaults to the Unix epoch (`1970-01-01T00:00:00.000Z`) and processes the full index. This is safe — it just means a cold start on the first run.

**Polling behaviour:**

- Fetches up to 100 documents per poll using Elasticsearch's `search_after` cursor for efficient pagination
- If a full batch of 100 is returned it immediately fetches the next batch without waiting — this lets it catch up quickly after a backlog
- Once caught up it waits `POLL_INTERVAL` seconds (default 1.0) before polling again
- Uses `@timestamp` as the cursor field for ordering and deduplication

**What it publishes:** the raw `_source` document from each Elasticsearch hit, unchanged. Whatever fields exist in the document are passed through as-is.

---

### Mock logs adapter

**File:** `ingestor/adapters/mock_logs_adapter.py`  
**Enabled by:** `mock_logs` in `ENABLED_INGESTORS`

Generates synthetic security log events and indexes them directly into Elasticsearch. It does not publish to Kafka itself — it feeds the Elasticsearch adapter by writing to the same index that adapter is polling. This lets you run the full pipeline end-to-end without a real log source.

**Generated event types** (randomly selected):

| Message | Level | HTTP Status |
|---|---|---|
| SQL injection attempt in query | CRITICAL | 403 |
| Cross-site scripting (XSS) in parameter | CRITICAL | 403 |
| Multiple failed login attempts for user `admin` | WARNING | 401 |
| Unauthorized access attempt to `/api/v1/admin/settings` | ERROR | 403 |
| Potential brute force attack on `/login` endpoint | WARNING | 429 |

**Services** assigned randomly: `auth-service`, `payment-gateway`, `inventory-manager`, `frontend-api`, `web-proxy`

Each event is generated with a random wait of 1–2 seconds between entries, controlled by `MOCK_DELAY`. This paces the pipeline at a realistic rate for demo and testing purposes.

**Each generated document:**

```json
{
  "@timestamp": "2026-03-12T10:23:01.441Z",
  "log.level": "CRITICAL",
  "message": "SQL Injection attempt detected in query: SELECT * FROM users WHERE id = 1 OR 1=1",
  "service.name": "auth-service",
  "trace.id": "a3f2c1d4-...",
  "user.id": "john_doe",
  "http.response.status_code": 403,
  "process.pid": 4821
}
```

**Note:** The mock adapter requires Elasticsearch to be reachable. Make sure `ELASTIC_HOST` is set correctly and that Elasticsearch is running before starting the ingestor with `mock_logs` enabled. The Elasticsearch client must be initialised with `scheme: http` when running locally without TLS — see [Setup](#setup).

---

### GNS3 adapter

**File:** `ingestor/adapters/gns3_adapter.py`  
**Enabled by:** `gns3` in `ENABLED_INGESTORS`

Simulates network device log events from a GNS3 virtual lab environment. It runs two things simultaneously in separate threads: a UDP syslog listener that accepts real syslog messages from GNS3 devices, and a simulator that generates synthetic Cisco-style log events at a configurable interval.

**UDP syslog listener:**

Binds to `0.0.0.0:1514` (configurable via `SYSLOG_PORT`) and accepts raw syslog UDP datagrams from any GNS3 node configured to send syslog to the host machine. Each message is wrapped in the standard log event envelope and published directly to `logs.unfiltered`.

To configure a Cisco router in GNS3 to send to this listener:
```
R1# configure terminal
R1(config)# logging host <your-host-ip> transport udp port 1514
R1(config)# logging trap informational
```

**Simulated network event types:**

| Template | Type | Level |
|---|---|---|
| `%SEC-6-IPACCESSLOGP: list 101 denied tcp {src} -> {dst}` | security | WARNING |
| `%LINEPROTO-5-UPDOWN: Interface {iface} changed state to {state}` | infrastructure | INFO |
| `%AUTH-4-LOGIN_FAILED: Login failed for user '{user}' from {src}` | security | ERROR |
| `%SYS-5-CONFIG_I: Configured from console by {user} on vty0` | audit | INFO |

Source IPs are drawn from a mix of internal (`10.0.0.0/24`) and external addresses. Destination IPs are always internal. Device names, interfaces, and users are randomised.

**Each generated GNS3 document:**

```json
{
  "@timestamp": "2026-03-12T10:23:01.441Z",
  "log.level": "WARNING",
  "message": "%SEC-6-IPACCESSLOGP: list 101 denied tcp 192.168.1.50(39124) -> 10.0.0.22(22), 1 packet",
  "service.name": "FW-01",
  "trace.id": "b8e4a2f1-...",
  "source.ip": "192.168.1.50",
  "destination.ip": "10.0.0.22",
  "event.module": "gns3-simulator",
  "event.category": "security"
}
```

---

## Startup coordination with Ledger

The Elasticsearch adapter implements a handshake with the Ledger on startup to avoid reprocessing logs. The flow is:

1. The adapter subscribes to the `actions` Kafka topic with a fresh consumer group
2. It publishes a `get_last_unfiltered_timestamp` request message to `actions`, including a unique `requester_id`
3. It polls `actions` for up to 10 seconds waiting for a response from the Ledger where `action == "response_last_unfiltered_timestamp"` and `requester == requester_id`
4. If a matching response arrives, the adapter uses that timestamp as its starting point
5. If no response arrives within 10 seconds, it falls back to epoch and logs a warning

This means the ingestor and ledger can start in any order. If the ledger starts first the handshake completes instantly. If the ingestor starts first it will wait up to 10 seconds and then proceed independently.

The `requester_id` is unique per adapter instance per startup (`ingestor-elasticsearch-<random_hex>`), so multiple ingestor instances running simultaneously do not confuse each other's responses.

---

## Log event format

All adapters normalise their output to the same envelope before publishing to `logs.unfiltered`. The Elasticsearch adapter passes the raw document through; the mock and GNS3 adapters construct this structure directly.

| Field | Type | Description |
|---|---|---|
| `@timestamp` | string | ISO 8601 UTC timestamp with milliseconds — `2026-03-12T10:23:01.441Z` |
| `log.level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `message` | string | The human-readable log message |
| `service.name` | string | Name of the service or device that produced the log |
| `trace.id` | string | UUID v4 used as the Kafka partition key and for correlating events |
| `user.id` | string | Username associated with the event (where applicable) |
| `http.response.status_code` | int | HTTP status code (application logs only) |
| `process.pid` | int | Process ID (application logs only) |
| `source.ip` | string | Source IP address (network logs only) |
| `destination.ip` | string | Destination IP address (network logs only) |
| `event.module` | string | Adapter that produced the event (network logs only) |
| `event.category` | string | Broad event category — `security`, `infrastructure`, `audit` (network logs only) |

Fields not present in a given event type are simply omitted. The classifier handles all field combinations.

---

## Project structure

```
ingestor/
  main.py                          — Entry point: loads adapters, starts threads, handles shutdown
  adapters/
    base.py                        — Abstract BaseAdapter class all adapters must implement
    elasticsearch_adapter.py       — Reads from Elasticsearch, publishes to logs.unfiltered
    mock_logs_adapter.py           — Generates synthetic security events into Elasticsearch
    gns3_adapter.py                — Simulates Cisco network events + UDP syslog listener
  requirements.txt
  .env
```

The `shared/` directory referenced by the Elasticsearch and GNS3 adapters lives in the repository root and contains the shared `AuroraProducer`/`AuroraConsumer` Kafka client and the `AuroraElasticClient` Elasticsearch wrapper.

---

## Setup

**Prerequisites:**
- Python 3.10+
- Kafka running (via Docker Compose from the [.github repo](https://github.com/Admin-or-Admin/.github))
- Elasticsearch running at the address in `ELASTIC_HOST`

**1. Create and activate a virtual environment**
```bash
cd ingestor
python -m venv venv
source venv/bin/activate      # Mac/Linux
venv\Scripts\activate         # Windows
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Create your `.env` file**
```bash
cp .env.example .env
```
Edit `.env` with your values. See [Environment variables](#environment-variables) for all options.

**4. Fix for local Elasticsearch without TLS**

The Elasticsearch Python client v8+ defaults to HTTPS. When running locally with a plain HTTP Elasticsearch (which is the default Docker setup), you must initialise the client explicitly. In `mock_logs_adapter.py`, the constructor does this already:

```python
self.es = Elasticsearch(self.es_hosts)
```

If you see SSL connection errors, replace with:

```python
self.es = Elasticsearch(
    [{"host": "localhost", "port": 9200, "scheme": "http"}]
)
```

---

## Environment variables

All configuration is read from `.env` at startup via `python-dotenv`.

| Variable | Default | Description |
|---|---|---|
| `ENABLED_INGESTORS` | `elasticsearch,mock_logs` | Comma-separated list of adapters to start. Valid values: `elasticsearch`, `mock_logs`, `gns3`. Order does not matter — all start simultaneously in separate threads |
| `ELASTIC_HOST` | `http://localhost:9200` | Full URL of the Elasticsearch instance including scheme and port |
| `ELASTIC_INDEX` | `mock-logs` | Name of the Elasticsearch index to read from (Elasticsearch adapter) and write to (Mock adapter) |
| `KAFKA_BROKERS` | `192.168.1.6:29092` | Kafka broker address. Use `localhost:29092` if Kafka runs on the same machine. Use the LAN IP if Kafka runs in Docker on a different machine |
| `KAFKA_TOPIC` | `logs.unfiltered` | Kafka topic to publish events to. Should not need to change |
| `POLL_INTERVAL` | `1.0` | Seconds between Elasticsearch polls when no new documents are found |
| `MOCK_DELAY` | `0.5` | Base delay in seconds between mock log generation. Actual delay is randomised between 1.0 and 2.0 seconds regardless of this value |
| `SYSLOG_PORT` | `1514` | UDP port the GNS3 adapter binds to for incoming syslog messages. Use 514 if running as root; 1514 is the default for non-root |
| `SIM_INTERVAL` | `1.0` | Seconds between simulated GNS3 network events |

A complete `.env` for local development:

```properties
# Adapters to run
ENABLED_INGESTORS=elasticsearch,mock_logs

# Elasticsearch
ELASTIC_HOST=http://localhost:9200
ELASTIC_INDEX=mock-logs

# Kafka — use localhost if everything runs on your machine
KAFKA_BROKERS=localhost:29092
KAFKA_TOPIC=logs.unfiltered

# Elasticsearch polling
POLL_INTERVAL=1.0

# Mock log generation speed
MOCK_DELAY=0.5

# GNS3 (only needed if gns3 is in ENABLED_INGESTORS)
SYSLOG_PORT=1514
SIM_INTERVAL=1.0
```

---

## Running the ingestor

The ingestor must be run as a module from the parent directory of the `ingestor/` folder so that the `shared/` imports resolve correctly.

```bash
# Activate venv first
source venv/bin/activate

# Run from the directory that contains the ingestor/ folder
PYTHONPATH=. python -m ingestor.main
```

Or with a custom adapter set:

```bash
ENABLED_INGESTORS=mock_logs PYTHONPATH=. python -m ingestor.main
ENABLED_INGESTORS=gns3 PYTHONPATH=. python -m ingestor.main
ENABLED_INGESTORS=elasticsearch,mock_logs,gns3 PYTHONPATH=. python -m ingestor.main
```

**Expected startup output:**

```
--- Aurora Ingestor Service ---
Enabled adapters: elasticsearch, mock_logs
All 2 ingestors are running.
Ingestor: Starting Elasticsearch adapter...
  [Elasticsearch] Connecting to 'actions' topic...
  [Elasticsearch] Requesting last timestamp from Ledger (req_id: ingestor-elasticsearch-3a8f2b1c)...
  [Elasticsearch] Received last timestamp from Ledger: 2026-03-12T09:44:12.000Z
  [Elasticsearch] Starting from: 2026-03-12T09:44:12.000Z
Ingestor: Starting MockLogs adapter...
  [Elasticsearch] Published 14 logs.
  [Elasticsearch] Published 8 logs.
```

**Shutdown:** press `Ctrl+C`. The service handles `SIGINT` and `SIGTERM` gracefully and exits cleanly.

---

## Adding a new adapter

1. Create a new file in `ingestor/adapters/`, e.g. `splunk_adapter.py`
2. Subclass `BaseAdapter` and implement `run()`

```python
from .base import BaseAdapter
from shared.kafka_client import AuroraProducer
import os, time

class SplunkAdapter(BaseAdapter):
    def __init__(self, name="Splunk"):
        super().__init__(name)
        self.kafka_brokers = [os.getenv("KAFKA_BROKERS", "localhost:29092")]
        self.kafka_topic   = os.getenv("KAFKA_TOPIC", "logs.unfiltered")

    def run(self):
        print(f"Ingestor: Starting {self.name} adapter...")
        producer = AuroraProducer(self.kafka_brokers)
        producer.ensure_topic(self.kafka_topic)

        while True:
            # fetch events from Splunk here
            events = fetch_from_splunk()
            for event in events:
                producer.send_log(self.kafka_topic, event)
            producer.flush()
            time.sleep(5)
```

3. Register it in `main.py`:

```python
from ingestor.adapters.splunk_adapter import SplunkAdapter

ADAPTER_MAP = {
    "elasticsearch": ElasticsearchAdapter,
    "gns3":          GNS3Adapter,
    "mock_logs":     MockLogsAdapter,
    "splunk":        SplunkAdapter,   # add this
}
```

4. Enable it: `ENABLED_INGESTORS=splunk` in `.env`

The new adapter will start in its own daemon thread alongside any other enabled adapters. No other files need to change.

The only contract the adapter must meet is that every document it publishes to `logs.unfiltered` includes at minimum `@timestamp`, `message`, and `service.name`. All other fields are optional and the classifier handles their presence or absence.

---

## Related repos

| Repo | Description |
|---|---|
| [rac-agents](https://github.com/Admin-or-Admin/rac-agents) | Classifier, analyst, and responder — consume from `logs.unfiltered` |
| [ledger](https://github.com/Admin-or-Admin/ledger) | Persists all pipeline events to PostgreSQL — responds to timestamp requests |
| [.github](https://github.com/Admin-or-Admin/.github) | Docker Compose for Kafka, Elasticsearch, Kibana, and PostgreSQL |
