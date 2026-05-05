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

## `manifest.yaml` (explicit structure)

### `services` (API workload)

| Field | Purpose |
|-------|---------|
| **`image`** | Local Docker image name/tag for the API (**must** match `docker build -t …`) |
| **`port`** | **Internal** container listen port — **not** published on the host |
| **`mode`** | **`stable`** or **`canary`** — **required**; `swiftdeploy promote` updates **`services.mode` in-place** and regenerates Compose |

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

Example (see repo file for the live copy):

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

metadata:
  version: "1.0.0"
  service_name: swiftdeploy-api
  contact: "felixgogodae777@gmail.com"
  deployed_by: "swiftdeploy"
```

---

## CLI walkthrough

### `init`

Parses YAML → writes **`./nginx.conf`** + **`./docker-compose.yml`** from [`templates/`](templates/).

### `validate` (all five must PASS)

1. **`manifest.yaml` exists** and parses as YAML (**PyYAML**)  
2. **Required fields** non-empty (includes **`services.mode`**, **`nginx.proxy_timeout`**, full **`metadata`** block)  
3. **`docker image inspect <services.image>`** succeeds  
4. **`nginx.port`** from manifest is **free** on the host (`socket.bind` probe)  
5. Rendered config passes **`nginx -t`** inside a throwaway `docker run` using **`<nginx.image>`**, config mounted at **`/etc/nginx/nginx.conf`**, and **`--add-host=api:127.0.0.1`** so the static **`upstream api:…`** resolves during the check (real Compose DNS is used at runtime).

Any failure → **non-zero exit**.

### `deploy`

Runs **`init`**, then **`docker compose up --build -d`**, then blocks up to **60s** on **`GET http://127.0.0.1:<nginx.port>/healthz`** through Nginx.

### `promote canary|stable` (exact sequence)

1. **Rewrite `manifest.yaml`** — sets **`services.mode`** to the target  
2. **`init`** — regenerate root configs  
3. **`docker compose up -d --no-deps --force-recreate api`** — **only** the API container restarts  
4. **Verify** — polls **`GET /healthz`** via Nginx until JSON **`mode`** matches the promotion target (or timeout → failure)

### `teardown [--clean]`

`docker compose down -v --remove-orphans`; **`--clean`** deletes generated **`nginx.conf`** + **`docker-compose.yml`**.

---

## Docker Compose constraints (generated)

The **`api`** service template intentionally has **no `ports:`** mapping — only **`expose`**. Published ports exist **only** on **`nginx`**.

Other items baked into the template:

- **`restart: unless-stopped`** on both services  
- **`healthcheck`** on **`api`**: `curl -f http://localhost:<services.port>/healthz` with **`interval: 10s`**, **`timeout: 2s`**, **`retries: 3`**  
- **`cap_drop: [ALL]`**, **`security_opt: no-new-privileges:true`**, **`user: "1000:1000"`** on **`api`**  
- Named volume **`api_logs`** mounted at **`/var/log/swiftdeploy`** on **`api`**

---

## API (FastAPI)

- **`GET /`** — includes **`mode`**, **`version`**, manifest-driven **`metadata`** map, timestamps  
- **`GET /healthz`** — **`status`**, **`uptime_seconds`**, **`mode`** (used by **`promote`** verification)  
- **`POST /chaos`** — **canary only** (`403` in stable). Chaos state is **process-global** (`ChaosState`). **`POST /chaos` is excluded from slow/error chaos** so it stays responsive for **`recover`**.

---

## Quick start

```bash
docker build -t swiftdeploy-hng14-api:latest .
python swiftdeploy validate
python swiftdeploy deploy
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

---

## License

MIT