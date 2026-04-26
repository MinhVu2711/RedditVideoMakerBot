"""
Video Builder for the manual pipeline.

Takes a post_object (with TTS audio already generated), downloads/chops
background video and audio, overlays screenshots onto the background
with correct timing, and renders the final video.

Reuses background download functions from video_creation/background.py.
Uses libx264 encoder (CPU-based) by default.
"""

import math
import multiprocessing
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Tuple

import ffmpeg
from moviepy import AudioFileClip, VideoFileClip
from rich.console import Console

from utils import settings
from utils.console import print_step, print_substep

console = Console()


class ProgressFfmpeg(threading.Thread):
    """Thread to monitor FFmpeg progress during rendering."""

    def __init__(self, vid_duration_seconds, progress_update_callback):
        threading.Thread.__init__(self, name="ProgressFfmpeg")
        self.stop_event = threading.Event()
        self.output_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        self.vid_duration_seconds = vid_duration_seconds
        self.progress_update_callback = progress_update_callback

    def run(self):
        while not self.stop_event.is_set():
            latest_progress = self.get_latest_ms_progress()
            if latest_progress is not None:
                completed_percent = latest_progress / self.vid_duration_seconds
                self.progress_update_callback(completed_percent)
            time.sleep(1)

    def get_latest_ms_progress(self):
        lines = self.output_file.readlines()
        if lines:
            for line in lines:
                if "out_time_ms" in line:
                    out_time_ms_str = line.split("=")[1].strip()
                    if out_time_ms_str.isnumeric():
                        return float(out_time_ms_str) / 1000000.0
        return None

    def stop(self):
        self.stop_event.set()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args, **kwargs):
        self.stop()


