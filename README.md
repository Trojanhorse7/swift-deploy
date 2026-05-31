# SwiftDeploy

[`manifest.yaml`](manifest.yaml) is the **only** hand-maintained deploy contract. [`swiftdeploy`](swiftdeploy) renders **`nginx.conf`** and **`docker-compose.yml` in the repository root** (no extra folders). The FastAPI app reads **`MODE`** from the manifest via Compose; **only Nginx** publishes a port on the host.

**Naming clarity:** the **`Dockerfile` sits at the repo root** because the brief requires that layout. The **container process is not root**: the API image ends with **`USER 1000:1000`** so Uvicorn runs as an unprivileged user.

---

## Prerequisites

- Docker Engine + **Compose V2** (`docker compose`)
- Python **3.10+**

```bash
python -m pip install -r requirements-cli.txt
chmod +x swiftdeploy   # Unix/macOS; on Windows use `python swiftdeploy`
```

---

## `manifest.yaml`

### `services` (API workload)

| Field | Purpose |
|-------|---------|
| **`image`** | Local Docker image name/tag for the API (**must** match `docker build -t …`) |
| **`port`** | **Internal** container listen port — **not** published on the host |
| **`mode`** | **`stable`** or **`canary`** — **required**; `swiftdeploy promote` updates **`services.mode` in-place** and regenerates Compose. The committed [`manifest.yaml`](manifest.yaml) reflects the last promote target. |

### `nginx` (reverse proxy)

| Field | Purpose |
|-------|---------|
| **`image`** | Upstream Nginx image (pinned tag recommended) |
| **`port`** | Host-facing HTTP port (published by Compose on **`nginx` only**) |
| **`proxy_timeout`** | Value applied to **`proxy_connect_timeout`**, **`proxy_send_timeout`**, **`proxy_read_timeout`** in generated [`templates/nginx.conf.j2`](templates/nginx.conf.j2) |

Generated Nginx also includes:

- **`add_header X-Deployed-By "`**`metadata.deployed_by`**`"`** on every response
- **`proxy_pass_header X-Mode`** so clients see upstream canary header  
- **`error_page` `502`/`503`/`504`** returning JSON bodies **`{ error, code, service, contact }`** using **`metadata.service_name`** and **`metadata.contact`**  
- Access log format: **`$time_iso8601 | $status | ${request_time}s | $upstream_addr | $request`** (written to **stdout** so `docker compose logs nginx` works under **`user: nginx`**)
- Temp paths (**`client_body_temp_path`**, **`proxy_temp_path`**, etc.) under **`/tmp`** so **`user: nginx`** can write (default **`/var/cache/nginx`** is root-owned)

### `network`

| Field | Purpose |
|-------|---------|
| **`name`** | Docker network name |
| **`driver_type`** | e.g. `bridge` |

### `metadata` (required)

All of these are **validated** by `swiftdeploy validate`:

| Field | Used for |
|-------|-----------|
| **`version`** | Shipped as **`APP_VERSION`** if top-level `app_version` is omitted; surfaced on **`GET /`** as `metadata.version` |
| **`service_name`** | Nginx JSON error payloads (`service` key) |
| **`contact`** | Nginx JSON error payloads (`contact` key) |
| **`deployed_by`** | Nginx **`X-Deployed-By`** response header |

Optional: top-level **`app_version`** overrides the Compose **`APP_VERSION`** env when you want it different from `metadata.version`.

### Other keys

- **`compose_project`** — Docker Compose project name (`docker compose -p`)

### `policy` (OPA + Prometheus thresholds)

Thresholds are **only** defined here (see [`policies/infrastructure/policy.rego`](policies/infrastructure/policy.rego) and [`policies/canary/policy.rego`](policies/canary/policy.rego)); Rego compares **`input.thresholds`** / **`input.host`** / **`input.metrics`**.

