#!/usr/bin/env python3
"""
Telemetry collector — pushes GPU and Emporia Vue metrics to InfluxDB.

Environment variables:
  INFLUXDB_URL          — InfluxDB HTTP endpoint (default: http://influxdb:8086)
  INFLUXDB_ORG          — InfluxDB org name
  INFLUXDB_BUCKET       — InfluxDB bucket name
  INFLUXDB_TOKEN        — InfluxDB auth token
  EMPORIA_VUE_IP        — Emporia Vue hub IP address
  EMPORIA_VUE_TOKEN     — Emporia Vue local API token
  EMPORIA_VUE_SERIAL    — Emporia Vue serial number
  GPU_POLL_INTERVAL     — Seconds between GPU samples (default: 15)
  EMPORIA_POLL_INTERVAL — Seconds between Emporia samples (default: 15)
"""

import os
import sys
import time
import threading
import logging
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("collector")

# ── InfluxDB ──────────────────────────────────────────────────────────────────

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "home")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "telemetry")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")

client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = client.write_api(write_options=SYNCHRONOUS)

# ── GPU (NVML) ────────────────────────────────────────────────────────────────

NVML_AVAILABLE = False
try:
    import pynvml

    pynvml.nvmlInit()
    NVML_AVAILABLE = True
    log.info("NVML initialized successfully")
except Exception as e:
    log.warning("NVML unavailable: %s — GPU collection disabled", e)

GPU_INTERVAL = int(os.environ.get("GPU_POLL_INTERVAL", "15"))


def collect_gpu_metrics() -> list[Point]:
    """Query all GPUs via NVML and return InfluxDB Points."""
    if not NVML_AVAILABLE:
        return []

    points = []
    device_count = pynvml.nvmlDeviceGetCount()

    for i in range(device_count):
        handle = pynvml.nvmlDeviceGetHandleByIndex(i)
        try:
            name = pynvml.nvmlDeviceGetName(handle).replace(" ", "_")
            uuid = pynvml.nvmlDeviceGetUUID(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temp = pynvml.nvmlDeviceGetTemperature(
                handle, pynvml.NVML_TEMPERATURE_GPU
            )
            power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
            power_limit = pynvml.nvmlDeviceGetPowerManagementLimit(handle) / 1000.0
            clock_gr = pynvml.nvmlDeviceGetClockInfo(
                handle, pynvml.NVML_CLOCK_GRAPHICS
            )
            clock_mem = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)

            # Fan speed (may raise on some GPUs)
            try:
                fan = pynvml.nvmlDeviceGetFanSpeed(handle)
            except Exception:
                fan = None

            point = (
                Point("gpu")
                .tag("gpu_index", str(i))
                .tag("gpu_name", name)
                .tag("gpu_uuid", uuid)
                .field("utilization_gpu", util.gpu)
                .field("utilization_memory", util.memory)
                .field("memory_used_mb", mem.used // (1024 * 1024))
                .field("memory_free_mb", mem.free // (1024 * 1024))
                .field("memory_total_mb", mem.total // (1024 * 1024))
                .field("temperature_c", temp)
                .field("power_w", round(power, 2))
                .field("power_limit_w", round(power_limit, 2))
                .field("clock_graphics_mhz", clock_gr)
                .field("clock_memory_mhz", clock_mem)
            )
            if fan is not None:
                point = point.field("fan_percent", fan)

            points.append(point)
        except Exception as e:
            log.error("GPU %d (%s) error: %s", i, name, e)

    return points


# ── Emporia Vue ───────────────────────────────────────────────────────────────

EMPORIA_IP = os.environ.get("EMPORIA_VUE_IP", "")
EMPORIA_TOKEN = os.environ.get("EMPORIA_VUE_TOKEN", "")
EMPORIA_SERIAL = os.environ.get("EMPORIA_VUE_SERIAL", "")
EMPORIA_INTERVAL = int(os.environ.get("EMPORIA_POLL_INTERVAL", "15"))

EMPORIA_ENABLED = bool(EMPORIA_IP and EMPORIA_TOKEN and EMPORIA_SERIAL)

if EMPORIA_ENABLED:
    import requests

    EMPORIA_SESSION = requests.Session()
    EMPORIA_SESSION.headers.update({
        "token": EMPORIA_TOKEN,
        "serial": EMPORIA_SERIAL,
    })
    EMPORIA_BASE = f"http://{EMPORIA_IP}/api/realTimeData"
    log.info("Emporia Vue target: %s", EMPORIA_IP)
else:
    log.info(
        "Emporia Vue not configured (set EMPORIA_VUE_IP, EMPORIA_VUE_TOKEN, EMPORIA_VUE_SERIAL)"
    )


def collect_emporia_metrics() -> list[Point]:
    """Poll Emporia Vue local API and return InfluxDB Points."""
    if not EMPORIA_ENABLED:
        return []

    try:
        resp = EMPORIA_SESSION.get(EMPORIA_BASE, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 200:
            log.warning("Emporia API returned code %s", data.get("code"))
            return []

        payload = data["climates"]
        points = []

        # Whole-home summary
        point_summary = Point("energy").tag("circuit", "total")
        point_summary.field("power_w", payload.get("w", 0))
        point_summary.field("voltage_v", payload.get("v", 0))
        point_summary.field("current_a", payload.get("a", 0))
        point_summary.field("power_factor", payload.get("pf", 0))
        points.append(point_summary)

        # Per-circuit breakdown
        circuits = data.get("channels", [])
        for ch in circuits:
            ch_name = ch.get("name", f"circuit_{ch.get('id', '?')}").replace(" ", "_")
            ch_power = ch.get("w", 0)
            ch_current = ch.get("a", 0)

            pt = Point("energy").tag("circuit", ch_name)
            pt.field("power_w", ch_power)
            pt.field("current_a", ch_current)
            points.append(pt)

        return points

    except requests.RequestException as e:
        log.error("Emporia request failed: %s", e)
        return []
    except Exception as e:
        log.error("Emporia parse error: %s", e)
        return []


# ── Collection loops ──────────────────────────────────────────────────────────

def gpu_loop():
    """Continuously collect GPU metrics in a background thread."""
    log.info("GPU collection loop started (interval=%ds)", GPU_INTERVAL)
    while True:
        try:
            points = collect_gpu_metrics()
            if points:
                write_api.write(INFLUX_BUCKET, INFLUX_ORG, points)
                # Just log the count of points written
                log.info("Wrote %d GPU points to InfluxDB", len(points))
        except Exception as e:
            log.error("GPU collection error: %s", e)
        time.sleep(GPU_INTERVAL)


def emporia_loop():
    """Continuously collect Emporia Vue metrics in a background thread."""
    if not EMPORIA_ENABLED:
        log.info("Emporia collection skipped (not configured)")
        return
    log.info("Emporia collection loop started (interval=%ds)", EMPORIA_INTERVAL)
    while True:
        try:
            points = collect_emporia_metrics()
            if points:
                write_api.write(INFLUX_BUCKET, INFLUX_ORG, points)
                log.debug("Wrote %d energy points", len(points))
        except Exception as e:
            log.error("Emporia collection error: %s", e)
        time.sleep(EMPORIA_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Starting telemetry collector")

    # GPU thread
    gpu_thread = threading.Thread(target=gpu_loop, daemon=True, name="gpu-collector")
    gpu_thread.start()

    # Emporia thread
    emporia_thread = threading.Thread(
        target=emporia_loop, daemon=True, name="emporia-collector"
    )
    emporia_thread.start()

    # Main thread keeps the process alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down collector")
    finally:
        if NVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
        client.close()


if __name__ == "__main__":
    main()
