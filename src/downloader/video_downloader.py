"""
영상 다운로드.

Playwright로 LMS 강의 페이지에서 video URL을 추출한 뒤,
requests로 청크 스트리밍 다운로드한다.
"""

import re
import time
from http.client import IncompleteRead
from pathlib import Path
from typing import Callable, Optional

import requests
from playwright.async_api import Page

from src.player.background_player import _find_player_frame, _click_play, _dismiss_dialog

_MAX_RETRIES = 3
_TIMEOUT = (10, 60)   # (connect, read) seconds
_CHUNK_SIZE = 65536   # 64 KB


def _sanitize_filename(name: str) -> str:
    """파일명에 사용 불가한 문자를 제거한다."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '', name)
    sanitized = sanitized.strip(' .')
    sanitized = re.sub(r'\s+', ' ', sanitized)
    return sanitized or 'lecture'


async def extract_video_url(page: Page, lecture_url: str) -> Optional[str]:
    """
    LMS 강의 페이지에서 video 태그의 src URL을 추출한다.

    1. 강의 페이지 이동
    2. commons.ssu.ac.kr frame 탐색
    3. 재생 버튼 클릭 (video 로딩 트리거)
    4. video.src 폴링
    """
    await page.goto(lecture_url, wait_until="networkidle")

    player_frame = await _find_player_frame(page)
    if not player_frame:
        return None

    # 이어보기 다이얼로그 처리 후 재생 버튼 클릭
    await _dismiss_dialog(player_frame, restart=True)
    await _click_play(player_frame)

    # video src 폴링 (최대 20초)
    for _ in range(40):
        for frame in page.frames:
            if "commons.ssu.ac.kr" not in frame.url:
                continue
            try:
                src = await frame.evaluate("""() => {
                    const v = document.querySelector('video');
                    return v ? (v.src || v.currentSrc || null) : null;
                }""")
                if src and src.startswith("http") and ".mp4" in src:
                    return src
            except Exception:
                pass

        import asyncio
        await asyncio.sleep(0.5)

    return None


def download_video(
    url: str,
    save_path: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """
    HTTP 스트리밍으로 영상을 다운로드한다.

    Args:
        url:         직접 다운로드 가능한 mp4 URL
        save_path:   저장 경로 (파일명 포함)
        on_progress: (downloaded_bytes, total_bytes) 콜백

    Returns:
        저장된 파일의 Path

    Raises:
        Exception: 최대 재시도 후에도 실패한 경우
    """
    save_path.parent.mkdir(parents=True, exist_ok=True)

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            _stream_download(url, save_path, on_progress, attempt)
            return save_path.resolve()
        except (IncompleteRead, requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            _remove_partial(save_path)
            if attempt < _MAX_RETRIES:
                wait = 2 ** attempt
                time.sleep(wait)
        except Exception as e:
            last_error = e
            _remove_partial(save_path)
            break

    raise last_error


def _stream_download(
    url: str,
    save_path: Path,
    on_progress: Optional[Callable[[int, int], None]],
    attempt: int,
):
    response = requests.get(url, stream=True, timeout=_TIMEOUT)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    downloaded = 0

    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total > 0:
                    on_progress(downloaded, total)


def _remove_partial(path: Path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def make_filename(course_name: str, lecture_title: str) -> str:
    """'과목명_영상원본이름.mp4' 형식의 파일명을 생성한다."""
    course = _sanitize_filename(course_name)
    title = _sanitize_filename(lecture_title)
    return f"{course}_{title}.mp4"
