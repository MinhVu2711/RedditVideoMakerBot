# 📖 Hướng Dẫn Sử Dụng Manual Pipeline

> **Tóm tắt**: Tạo video từ screenshots chụp tay (Reddit, Threads, X) mà không cần API.

---

## 🚀 Quick Start (3 bước)

### Bước 1: Tạo folder cho post mới
```bash
cd /home/minhvu/projects/RedditVideoMakerBot
python manual_main.py init my_first_post --platform reddit
```

Kết quả:
```
manual_posts/my_first_post/
├── meta.json           ← (optional) metadata
├── 0_title.txt         ← Chỉnh text cho TTS ở đây
└── 1_comment.txt       ← Chỉnh text comment ở đây
```

### Bước 2: Thêm screenshots + text

1. **Chụp screenshot** bài đăng → lưu thành `0_title.png`
2. **Chụp screenshot** comments → lưu thành `1_comment.png`, `2_comment.png`, ...
3. **Sửa file `.txt`** tương ứng — nhập nội dung text mà bot sẽ đọc thành giọng nói

```
manual_posts/my_first_post/
├── meta.json
├── 0_title.png         ← Screenshot bài đăng
├── 0_title.txt         ← "What's the most underrated life hack?"
├── 1_comment.png       ← Screenshot comment 1
├── 1_comment.txt       ← "I always put my phone on airplane mode..."
├── 2_comment.png       ← Screenshot comment 2
└── 2_comment.txt       ← "Using a binder clip as a phone stand..."
```

> [!IMPORTANT]
> Mỗi file `.png` **bắt buộc** phải có file `.txt` cùng số thứ tự. Số `0` luôn là title/bài đăng chính.

### Bước 3: Render video
```bash
python manual_main.py render my_first_post
```

Video sẽ được lưu tại: `manual_results/my_first_post.mp4`

---

## 📋 Tất Cả Commands

| Command | Mô tả |
|---------|--------|
| `python manual_main.py init <post_id>` | Tạo folder mới với template files |
| `python manual_main.py init <post_id> --platform threads` | Tạo folder cho Threads post |
| `python manual_main.py render <post_id>` | Render 1 post thành video |
| `python manual_main.py render --all` | Render tất cả posts chưa render |
| `python manual_main.py render <post_id> --force` | Re-render (dù đã render trước đó) |
| `python manual_main.py list` | Liệt kê tất cả posts + trạng thái |

---

## 📁 Quy Tắc Đặt Tên File

```
<số_thứ_tự>_<loại>.<ext>
```

| File | Ý nghĩa |
|------|----------|
| `0_title.png` | Screenshot bài đăng chính (bắt buộc) |
| `0_title.txt` | Text TTS cho bài đăng (bắt buộc) |
| `1_comment.png` | Screenshot comment 1 |
| `1_comment.txt` | Text TTS cho comment 1 |
| `N_comment.png/txt` | Comment thứ N |
| `meta.json` | Metadata (optional) |

> [!TIP]  
> File `.txt` hỗ trợ dòng comment bắt đầu bằng `#` — những dòng này sẽ bị bỏ qua khi TTS.

---

## ⚙️ Cấu Hình

Thêm section `[manual]` vào `config.toml` (hoặc để trống — bot sẽ dùng defaults):

```toml
[manual]
input_dir = "manual_posts"        # Thư mục input
output_dir = "manual_results"     # Thư mục output
encoder = "libx264"               # CPU encoder (hoặc h264_nvenc nếu có GPU)
resolution_w = 1080               # Width video
resolution_h = 1920               # Height video (1080x1920 = portrait)
opacity = 0.9                     # Độ trong suốt screenshot overlay
background_video = "minecraft"    # Video nền
background_audio = "lofi"         # Audio nền
background_audio_volume = 0.15    # Âm lượng audio nền (0 = tắt)
max_video_length = 120            # Max thời lượng video (giây)
```

TTS engine được lấy từ section `[settings.tts]` trong `config.toml`. Mặc định dùng **GoogleTranslate** (không cần API key).

---

## 🏗️ Kiến Trúc Module

```
manual/
├── __init__.py          # Module docstring
├── scanner.py           # Quét folders, validate, build post_object
├── tts_processor.py     # TTS: text → MP3 (reuse TTS/ engines)
└── video_builder.py     # FFmpeg: screenshots + audio → video

manual_main.py           # CLI entry point (init, render, list)
```

**Hoàn toàn tách biệt** với flow cũ (`main.py`). Không sửa bất kỳ file nào của flow cũ.

### Files đã tạo/sửa

| File | Action | Mô tả |
|------|--------|--------|
| [manual/__init__.py](file:///home/minhvu/projects/RedditVideoMakerBot/manual/__init__.py) | 🆕 Created | Module init |
| [manual/scanner.py](file:///home/minhvu/projects/RedditVideoMakerBot/manual/scanner.py) | 🆕 Created | Folder scanner & validator |
| [manual/tts_processor.py](file:///home/minhvu/projects/RedditVideoMakerBot/manual/tts_processor.py) | 🆕 Created | TTS processor |
| [manual/video_builder.py](file:///home/minhvu/projects/RedditVideoMakerBot/manual/video_builder.py) | 🆕 Created | Video assembler |
| [manual_main.py](file:///home/minhvu/projects/RedditVideoMakerBot/manual_main.py) | 🆕 Created | CLI entry point |
| [.gitignore](file:///home/minhvu/projects/RedditVideoMakerBot/.gitignore) | ✏️ Updated | Thêm `manual_posts/`, `manual_results/` |

---

## ⚠️ Lưu Ý

1. **FFmpeg** phải được cài sẵn trên hệ thống
2. **Background video** sẽ tự động tải từ YouTube lần đầu (cần internet)
3. Config.toml có thể trống — bot dùng built-in defaults (GoogleTranslate TTS)
4. Encoder mặc định là `libx264` (CPU) — phù hợp máy không có GPU NVIDIA
