# Monitoring Stack

InfluxDB + Grafana for GPU and energy telemetry.

## Services

| Service | Port | URL |
|---------|------|-----|
| InfluxDB | 8888 | `http://192.168.254.111:8888` |
| Grafana | 3002 | `http://192.168.254.111:3002` |

## Quick Start

### 1. Fill in Emporia Vue credentials

Get your Emporia Vue local API token and serial number:

1. Open the Emporia Vue app
2. Go to your device settings
3. The **serial number** is shown on the device info screen
4. For the **token**, use one of these methods:
   - Network sniff: capture the HTTP traffic from the app to `api.emporiaenergy.com` — look for the `token` and `serial` headers
   - Or use the [emporia-local](https://github.com/danielwe/deconz-rest-plugin) community tools to extract it
5. Update `.env`:
   ```
   EMPORIA_VUE_TOKEN=your_token_here
   EMPORIA_VUE_SERIAL=your_serial_here
   ```

### 2. Start the stack

```bash
cd /mnt/data/docker
docker compose up -d monitoring
```

This starts 3 containers:
- **influxdb** — time-series database (90-day retention)
- **grafana** — dashboards, auto-provisioned with InfluxDB datasource
- **telemetry-collector** — Python script collecting GPU + Emporia metrics

### 3. Access Grafana

- URL: `http://192.168.254.111:3002`
- Login: `admin` / `GRAFANA_ADMIN_PASSWORD` (from `.env`)
- Two dashboards are auto-provisioned:
  - **NVIDIA GPUs** — utilization, temperature, power, VRAM, clocks for all 3 GPUs
  - **Emporia Energy** — whole-home power, per-circuit breakdown, voltage

## Collected Metrics

### GPU (15s interval)
- GPU & memory utilization (%)
- Temperature (°C)
- Power draw & limit (W)
- VRAM used/free/total (MB)
- Graphics & memory clock (MHz)
- Fan speed (%)

### Emporia Vue (15s interval)
- Whole-home: power (W), voltage (V), current (A), power factor
- Per-circuit: power (W), current (A)

## Retention

InfluxDB is configured with a **90-day retention policy**. Data older than 90 days is automatically purged.

## Stopping / Removing

```bash
docker compose down monitoring
```

Data persists in `./influxdb/` and `./grafana-data/` — it survives container restarts.

## Troubleshooting

### GPU metrics missing
- Check collector logs: `docker logs telemetry-collector`
- The container needs NVML — the NVIDIA driver on the host provides this via the `nvidia-container-toolkit`
- If NVML fails, the collector logs a warning but keeps running (Emporia still works)

### Emporia metrics missing
- Verify `EMPORIA_VUE_TOKEN` and `EMPORIA_VUE_SERIAL` are set in `.env`
- Verify the Vue hub is reachable: `curl http://192.168.254.25/api/realTimeData -H "token: YOUR_TOKEN" -H "serial: YOUR_SERIAL"`
- Check collector logs for connection errors

### Grafana dashboards empty
- Wait 1-2 minutes after startup for data to accumulate
- Check InfluxDB has data: `docker exec influxdb influx query --org home --bucket telemetry --query 'from(bucket:"telemetry") |> range(start:-1h) |> count()'`
