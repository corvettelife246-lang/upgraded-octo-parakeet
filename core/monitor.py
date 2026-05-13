"""
Real-time system monitor — CPU, RAM, disk, GPU, network.
Streams metrics over WebSocket at configurable intervals.

GPU support:
  NVIDIA: via pynvml (pip install nvidia-ml-py3)
  AMD:    via rocm-smi (CLI fallback)
  Intel:  partial via psutil sensors
"""
import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _gpu_metrics() -> list[dict]:
    """Try NVML first, then rocm-smi, then return empty."""
    try:
        import pynvml
        pynvml.nvmlInit()
        count  = pynvml.nvmlDeviceGetCount()
        gpus   = []
        for i in range(count):
            h    = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            mem  = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            gpus.append({
                "index": i, "name": name if isinstance(name, str) else name.decode(),
                "util_pct": util.gpu,
                "mem_used_mb":  mem.used  // 1024 // 1024,
                "mem_total_mb": mem.total // 1024 // 1024,
                "mem_pct": round(mem.used / mem.total * 100, 1),
                "temp_c": temp,
            })
        return gpus
    except Exception:
        pass

    # AMD fallback via rocm-smi
    try:
        import subprocess, json as _json
        out = subprocess.check_output(["rocm-smi", "--showuse", "--showmemuse", "--json"],
                                       timeout=3, stderr=subprocess.DEVNULL)
        data = _json.loads(out)
        gpus = []
        for key, val in data.items():
            if key.startswith("card"):
                gpus.append({
                    "index": int(key.replace("card","")),
                    "name": "AMD GPU",
                    "util_pct": float(val.get("GPU use (%)", 0)),
                    "mem_pct":  float(val.get("GPU Memory Allocated (VRAM%)", 0)),
                    "mem_used_mb": None, "mem_total_mb": None, "temp_c": None,
                })
        return gpus
    except Exception:
        return []


def collect_metrics() -> dict:
    import psutil
    cpu_pct   = psutil.cpu_percent(interval=0.1)
    cpu_freq  = psutil.cpu_freq()
    cpu_count = psutil.cpu_count(logical=True)
    mem       = psutil.virtual_memory()
    swap      = psutil.swap_memory()
    disk      = psutil.disk_usage("/")
    net       = psutil.net_io_counters()

    try:
        temps = psutil.sensors_temperatures() or {}
        cpu_temp = None
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
            if key in temps and temps[key]:
                cpu_temp = temps[key][0].current
                break
    except AttributeError:
        cpu_temp = None

    return {
        "ts": time.time(),
        "cpu": {
            "pct":       cpu_pct,
            "count":     cpu_count,
            "freq_mhz":  round(cpu_freq.current, 0) if cpu_freq else None,
            "temp_c":    cpu_temp,
        },
        "ram": {
            "used_mb":  mem.used     // 1024 // 1024,
            "total_mb": mem.total    // 1024 // 1024,
            "pct":      mem.percent,
        },
        "swap": {
            "used_mb":  swap.used    // 1024 // 1024,
            "total_mb": swap.total   // 1024 // 1024,
            "pct":      swap.percent,
        },
        "disk": {
            "used_gb":  round(disk.used  / 1e9, 1),
            "total_gb": round(disk.total / 1e9, 1),
            "pct":      disk.percent,
        },
        "net": {
            "sent_mb":  round(net.bytes_sent / 1e6, 1),
            "recv_mb":  round(net.bytes_recv / 1e6, 1),
        },
        "gpu": _gpu_metrics(),
    }


async def metrics_stream(interval: float = 2.0):
    """Async generator yielding metric dicts at `interval` seconds."""
    while True:
        try:
            yield await asyncio.to_thread(collect_metrics)
        except Exception as exc:
            logger.warning("Metrics collection error: %s", exc)
        await asyncio.sleep(interval)