| Block | Purpose |
|-------|---------|
| **`thresholds.min_disk_free_gb`** | Pre-deploy gate: host disk headroom |
| **`thresholds.min_mem_available_gb`** | Pre-deploy gate: host RAM available (**`/proc`-style** via psutil `available`) |
| **`thresholds.max_cpu_load`** | Pre-deploy gate: 1-minute load (Unix) or CPU-derived estimate (Windows) |
| **`thresholds.max_error_rate_percent`** | Pre-promote to **canary**: rolling-window error rate from Prometheus gauges |
| **`thresholds.max_p99_latency_ms`** | Pre-promote to **canary**: rolling-window P99 latency |
| **`thresholds.metrics_window_seconds`** | Must match API **`METRICS_WINDOW_SECONDS`** (Compose passes this env) |
| **`opa.image`** | Sidecar image for `opa run --server /policies` |
| **`opa.host_port`** | Published as **`127.0.0.1:<port>:8181`** only (local policy queries) |

OPA listens on **`http://127.0.0.1:<policy.opa.host_port>`** (maps container **8181**). Prometheus text format for the app is exposed at **`GET http://127.0.0.1:<nginx.port>/metrics`** (proxied through Nginx to the API).

Example (see repo [`manifest.yaml`](manifest.yaml) for the live copy):

```yaml
services:
  image: swiftdeploy-hng14-api:latest
  port: 3000
  mode: stable

nginx:
  image: nginx:1.27-alpine
  port: 8080
  proxy_timeout: 60s

network:
  name: swiftdeploy-net
  driver_type: bridge

app_version: "1.0.0"

metadata:
  version: "1.0.0"
  service_name: swiftdeploy-api
  contact: "felixgogodae777@gmail.com"
  deployed_by: "swiftdeploy"

compose_project: swiftdeploy

policy:
  thresholds:
    min_disk_free_gb: 10
    min_mem_available_gb: 1
    max_cpu_load: 2.0
    max_error_rate_percent: 1
    max_p99_latency_ms: 500
    metrics_window_seconds: 30
  opa:
    image: openpolicyagent/opa:0.69.0
    host_port: 9182
```

Blog write-up:

---

## CLI walkthrough

### `init`

Parses YAML → writes **`./nginx.conf`** + **`./docker-compose.yml`** from [`templates/`](templates/).

### `validate` (all five must PASS)

1. **`manifest.yaml` exists** and parses as YAML (**PyYAML**)  
2. **Required fields** non-empty (includes **`services.mode`**, **`nginx.proxy_timeout`**, full **`metadata`** block, and **`policy.thresholds`** / **`policy.opa`**)  
3. **`docker image inspect <services.image>`** succeeds  
4. **`nginx.port`** from manifest is **free** on the host (`socket.bind` probe)  
5. Rendered config passes **`nginx -t`** inside a throwaway `docker run` using **`<nginx.image>`**, config mounted at **`/etc/nginx/nginx.conf`**, and **`--add-host=api:127.0.0.1`** so the static **`upstream api:…`** resolves during the check (real Compose DNS is used at runtime).

Any failure → **non-zero exit**.

### `deploy`

Runs **`init`**, starts the **`opa`** service (**`docker compose up -d opa`**) and waits for OPA health, runs **pre-deploy** policy against **`swiftdeploy/infrastructure/decision`** (host disk + load vs **`manifest.policy.thresholds`**). On **DENY**, prints reasons and exits **without** bringing up the rest of the stack. On **ALLOW**, runs **`docker compose up --build -d`**, then blocks up to **60s** on **`GET http://127.0.0.1:<nginx.port>/healthz`** through Nginx.

### `promote canary|stable` (exact sequence)

1. **Pre-promote policy** — **`swiftdeploy/canary/decision`**. Promoting **to stable** skips SLO checks (policy allows). Promoting **to canary** scrapes **`/metrics`**, derives rolling-window error rate + P99 from **`swiftdeploy_window_*`** gauges, and compares against thresholds; **DENY** exits before **`manifest.yaml`** is modified.  
2. **Rewrite `manifest.yaml`** — sets **`services.mode`** to the target (skipped if already set)  
3. **`init`** — regenerate root configs  
4. **`docker compose up -d --no-deps --force-recreate api`** — **only** the API container restarts  
5. **Verify** — polls **`GET /healthz`** via Nginx until JSON **`mode`** matches the promotion target (or timeout → failure)

