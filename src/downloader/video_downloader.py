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
    LMS 강의 페이지에서 mp4 URL을 추출한다.

    Plan A: video 태그 src 폴링 (일반 타입)
    Plan B: Network 요청 가로채기 — mp4 URL이 포함된 요청 캡처 (readystream 등)
    """
    import asyncio

    captured: dict = {"url": None}
    all_requests: list = []  # 디버그: 모든 네트워크 요청 기록

    _EXCLUDE_PATTERNS = ("preloader.mp4", "preview.mp4", "thumbnail.mp4")

    def _is_valid_mp4(url: str) -> bool:
        return ".mp4" in url and not any(p in url for p in _EXCLUDE_PATTERNS)

    def _on_request(request):
        url = request.url
        all_requests.append(url)
        if _is_valid_mp4(url) and captured["url"] is None:
            # print(f"  [NET] mp4 요청 감지: {url[:120]}")
            captured["url"] = url

    def _on_response(response):
        url = response.url
        if _is_valid_mp4(url) and captured["url"] is None:
            # print(f"  [NET] mp4 응답 감지: {url[:120]}")
            captured["url"] = url
        # content.php 응답에서 미디어 URL 추출
        if "content.php" in url and "commons.ssu.ac.kr" in url:
            async def _parse_content_php():
                try:
                    import xml.etree.ElementTree as ET
                    body = await response.text()
                    root = ET.fromstring(body)
                    info = root.find("content_playing_info")
                    if info is None:
                        info = root

                    # service_root는 content_playing_info의 형제 노드 (root 직접 탐색)
                    svc = root.find("service_root")
                    progressive_uri = None
                    if svc is not None:
                        for media_el in svc.findall("media"):
                            for uri_el in media_el.findall("media_uri"):
                                if uri_el.get("method") == "progressive" and uri_el.get("target") == "all":
                                    progressive_uri = uri_el.text.strip() if uri_el.text else None
                                    break

                    # main_media_list > main_media (파일명)
                    media_file = None
                    for story in info.findall(".//story"):
                        for mm in story.findall(".//main_media"):
                            if mm.text:
                                media_file = mm.text.strip()
                                break

                    # print(f"  [NET] progressive_uri: {progressive_uri}")
                    # print(f"  [NET] media_file: {media_file}")

                    if progressive_uri and media_file and "[MEDIA_FILE]" in progressive_uri:
                        final_url = progressive_uri.replace("[MEDIA_FILE]", media_file)
                        # print(f"  [NET] 미디어 URL 조합: {final_url}")
                        if captured["url"] is None:
                            captured["url"] = final_url
                except Exception as e:
                    pass  # print(f"  [NET] content.php 파싱 오류: {e}")
            asyncio.ensure_future(_parse_content_php())

    page.on("request", _on_request)
    page.on("response", _on_response)

    try:
        # print(f"  [DBG] 페이지 이동: {lecture_url[:80]}")
        await page.goto(lecture_url, wait_until="domcontentloaded", timeout=60000)
        # iframe + content.php 로드 대기 (비동기 파싱 완료까지)
        for _ in range(20):  # 최대 10초
            await asyncio.sleep(0.5)
            if captured["url"]:
                break
        # print(f"  [DBG] 현재 페이지 URL: {page.url[:80]}")

        # content.php에서 미디어 URL이 추출됐으면 바로 반환
        if captured["url"]:
            # print(f"  [NET] content.php에서 미디어 URL 추출 성공: {captured['url']}")
            return captured["url"]

        player_frame = await _find_player_frame(page)
        if not player_frame:
            # print("  [DBG] player frame을 찾지 못했습니다.")
            # for f in page.frames:
            #     print(f"  [DBG]   {f.url[:100]}")
            return None

        # print(f"  [DBG] player frame 발견: {player_frame.url[:80]}")

        # 이어보기 다이얼로그 처리 후 재생 버튼 클릭
        await asyncio.sleep(1)
        await _dismiss_dialog(player_frame, restart=True)
        clicked = await _click_play(player_frame)
        # print(f"  [DBG] 재생 버튼 클릭: {'성공' if clicked else '실패(버튼 없음)'}")
        await asyncio.sleep(1)
        await _dismiss_dialog(player_frame, restart=True)

        # 최대 60초 폴링: Plan A(video DOM) + Plan B(network 캡처) 동시 확인
        # 재생 후 새로운 frame이 생성될 수 있으므로 page.frames 전체를 매번 재스캔
        # 이어보기 다이얼로그도 매 폴링마다 체크 (재생 도중 뒤늦게 뜨는 경우 대응)
        dialog_dismissed = False
        for i in range(120):
            # Plan B 먼저 확인 (network에서 이미 캡처됐을 수 있음)
            if captured["url"]:
                return captured["url"]

            # 이어보기 다이얼로그가 재생 도중 뒤늦게 뜨는 경우 처리
            if not dialog_dismissed:
                dialog_dismissed = await _dismiss_dialog(player_frame, restart=True)

            # Plan A: 모든 commons frame에서 video 태그 src 확인 (재생 후 새 frame 포함)
            commons_frames = [f for f in page.frames if "commons.ssu.ac.kr" in f.url]
            # if i % 10 == 0:
            #     print(f"  [DBG] 폴링({i}): commons frame 수={len(commons_frames)}")
            #     for fi, f in enumerate(commons_frames):
            #         print(f"  [DBG]   commons[{fi}]: {f.url[:80]}")

            for frame in commons_frames:
                try:
                    # get_attribute 방식으로 직접 조회 (evaluate보다 안정적)
                    video_el = await frame.query_selector("video.vc-vplay-video1")
                    if video_el:
                        src = await video_el.get_attribute("src")
                        # if i % 10 == 0:
                        #     print(f"  [DBG]   vc-vplay-video1 src: {str(src)[:80]}")
                        if src and src.startswith("http") and ".mp4" in src:
                            return src

                    # fallback: 모든 video 태그 확인
                    result = await frame.evaluate("""() => {
                        const videos = document.querySelectorAll('video');
                        for (const v of videos) {
                            const src = v.src || v.currentSrc || '';
                            if (src && src.startsWith('http') && src.includes('.mp4')) return src;
                        }
                        return null;
                    }""")
                    # if i % 10 == 0:
                    #     print(f"  [DBG]   fallback video.src: {str(result)[:80]}")
                    if result:
                        return result
                except Exception:
                    pass  # if i % 10 == 0: print(f"  [DBG]   video 평가 오류: {e}")

            await asyncio.sleep(0.5)

        # 폴링 종료 (60초) — 아래 디버그 코드는 URL 추출 실패 시 원인 분석용
        # print("  [DBG] 60초 폴링 종료. player 설정 파일 분석...")

        # async def _fetch_text(url: str) -> str:
        #     try:
        #         resp = await page.request.get(url)
        #         if resp.status != 200:
        #             return ""
        #         raw = await resp.body()
        #         for enc in ("utf-8", "euc-kr", "cp949", "latin-1"):
        #             try:
        #                 return raw.decode(enc)
        #             except Exception:
        #                 continue
        #         return raw.decode("latin-1")
        #     except Exception as e:
        #         print(f"  [DBG] fetch 오류 {url}: {e}")
        #         return ""

        # uni-player.min.js — m3u8/HLS URL 조합 로직 분석
        # import re as _re
        # player_js_url = next((u for u in all_requests if "uni-player.min.js" in u), None)
        # if player_js_url:
        #     print(f"  [DBG] uni-player.min.js fetch 중...")
        #     text = await _fetch_text(player_js_url)
        #     print(f"  [DBG] uni-player.min.js 크기: {len(text)} bytes")
        #     matches = _re.findall(r'.{0,150}(?:m3u8|\.m3u|hls(?:Url|Path|Src)|readystream|stream_url|streamUrl|videoSrc|mediaSrc|contentUri|content_uri|upf|ssmovie).{0,150}', text)
        #     print(f"  [DBG] uni-player.min.js 관련 키워드 ({len(matches)}개):")
        #     for m in matches[:40]:
        #         print(f"  [DBG]   {m.strip()[:300]}")

        return captured["url"]

    finally:
        page.remove_listener("request", _on_request)
        page.remove_listener("response", _on_response)


async def download_video_with_browser(
    page,
    url: str,
    save_path: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Playwright 브라우저 컨텍스트로 영상을 다운로드한다 (CDN 인증 자동 처리)."""
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # CDN URL이 403이면 원본 서버(commons.ssu.ac.kr) 경로로 대체 시도
    urls_to_try = [url]
    if "commonscdn.com" in url:
        fallback = url.replace("ssu-toast.commonscdn.com", "commons.ssu.ac.kr")
        urls_to_try.append(fallback)

    referer = "https://commons.ssu.ac.kr/"
    last_status = None
    for try_url in urls_to_try:
        # print(f"  [DBG] 다운로드 시도: {try_url[:100]}")
        response = await page.request.get(
            try_url,
            headers={"Referer": referer},
        )
        last_status = response.status
        # print(f"  [DBG] 응답 상태: {last_status}")
        if response.status == 200:
            total = int(response.headers.get("content-length", 0))
            body = await response.body()
            with open(save_path, "wb") as f:
                f.write(body)
            if on_progress and total > 0:
                on_progress(len(body), total)
            return save_path.resolve()

    raise Exception(f"다운로드 실패: HTTP {last_status} for {url}")


def download_video(
    url: str,
    save_path: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    cookies: dict = None,
    referer: str = None,
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
            _stream_download(url, save_path, on_progress, attempt, cookies=cookies, referer=referer)
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
    cookies: dict = None,
    referer: str = None,
):
    headers = {"Referer": referer} if referer else {}
    response = requests.get(url, stream=True, timeout=_TIMEOUT,
                            cookies=cookies, headers=headers)
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
