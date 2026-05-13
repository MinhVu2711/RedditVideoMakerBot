import base64
import json
import os
import random
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils import settings
from utils.console import print_substep

# Load environment variables from .env file
load_dotenv()

CRIKK_API_URL = os.getenv("CRIKK_API_URL", "")
CRIKK_BASE_URL = os.getenv("CRIKK_BASE_URL", "")
VOICES_FILE = Path(__file__).resolve().parent.parent / "config" / "crikk_voices.json"

def _load_voices() -> list[dict]:
    try:
        with open(VOICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print_substep(f"Warning: Could not load voices from {VOICES_FILE}: {e}", style="yellow")
        return []


class Crikk:
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
        self.max_chars = 1200
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
            "voice": voice_id,
        }
        headers = {
            "cache-control": "no-cache",
            "content-type": "application/json",
            "accept": "*/*",
            "user-agent": self._pick_user_agent(), # important
            "origin": CRIKK_BASE_URL, # important
        }

        resp = requests.post(CRIKK_API_URL, json=payload, headers=headers)
        data = resp.json()

        if data.get("message") == "success":
            print_substep(f"[Crikk debug] Received")
            return self._extract_audio(data)

        raise RuntimeError(f"Crikk TTS failed")

    def _extract_audio(self, data: dict) -> bytes:
        # Expecting a dict with a "url" field containing a data URI
        url = data.get("audio_data")
        if not url:
            raise RuntimeError("Missing 'url' in API response data")
        # url format: "data:audio/mpeg;base64,<base64data>"
        if not (url.startswith("data:") and ";base64," in url):
            raise RuntimeError(f"Unexpected URL format in API response: {url}")
        b64_part = url.split(";base64,", 1)[1]
        return base64.b64decode(b64_part)
