"""
Scanner module for the manual pipeline.

Scans manual_posts/ directories for screenshots (.png), audio files (.mp3),
and optional text files (.txt). Builds a unified post_object for processing.

Folder convention:
    manual_posts/
    └── my_post_001/
        ├── meta.json           (optional - metadata)
        ├── 0_title.png         (required - screenshot of post title)
        ├── 0_title.mp3         (preferred - pre-recorded audio)
        ├── 0_title.txt         (fallback - text for TTS if no .mp3)
        ├── 1_comment.png       (optional - comment screenshots)
        ├── 1_comment.mp3       (preferred - pre-recorded audio)
        ├── 1_comment.txt       (fallback - text for TTS if no .mp3)
        └── ...

Priority: .mp3 > .txt (if both exist, .mp3 is used and TTS is skipped).
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.console import print_step, print_substep


class PostScanner:
    """Scans manual_posts/ directory, validates structure, builds post_object."""

    # Regex pattern: <number>_<type>.<ext> where ext is png/jpg/jpeg/mp3/txt
    FILE_PATTERN = re.compile(r"^(\d+)_(title|comment)\.(png|jpg|jpeg|mp3|txt)$", re.IGNORECASE)

    def __init__(self, input_dir: str = "manual_posts"):
        self.input_dir = Path(input_dir)

    def scan_all(self) -> List[dict]:
        """Scan all post folders in the input directory.

        Returns:
            List of post_object dicts, sorted by folder name
        """
        if not self.input_dir.exists():
            print_substep(f"Input directory '{self.input_dir}' does not exist.", style="red")
            return []

        posts = []
        for post_dir in sorted(self.input_dir.iterdir()):
            if post_dir.is_dir() and not post_dir.name.startswith("."):
                post_obj = self.scan_one(post_dir.name)
                if post_obj is not None:
                    posts.append(post_obj)

        return posts

    def scan_one(self, post_id: str) -> Optional[dict]:
        """Scan a single post folder and build post_object.

        Args:
            post_id: Name of the folder inside manual_posts/

        Returns:
            post_object dict or None if invalid
        """
        post_dir = self.input_dir / post_id

        if not post_dir.exists():
            print_substep(f"Post directory '{post_dir}' does not exist.", style="red")
            return None

        is_valid, errors = self.validate(post_dir)
        if not is_valid:
            print_substep(f"Validation failed for '{post_id}':", style="red")
            for err in errors:
                print_substep(f"  ✗ {err}", style="red")
            return None

        return self._build_post_object(post_dir)

    def validate(self, post_dir: Path) -> Tuple[bool, List[str]]:
        """Validate a post folder structure.

        Checks:
        - At least 1 image file exists
        - Title image (0_title.png) exists
        - Each image has a corresponding .mp3 or .txt file
        - Files follow naming convention

        Returns:
            (is_valid, list_of_errors)
        """
        errors = []

        # Gather all matching files
        images, audios, texts = self._categorize_files(post_dir)

        # Check: at least 1 image
        if not images:
            errors.append("No image files found. Need at least 0_title.png")
            return False, errors

        # Check: title image exists (index 0)
        if 0 not in images:
            errors.append("Missing title image: 0_title.png (must start with '0_')")

        # Check: each image has a corresponding .mp3 or .txt file
        for idx in sorted(images.keys()):
            if idx not in audios and idx not in texts:
                errors.append(
                    f"Missing audio/text for image #{idx}: "
                    f"provide '{idx}_title.mp3' (or .txt as fallback)"
                )

        # Check: text files (used as TTS fallback) are not empty
        for idx, txt_path in texts.items():
            if idx not in audios:  # Only check .txt if no .mp3 exists
                content = txt_path.read_text(encoding="utf-8").strip()
                if not content:
                    errors.append(f"Text file is empty (and no .mp3 provided): {txt_path.name}")

        return len(errors) == 0, errors

    def list_status(self) -> List[dict]:
        """List all posts with their status.

        Returns:
            List of dicts with keys: post_id, num_images, num_audios, num_texts, status
        """
        if not self.input_dir.exists():
            return []

        results = []
        for post_dir in sorted(self.input_dir.iterdir()):
            if not post_dir.is_dir() or post_dir.name.startswith("."):
                continue

            images, audios, texts = self._categorize_files(post_dir)
            is_valid, errors = self.validate(post_dir)

            # Determine status
            if not images:
                status = "empty"
            elif not is_valid:
                status = "incomplete"
            else:
                status = "ready"

            results.append(
                {
                    "post_id": post_dir.name,
                    "num_images": len(images),
                    "num_audios": len(audios),
                    "num_texts": len(texts),
                    "status": status,
                    "errors": errors,
                }
            )

        return results

    def _categorize_files(self, post_dir: Path) -> Tuple[Dict[int, Path], Dict[int, Path], Dict[int, Path]]:
        """Categorize files in a post directory into images, audios, and texts.

        Returns:
            (images_dict, audios_dict, texts_dict) where key is the index number
        """
        images = {}  # {0: Path("0_title.png"), ...}
        audios = {}  # {0: Path("0_title.mp3"), ...}
        texts = {}   # {0: Path("0_title.txt"), ...}

        for f in post_dir.iterdir():
            match = self.FILE_PATTERN.match(f.name)
            if match:
                idx = int(match.group(1))
                ext = match.group(3).lower()
                if ext in ("png", "jpg", "jpeg"):
                    images[idx] = f
                elif ext == "mp3":
                    audios[idx] = f
                elif ext == "txt":
                    texts[idx] = f

        return images, audios, texts

    def _build_post_object(self, post_dir: Path) -> dict:
        """Build the unified post_object from a validated post directory.

        Returns:
            dict with structure:
            {
                "post_id": str,
                "platform": str,
                "title": str,
                "author": str,
                "url": str,
                "post_dir": str,
                "screenshots": [
                    {
                        "index": int,
                        "type": "title" | "comment",
                        "image_path": str,
                        "text": str,
                        "audio_path": None,
                        "audio_duration": None,
                    },
                    ...
                ],
                "total_duration": 0,
                "output_path": None,
            }
        """
        post_id = post_dir.name

        # Read optional meta.json
        meta = self._read_meta(post_dir)

        # Categorize files
        images, audios, texts = self._categorize_files(post_dir)

        # Build screenshots list (sorted by index)
        screenshots = []
        for idx in sorted(images.keys()):
            img_path = images[idx]
            # Determine type from filename
            match = self.FILE_PATTERN.match(img_path.name)
            entry_type = match.group(2).lower() if match else "comment"

            # Audio: prefer .mp3, fallback to .txt for TTS
            audio_path = str(audios[idx]) if idx in audios else None
            text_content = ""
            if idx in texts:
                text_content = texts[idx].read_text(encoding="utf-8").strip()

            screenshots.append(
                {
                    "index": idx,
                    "type": entry_type,
                    "image_path": str(img_path),
                    "text": text_content,
                    "audio_path": audio_path,    # Pre-filled if .mp3 exists
                    "audio_duration": None,
                }
            )

        # Use title text, meta title, or folder name
        title = ""
        if screenshots and screenshots[0]["text"]:
            title = screenshots[0]["text"][:100]
        elif meta.get("title"):
            title = meta["title"]
        else:
            title = post_id

        return {
            "post_id": post_id,
            "platform": meta.get("platform", "other"),
            "title": title,
            "author": meta.get("author", ""),
            "url": meta.get("url", ""),
            "post_dir": str(post_dir),
            "screenshots": screenshots,
            "total_duration": 0,
            "output_path": None,
        }

    def _read_meta(self, post_dir: Path) -> dict:
        """Read meta.json if it exists, return empty dict otherwise."""
        meta_path = post_dir / "meta.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print_substep(f"Warning: Could not read meta.json: {e}", style="yellow")
        return {}


def create_post_folder(input_dir: str, post_id: str, platform: str = "reddit") -> Path:
    """Create a new post folder with template files.

    Args:
        input_dir: Base directory for manual posts
        post_id: Name for the new post folder
        platform: Source platform (reddit, threads, x, other)

    Returns:
        Path to the created folder
    """
    post_dir = Path(input_dir) / post_id
    post_dir.mkdir(parents=True, exist_ok=True)

    # Create meta.json template
    meta = {
        "platform": platform,
        "post_id": post_id,
        "title": "",
        "author": "",
        "url": "",
        "created_at": "",
        "tags": [],
        "notes": "",
    }
    meta_path = post_dir / "meta.json"
    if not meta_path.exists():
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4, ensure_ascii=False)

    print_step(f"Created post folder: {post_dir}")
    print_substep("Next steps:", style="bold cyan")
    print_substep("  1. Add screenshots: 0_title.png, 1_comment.png, ...")
    print_substep("  2. Add audio files: 0_title.mp3, 1_comment.mp3, ...")
    print_substep("     (Or use .txt files instead — TTS will generate audio)")
    print_substep("  3. (Optional) Edit meta.json with post details")
    print_substep(f"  4. Run: python manual_main.py render {post_id}")

    return post_dir
