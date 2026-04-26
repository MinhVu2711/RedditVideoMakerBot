"""
Manual Screenshot → Video Pipeline

This module provides an alternative workflow that creates videos
from manually captured screenshots and text files, without requiring
any social media API access.

Supported platforms: Reddit, Threads (Meta), X (Twitter), or any other.

Usage:
    python manual_main.py init <post_id>        # Create folder structure
    python manual_main.py render <post_id>      # Render one post
    python manual_main.py render --all          # Render all unrendered posts
    python manual_main.py list                  # List all posts with status
"""