class ManualVideoBuilder:
    """Builds the final video from screenshots + TTS audio + background."""

    def __init__(self, post_object: dict, manual_config: dict):
        """
        Args:
            post_object: Post data with audio already generated (from tts_processor)
            manual_config: Manual-specific config dict
        """
        self.post = post_object
        self.post_id = post_object["post_id"]
        self.config = manual_config
        self.temp_dir = Path(f"assets/temp/{self.post_id}")

        # Video settings
        self.W = int(self.config.get("resolution_w", settings.config["settings"].get("resolution_w", 1080)))
        self.H = int(self.config.get("resolution_h", settings.config["settings"].get("resolution_h", 1920)))
        self.opacity = float(self.config.get("opacity", settings.config["settings"].get("opacity", 0.9)))
        self.encoder = self.config.get("encoder", "libx264")

        # Background settings
        self.bg_video_name = self.config.get(
            "background_video",
            settings.config["settings"]["background"].get("background_video", "random"),
        )
        self.bg_audio_name = self.config.get(
            "background_audio",
            settings.config["settings"]["background"].get("background_audio", "random"),
        )
        self.bg_audio_volume = float(
            self.config.get(
                "background_audio_volume",
                settings.config["settings"]["background"].get("background_audio_volume", 0.15),
            )
        )

        # Local background directories (user drops files here)
        self.bg_video_dir = Path(self.config.get("background_video_dir", "assets/backgrounds/video"))
        self.bg_audio_dir = Path(self.config.get("background_audio_dir", "assets/backgrounds/audio"))

        # Output settings
        self.output_dir = Path(self.config.get("output_dir", "manual_results"))

    def build(self) -> str:
        """Build the final video.

        Pipeline:
        1. Filter screenshots that have audio
        2. Download background video & audio (cached)
        3. Chop background to match video length
        4. Prepare background (crop to aspect ratio)
        5. Concat all audio clips → final audio track
        6. Mix with background audio
        7. Overlay screenshots onto background with timing
        8. Render final video

        Returns:
            Path to the output video file
        """
        # Filter screenshots with audio
        clips = [s for s in self.post["screenshots"] if s.get("audio_path") and s.get("audio_duration")]
        if not clips:
            print_substep("No audio clips found. Cannot create video.", style="red")
            return ""

        total_duration = sum(s["audio_duration"] for s in clips)
        video_length = math.ceil(total_duration)

        console.log(f"[bold green] Video will be: {video_length} seconds long ({len(clips)} clips)")

        # Ensure temp directory exists
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Download backgrounds
        print_step("📥 Downloading backgrounds (if needed)...")
        bg_config = self._get_background_config()
        self._download_backgrounds(bg_config)

        # Step 2: Chop backgrounds to video length
        print_step("✂️ Chopping backgrounds to video length...")
        self._chop_backgrounds(bg_config, video_length)

        # Step 3: Prepare background (crop to aspect ratio)
        print_step("🎬 Preparing background...")
        bg_path = self._prepare_background()
        background_clip = ffmpeg.input(bg_path)

        # Step 4: Concat audio clips
        print_step("🔊 Building audio track...")
        audio_inputs = [ffmpeg.input(s["audio_path"]) for s in clips]
        audio_concat = ffmpeg.concat(*audio_inputs, a=1, v=0)
        audio_path = str(self.temp_dir / "audio.mp3")
        ffmpeg.output(
            audio_concat, audio_path, **{"b:a": "192k"}
        ).overwrite_output().run(quiet=True)

        # Step 5: Merge with background audio
        audio = ffmpeg.input(audio_path)
        final_audio = self._merge_background_audio(audio)

        # Step 6: Overlay screenshots
        print_step("🖼️ Overlaying screenshots...")
        screenshot_width = int((self.W * 45) // 100)
        current_time = 0

        for s in clips:
            img_input = ffmpeg.input(s["image_path"])["v"].filter("scale", screenshot_width, -1)
            img_overlay = img_input.filter("colorchannelmixer", aa=self.opacity)

            background_clip = background_clip.overlay(
                img_overlay,
                enable=f"between(t,{current_time},{current_time + s['audio_duration']})",
                x="(main_w-overlay_w)/2",
                y="(main_h-overlay_h)/2",
            )
            current_time += s["audio_duration"]

        # Scale to final resolution
        background_clip = background_clip.filter("scale", self.W, self.H)

        # Step 7: Render
        print_step("🎥 Rendering the video...")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Normalize filename
        filename = self._normalize_filename(self.post.get("title", self.post_id))
        output_path = str(self.output_dir / f"{filename}.mp4")
        # Prevent path too long
        if len(output_path) > 251:
            output_path = output_path[:247] + ".mp4"

        from tqdm import tqdm

        pbar = tqdm(total=100, desc="Progress: ", bar_format="{l_bar}{bar}", unit=" %")

        def on_update(progress):
            status = round(progress * 100, 2)
            old_percentage = pbar.n
            pbar.update(status - old_percentage)

        with ProgressFfmpeg(video_length, on_update) as progress:
            try:
                ffmpeg.output(
                    background_clip,
                    final_audio,
                    output_path,
                    f="mp4",
                    **{
                        "c:v": self.encoder,
                        "b:v": "20M",
                        "b:a": "192k",
                        "threads": multiprocessing.cpu_count(),
                    },
                ).overwrite_output().global_args(
                    "-progress", progress.output_file.name
                ).run(
                    quiet=True,
                    overwrite_output=True,
                    capture_stdout=False,
                    capture_stderr=False,
                )
            except ffmpeg.Error as e:
                print_substep(f"FFmpeg error: {e.stderr.decode('utf8') if e.stderr else str(e)}", style="red")
                pbar.close()
                return ""

        old_percentage = pbar.n
        pbar.update(100 - old_percentage)
        pbar.close()

        # Save to tracking (shared videos.json)
        self._save_tracking(bg_config, output_path)

        # Cleanup temp files
        print_step("🗑️ Removing temporary files...")
        self._cleanup()

        self.post["output_path"] = output_path
        print_step(f"✅ Done! Video saved to: {output_path}")

        return output_path

    def _scan_local_files(self, directory: Path, extensions: tuple) -> list:
        """Scan a directory for files matching given extensions.

        Returns:
            List of Path objects, sorted by name
        """
        if not directory.exists():
            return []
        files = []
        for f in directory.iterdir():
            if f.is_file() and f.suffix.lower() in extensions:
                files.append(f)
        return sorted(files)

    def _get_background_config(self) -> dict:
        """Get background video & audio — local random or YouTube fallback.

        Priority:
        1. Scan local directories for video/audio files
        2. If config is 'random' or local files exist → pick random from local
        3. If config is a specific name AND no local files → use YouTube download

        Returns:
            dict with 'video_path', 'audio_path', 'video_credit', 'audio_credit'
        """
        import random

        result = {
            "video_path": None,
            "audio_path": None,
            "video_credit": "unknown",
            "audio_credit": "unknown",
            "_youtube_video": None,  # YouTube config tuple (for download if needed)
            "_youtube_audio": None,
        }

        # --- Video background ---
        video_exts = (".mp4", ".mkv", ".webm", ".avi", ".mov")
        local_videos = self._scan_local_files(self.bg_video_dir, video_exts)

        if local_videos:
            # Pick random from local files
            chosen = random.choice(local_videos)
            result["video_path"] = str(chosen)
            result["video_credit"] = chosen.stem
            print_substep(f"🎬 Background video: {chosen.name} (random from {len(local_videos)} files)")
        else:
            # Fallback: YouTube download via background_options
            try:
                from video_creation.background import background_options
                video_name = self.bg_video_name
                if video_name == "random" or video_name not in background_options["video"]:
                    video_name = random.choice(list(background_options["video"].keys()))
                result["_youtube_video"] = background_options["video"][video_name]
                print_substep(f"🎬 Background video: {video_name} (YouTube)")
            except Exception as e:
                print_substep(f"⚠ Could not load YouTube backgrounds: {e}", style="yellow")

        # --- Audio background ---
        if self.bg_audio_volume > 0:
            audio_exts = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")
            local_audios = self._scan_local_files(self.bg_audio_dir, audio_exts)

            if local_audios:
                chosen = random.choice(local_audios)
                result["audio_path"] = str(chosen)
                result["audio_credit"] = chosen.stem
                print_substep(f"🎵 Background audio: {chosen.name} (random from {len(local_audios)} files)")
            else:
                try:
                    from video_creation.background import background_options
                    audio_name = self.bg_audio_name
                    if audio_name == "random" or audio_name not in background_options["audio"]:
                        audio_name = random.choice(list(background_options["audio"].keys()))
                    result["_youtube_audio"] = background_options["audio"][audio_name]
                    print_substep(f"🎵 Background audio: {audio_name} (YouTube)")
                except Exception as e:
                    print_substep(f"⚠ Could not load YouTube audio backgrounds: {e}", style="yellow")

        return result

    def _download_backgrounds(self, bg_config: dict):
        """Download YouTube backgrounds only if no local files were found."""
        if bg_config.get("_youtube_video"):
            from video_creation.background import download_background_video
            download_background_video(bg_config["_youtube_video"])
            # Set video_path to the downloaded file
            yt_cfg = bg_config["_youtube_video"]
            bg_config["video_path"] = f"assets/backgrounds/video/{yt_cfg[2]}-{yt_cfg[1]}"
            bg_config["video_credit"] = yt_cfg[2]

        if bg_config.get("_youtube_audio"):
            from video_creation.background import download_background_audio
            download_background_audio(bg_config["_youtube_audio"])
            yt_cfg = bg_config["_youtube_audio"]
            bg_config["audio_path"] = f"assets/backgrounds/audio/{yt_cfg[2]}-{yt_cfg[1]}"
            bg_config["audio_credit"] = yt_cfg[2]

    def _chop_backgrounds(self, bg_config: dict, video_length: int):
        """Chop background video and audio to match the video length."""
        from video_creation.background import get_start_and_end_times

        # Chop background audio
        if self.bg_audio_volume > 0 and bg_config.get("audio_path"):
            audio_file = bg_config["audio_path"]
            if Path(audio_file).exists():
                background_audio = AudioFileClip(audio_file)
                start_a, end_a = get_start_and_end_times(video_length, background_audio.duration)
                chopped = background_audio.subclipped(start_a, end_a)
                chopped.write_audiofile(str(self.temp_dir / "background.mp3"))
                background_audio.close()
                chopped.close()

        # Chop background video
        video_file = bg_config.get("video_path")
        if video_file and Path(video_file).exists():
            with VideoFileClip(video_file) as video:
                start_v, end_v = get_start_and_end_times(video_length, video.duration)
                chopped = video.subclipped(start_v, end_v)
                chopped.write_videofile(str(self.temp_dir / "background.mp4"))
        else:
            print_substep("⚠ No background video file found!", style="red")
            raise FileNotFoundError(f"Background video not found: {video_file}")

    def _prepare_background(self) -> str:
        """Crop background video to correct aspect ratio (W:H).

        Returns:
            Path to the cropped background video
        """
        output_path = str(self.temp_dir / "background_noaudio.mp4")
        try:
            (
                ffmpeg.input(str(self.temp_dir / "background.mp4"))
                .filter("crop", f"ih*({self.W}/{self.H})", "ih")
                .output(
                    output_path,
                    an=None,
                    **{
                        "c:v": self.encoder,
                        "b:v": "20M",
                        "threads": multiprocessing.cpu_count(),
                    },
                )
                .overwrite_output()
                .run(quiet=True)
            )
        except ffmpeg.Error as e:
            print_substep(f"Background prepare error: {e}", style="red")
            raise
        return output_path

    def _merge_background_audio(self, tts_audio):
        """Merge TTS audio with background audio.

        Args:
            tts_audio: FFmpeg audio input of the TTS track

        Returns:
            Merged audio stream or original if background audio disabled
        """
        if self.bg_audio_volume == 0:
            return tts_audio

        bg_audio_path = self.temp_dir / "background.mp3"
        if not bg_audio_path.exists():
            return tts_audio

        bg_audio = ffmpeg.input(str(bg_audio_path)).filter("volume", self.bg_audio_volume)
        merged = ffmpeg.filter([tts_audio, bg_audio], "amix", duration="longest")
        return merged

    def _normalize_filename(self, name: str) -> str:
        """Normalize a string to be safe for filenames."""
        # Remove problematic characters
        name = re.sub(r'[?\\"%*:|<>]', "", name)
        name = re.sub(r"[/]", " ", name)
        name = name.strip()
        if not name:
            name = self.post_id
        # Limit length
        return name[:100]

    def _save_tracking(self, bg_config: dict, output_path: str):
        """Save rendered video info to shared videos.json.

        Handles missing file gracefully (creates it if needed).
        Does NOT import from utils.videos to avoid praw dependency.
        """
        import json
        import time as t

        videos_path = Path("./video_creation/data/videos.json")
        videos_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing data or start fresh
        done_vids = []
        if videos_path.exists():
            try:
                with open(videos_path, "r", encoding="utf-8") as f:
                    done_vids = json.load(f)
            except (json.JSONDecodeError, IOError):
                done_vids = []

        # Skip if already recorded
        if self.post_id in [v.get("id") for v in done_vids]:
            return

        payload = {
            "subreddit": self.post.get("platform", "manual"),
            "id": self.post_id,
            "time": str(int(t.time())),
            "background_credit": bg_config.get("video_credit", "unknown"),
            "reddit_title": self.post.get("title", ""),
            "filename": Path(output_path).name,
        }
        done_vids.append(payload)

        with open(videos_path, "w", encoding="utf-8") as f:
            json.dump(done_vids, f, ensure_ascii=False, indent=4)

    def _cleanup(self):
        """Remove temporary files for this post."""
        temp_path = f"assets/temp/{self.post_id}/"
        if Path(temp_path).exists():
            import shutil
            shutil.rmtree(temp_path)
