# Microsoft Foundry Local — Setup & Integration Guide
## Run the AI Multi-Agent Admin fully offline on local GPU

Foundry Local lets you run AI models (Phi-4, Llama 3, Mistral, Qwen, etc.)
directly on your Windows 10 x64 Home GPU/CPU — no internet, no API key.

---

## Step 1 — Install Foundry Local on Windows

Open **PowerShell as Administrator**:

```powershell
# Install via winget (requires Windows 10 build 17763+)
winget install Microsoft.FoundryLocal

# Verify installation
foundry --version
```

> Foundry Local requires **Windows 10 x64 Home** with either:
> - NVIDIA GPU (CUDA via DirectML)
> - AMD GPU (ROCm via DirectML)
> - Intel Arc GPU (DirectML)
> - CPU fallback (slower, but works)

---

## Step 2 — Download a Model

```powershell
# See all available models
foundry model list

# Download recommended models (pick one or more):
foundry model pull phi-4-mini          # 3.8B — fast, great for coding + chat
foundry model pull phi-4               # 14B — best quality, needs 16GB RAM
foundry model pull llama-3.2-3b        # 3B — good general purpose
foundry model pull llama-3.1-8b        # 8B — excellent for code
foundry model pull mistral-7b          # 7B — solid reasoning
foundry model pull qwen2.5-7b          # 7B — strong multilingual + code
foundry model pull phi-3.5-mini        # 3.8B — smaller, very fast
```

---

## Step 3 — Start the Foundry Local Service

```powershell
# Start the service (exposes OpenAI-compatible API on port 5273)
foundry service start

# Check it's running
foundry service status

# You should see:
#   Service: Running
#   URL: http://localhost:5273
#   Model: phi-4-mini (or whichever you loaded)
```

To load a specific model:
```powershell
foundry model run phi-4-mini
# This starts the service AND loads the model
```

Test the API from PowerShell:
```powershell
Invoke-RestMethod -Uri "http://localhost:5273/v1/models" -Method GET
```

---

## Step 4 — Configure the AI Admin Platform (WSL-2)

In your WSL-2 terminal, edit the `.env` file:

```bash
cd ~/upgraded-octo-parakeet
nano .env
```

Add / change these lines:

```env
# ── Switch to Foundry Local backend ──
LLM_BACKEND=foundry

# Foundry Local URL — auto-detected from WSL-2 nameserver, or set manually:
# FOUNDRY_LOCAL_HOST=172.28.80.1      # your Windows host IP from WSL-2
# FOUNDRY_LOCAL_URL=http://172.28.80.1:5273

# Which Foundry Local model to use:
FOUNDRY_LOCAL_MODEL=phi-4-mini

# No ANTHROPIC_API_KEY needed when using Foundry Local!
```

To find your Windows host IP from WSL-2:
```bash
cat /etc/resolv.conf | grep nameserver
# e.g.  nameserver 172.28.80.1
```

The platform auto-detects this IP — you usually don't need to set it manually.

---

## Step 5 — Start the AI Admin Platform

```bash
cd ~/upgraded-octo-parakeet
source .venv/bin/activate
python main.py
```

Then open your Windows browser: **http://localhost:8000**

Check the backend status: **http://localhost:8000/api/backend**

```json
{
  "backend": "foundry_local",
  "url": "http://172.28.80.1:5273",
  "active_model": "phi-4-mini",
  "available_models": ["phi-4-mini", "phi-4", "llama-3.1-8b"],
  "status": "ok"
}
```

---

## Available Models Reference

| Model | Size | Best For | VRAM |
|-------|------|----------|------|
| `phi-4-mini` | 3.8B | Coding, chat, fast responses | 4 GB |
| `phi-4` | 14B | Complex reasoning, best quality | 10 GB |
| `phi-3.5-mini` | 3.8B | Ultra-fast, lightweight tasks | 3 GB |
| `llama-3.2-3b` | 3B | General purpose, fast | 3 GB |
| `llama-3.1-8b` | 8B | Code, reasoning, strong | 6 GB |
| `mistral-7b` | 7B | Instruction following | 5 GB |
| `qwen2.5-7b` | 7B | Code + multilingual | 5 GB |

---

## Offline Project Building

With Foundry Local running, the entire platform works **100% offline**:

| Component | Offline? | Notes |
|-----------|----------|-------|
| LLM Chat | ✅ Yes | Foundry Local on local GPU |
| Voice STT | ✅ Yes | Whisper runs locally |
| Voice TTS | ✅ Yes | Use `TTS_ENGINE=pyttsx3` for fully offline TTS |
| Vision | ✅ Yes | OpenCV + Foundry Local vision models |
| Code Execution | ✅ Yes | Python sandbox, no network |
| Agent Routing | ✅ Yes | All in-process |

For fully offline TTS, set in `.env`:
```env
TTS_ENGINE=pyttsx3    # offline, uses Windows SAPI voices
# or
TTS_ENGINE=edge       # requires internet (Azure neural voices)
```

---

## Switching Between Backends

```bash
# Use Foundry Local (offline)
LLM_BACKEND=foundry python main.py

# Use Anthropic Claude API (online)
LLM_BACKEND=anthropic python main.py
```

The platform automatically falls back to Anthropic if Foundry Local
is unreachable (service not running, model not loaded, etc.).

---

## Troubleshooting

**"Connection refused" on port 5273**
```powershell
# Windows — restart the service
foundry service stop
foundry model run phi-4-mini
```

**Model not found**
```powershell
foundry model list     # see what's downloaded
foundry model pull phi-4-mini
```

**Slow inference**
- Ensure DirectML is using your GPU: `foundry service status --verbose`
- For NVIDIA, install CUDA drivers and ensure DirectML picks the GPU
- Use a smaller model (phi-3.5-mini or llama-3.2-3b) for speed

**WSL-2 can't reach Windows port 5273**
```bash
# Check Windows Firewall — allow port 5273 inbound
# In PowerShell (Admin):
netsh advfirewall firewall add rule name="Foundry Local WSL" ^
  dir=in action=allow protocol=TCP localport=5273
```
