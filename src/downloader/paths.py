"""강의 다운로드 경로 계산 — 서비스/UI 레이어가 공통으로 사용하는 순수 함수 모음.

`LectureItem`에 메서드로 추가하면 scraper → downloader 역방향 의존이 되어
`src/scraper/models.py`가 이 파일을 import해야 한다. 그래서 모델 대신 downloader 레이어에
두고, 호출자(service/ui)가 lecture + 과목명 + 경로를 넘기도록 한다.

경로 구조 `과목명/N주차/강의명.mp4`는 `make_filepath`가 단일 소스 오브 트루스.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.downloader.video_downloader import make_filepath

if TYPE_CHECKING:
    from src.scraper.models import LectureItem


def expected_paths(
    download_dir: str | Path,
    course_long_name: str,
    lec: LectureItem,
) -> tuple[Path, Path]:
    """`(mp4, mp3)` 절대 경로 튜플."""
    mp4_rel = make_filepath(course_long_name, lec.week_label, lec.title)
    mp4 = (Path(download_dir) / mp4_rel).resolve()
    mp3 = mp4.with_suffix(".mp3")
    return mp4, mp3


def file_present(
    download_dir: str | Path,
    course_long_name: str,
    lec: LectureItem,
    rule: str,
) -> bool:
    """DOWNLOAD_RULE에 따라 기대되는 파일이 모두 존재하는지 확인한다."""
    mp4, mp3 = expected_paths(download_dir, course_long_name, lec)
    if rule == "video":
        return mp4.exists()
    if rule == "audio":
        return mp3.exists()
    if rule == "both":
        return mp4.exists() and mp3.exists()
    # 규칙 미설정 — 둘 중 하나만 있어도 present 간주
    return mp4.exists() or mp3.exists()
