# WSL-2 Audio & Video Setup Guide
## Windows 10 x64 Home

### Audio (Microphone + Speaker)

WSL-2 does not natively pass audio hardware. Use one of these approaches:

#### Option A — PulseAudio over TCP (recommended)
1. Install [PulseAudio for Windows](https://www.freedesktop.org/wiki/Software/PulseAudio/Ports/Windows/Support/)
2. Edit `%APPDATA%\pulse\default.pa` — add:
   ```
   load-module module-native-protocol-tcp auth-anonymous=1
   ```
3. Start PulseAudio on Windows, then in WSL-2:
   ```bash
   export PULSE_SERVER=tcp:$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}')
   paplay /usr/share/sounds/alsa/Front_Left.wav   # test
   ```
4. The install.sh script writes `~/.config/pulse/client.conf` automatically.

#### Option B — WSLg (Windows 11 / Windows 10 Build 22000+)
WSLg includes built-in audio/video passthrough. No extra config needed.

---

### Camera / Video

WSL-2 does not expose `/dev/video*` directly on older builds.

#### Option A — USB/IP passthrough (recommended for Windows 10)
```powershell
# In elevated PowerShell on Windows:
winget install usbipd
usbipd wsl list               # find your camera
usbipd wsl attach --busid <id>
```
Then in WSL-2:
```bash
ls /dev/video*   # should appear
```

#### Option B — Use the browser webcam (no WSL camera needed)
The web UI captures video directly from the browser via `getUserMedia`.
The camera popup and snapshot features work entirely client-side —
no `/dev/video*` needed for the browser-based pipeline.

#### Option C — Virtual camera with OBS
Install OBS on Windows + Virtual Camera plugin, then attach via USB/IP.

---

### Verifying the setup
```bash
source .venv/bin/activate
python - <<'EOF'
from voice.speech_to_text import SpeechToText
from voice.text_to_speech import TextToSpeech
from vision.camera import Camera
import asyncio

tts = TextToSpeech()
audio = asyncio.run(tts.synthesize("Hello from AI Admin"))
print(f"TTS OK — {len(audio)} bytes")

cam = Camera()
if cam.open():
    frame = cam.read_frame()
    print(f"Camera OK — frame size: {len(frame)} bytes")
    cam.close()
else:
    print("Camera not found (use browser webcam instead)")
EOF
```