### `status [--interval SEC] [-n N]`

Live dashboard: **`/healthz`**, **`/metrics`** (approximate **req/s** from counter deltas, window **err%** / **P99**), **`chaos_active`** from metrics when present, **per-rule policy compliance** from OPA **`decision.checks`**, plus aggregate ALLOW/DENY. Each sample is appended as one JSON line to **`history.jsonl`** (gitignored).

### `audit`

Reads **`history.jsonl`** and writes **`audit_report.md`** (GFM summary, **timeline events** for mode/chaos transitions, violation table, recent samples including chaos).

### `teardown [--clean]`

`docker compose down -v --remove-orphans`; **`--clean`** deletes generated **`nginx.conf`** + **`docker-compose.yml`**.

---

## Docker Compose constraints (generated)

The **`api`** service template intentionally has **no `ports:`** mapping — only **`expose`**. The host-facing HTTP port is published on **`nginx`**; **`opa`** binds **`127.0.0.1:<policy.opa.host_port>`** only (localhost policy queries).

Other items baked into the template:

- **`opa`** service — **`opa run --server /policies`** with repo **`./policies`** mounted read-only; port **`127.0.0.1:<policy.opa.host_port>:8181`**  
- **`restart: unless-stopped`** on **`api`**, **`nginx`**, and **`opa`**  
- **`healthcheck`** on **`api`**: `curl -f http://localhost:<services.port>/healthz` with **`interval: 10s`**, **`timeout: 2s`**, **`retries: 3`**  
- **`cap_drop: [ALL]`**, **`security_opt: no-new-privileges:true`**, **`user: "1000:1000"`** on **`api`**  
- Named volume **`api_logs`** mounted at **`/var/log/swiftdeploy`** on **`api`**

---

## API (FastAPI)

- **`GET /`** — includes **`mode`**, **`version`**, manifest-driven **`metadata`** map, timestamps  
- **`GET /healthz`** — **`status`**, **`uptime_seconds`**, **`mode`** (used by **`promote`** verification)  
- **`GET /metrics`** — Prometheus exposition format (**`http_requests_total`**, **`http_request_duration_seconds`**, rolling-window **`swiftdeploy_window_*`** gauges for canary policy inputs)  
- **`POST /chaos`** — **canary only** (`403` in stable). Chaos state is **process-global** (`ChaosState`). **`POST /chaos` is excluded from slow/error chaos** so it stays responsive for **`recover`**.

---

## Quick start

```bash
docker build -t swiftdeploy-hng14-api:latest .
python swiftdeploy validate
python swiftdeploy deploy
python swiftdeploy status --interval 2 -n 5   # optional: five samples then exit
python swiftdeploy audit                      # writes ./audit_report.md
python swiftdeploy promote canary
python swiftdeploy promote stable
python swiftdeploy teardown --clean
```

---

## Screenshots (Google Drive)

**Folder:** [Swift Deploy screenshots](https://drive.google.com/drive/folders/1SKQYKb_e_IFUSYO4WLsSKVNBB4KIilgH)

The Drive folder includes:

- **`validate`** output  
- **`deploy`** output  
- **`promote`** (canary / stable) and **`/healthz`** confirmation  
- Generated **`nginx.conf`** and **`docker-compose.yml`**  
- Nginx **access** logs  

---
## Notes

- **`swiftdeploy promote`** rewrites **`manifest.yaml`** with **`yaml.safe_dump`** — **YAML comments and key order may change**; values stay correct.
- **`history.jsonl`** and **`audit_report.md`** are gitignored local artifacts from **`status`** / **`audit`**.

---

## License

MIT