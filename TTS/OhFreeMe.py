import base64
import json
import random
import time
from pathlib import Path

import requests

from utils import settings
from utils.console import print_substep

API_URL = "https://tts.ohfree.me/api/tts"
# JWT token for authentication (replace if needed)
VOICES_FILE = Path(__file__).resolve().parent.parent / "config" / "ohfreeme_voices.json"
MAX_RETRIES = 3
RATE_LIMIT_WAIT = 10


def _load_voices() -> list[dict]:
    # existing function unchanged

    # existing function unchanged

    try:
        with open(VOICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print_substep(f"Warning: Could not load voices from {VOICES_FILE}: {e}", style="yellow")
        return []


class OhFreeMe:
    # Load list of User‑Agent strings for random header
    _user_agents = None

    def _load_user_agents(self) -> list[str]:
        """Read user_agents.json and cache result.
        Returns empty list on failure and logs warning.
        """
        if self._user_agents is not None:
            return self._user_agents
        try:
            agents_path = Path(__file__).resolve().parent.parent / "config" / "user_agents.json"
            with open(agents_path, "r", encoding="utf-8") as f:
                self._user_agents = json.load(f)
        except Exception as e:
            print_substep(f"Warning: Could not load user agents: {e}", style="yellow")
            self._user_agents = []
        return self._user_agents

    def _pick_user_agent(self) -> str:
        """Return a random User‑Agent string from loaded list.
        Falls back to generic UA on empty list.
        """
        agents = self._load_user_agents()
        if agents:
            return random.choice(agents)
        return "Mozilla/5.0"

    def __init__(self):
        self.max_chars = 2500
        self.voices = _load_voices()

    def run(self, text, filepath, random_voice: bool = False):
        voice = self._pick_voice(random_voice)
        audio_bytes = self._call_api(text, voice["id"])
        with open(filepath, "wb") as f:
            f.write(audio_bytes)

    def randomvoice(self) -> dict:
        lang = settings.config["settings"]["tts"].get("ohfreeme_lang", "vi")
        filtered = [v for v in self.voices if v["lang"] == lang]
        if not filtered:
            filtered = self.voices
        return random.choice(filtered)

    def _pick_voice(self, random_voice: bool) -> dict:
        if random_voice:
            return self.randomvoice()
        lang = settings.config["settings"]["tts"].get("ohfreeme_lang", "vi")
        gender = settings.config["settings"]["tts"].get("ohfreeme_gender", "random")
        candidates = [v for v in self.voices if v["lang"] == lang]
        if gender != "random":
            candidates = [v for v in candidates if v["gender"] == gender]
        if not candidates:
            candidates = self.voices
        return random.choice(candidates)

    def _call_api(self, text: str, voice_id: int) -> bytes:
        payload = {
            "text": text,
            "id": voice_id,
            "useEnhance": settings.config["settings"]["tts"].get("ohfreeme_enhance", False),
            "rate": settings.config["settings"]["tts"].get("ohfreeme_rate", 1),
            "pitch": settings.config["settings"]["tts"].get("ohfreeme_pitch", 0),
        }
        headers = {
            "accept": "*/*",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "origin": "https://tts.ohfree.me",
            "cookie": f"auth_token={JWT_TOKEN}",
            "referer": "https://tts.ohfree.me/",
            "user-agent": self._pick_user_agent(),
        }

        # streaming NDJSON response with debug logging
        raw_response = b""
        for attempt in range(MAX_RETRIES):
            resp = requests.post(API_URL, json=payload, headers=headers, stream=True)
            # Rate‑limit handling – first line may contain error object
            try:
                first_line = next(resp.iter_lines())
                # first_line is bytes; decode for JSON parsing
                parsed = json.loads(first_line.decode('utf-8'))
                # debug: show parsed first line
                print_substep(f"[OhFreeMe debug] First line parsed: {parsed}", style="blue")
                if parsed.get("status") == "error" and "Too many requests" in parsed.get("message", ""):
                    print_substep(
                        f"  Rate limited, waiting {RATE_LIMIT_WAIT}s... (attempt {attempt + 1}/{MAX_RETRIES})",
                        style="yellow",
                    )
                    time.sleep(RATE_LIMIT_WAIT)
                    continue
                raw_response += first_line
            except (StopIteration, json.JSONDecodeError):
                pass

            # iterate remaining chunks until done
            for line in resp.iter_lines():
                if not line:
                    continue
                raw_response += line
                # debug: print raw line (decoded) to terminal
                try:
                    decoded_line = line.decode('utf-8')
                    print_substep(f"[OhFreeMe debug] Received line: {decoded_line}", style="blue")
                except Exception:
                    pass
                # check for error object (e.g., server overload)
                try:
                    obj = json.loads(line.decode('utf-8'))
                    if obj.get('status') == 'error':
                        raise RuntimeError(f"OhFreeMe API error: {obj.get('message', 'unknown')}")
                except json.JSONDecodeError:
                    pass
                if b'"status":"done"' in line:
                    break
            if b'"status":"done"' in raw_response:
                # decode to str for _extract_audio
                return self._extract_audio(raw_response.decode('utf-8'))

        raise RuntimeError(f"OhFreeMe TTS failed after {MAX_RETRIES} retries (rate limited)")

    def _extract_audio(self, raw: str) -> bytes:
        # API returns multiple JSON objects concatenated, e.g.:
        # {"status":"audio_chunk","chunk":"..."}{"status":"done","url":"data:audio/mpeg;base64,..."}
        decoder = json.JSONDecoder()
        pos = 0
        audio_b64 = None

        while pos < len(raw):
            # Skip whitespace
            while pos < len(raw) and raw[pos].isspace():
                pos += 1
            if pos >= len(raw):
                break

            obj, end = decoder.raw_decode(raw, pos)
            pos = end

            if obj.get("status") == "done" and "url" in obj:
                url = obj["url"]
                # url format: "data:audio/mpeg;base64,<base64data>"
                if url.startswith("data:") and ";base64," in url:
                    b64_part = url.split(";base64,", 1)[1]
                    audio_b64 = b64_part
                break

        if not audio_b64:
            raise RuntimeError(f"Could not extract audio from API response")

        return base64.b64decode(audio_b64)
