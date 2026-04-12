"""누락 다운로드 복구 파이프라인 — UI/CLI 공용.

`src/ui/recover.py`(TUI 메뉴)와 `scripts/recover_missing.py`(CLI)에서 동일하게 사용한다.
두 진입점은 입력(scraper/courses/details)과 출력(console/stdout) 어댑터만 담당하고,
수집·실행·집계는 이 모듈이 단일 소스로 제공한다.

수집 대상:
- `lec.completion == "completed"` (LMS 기준 출석 완료)
- `lec.is_downloadable` True (learningx 등 구조적 불가는 제외)
- `lec.file_present(...)` False (현재 DOWNLOAD_RULE 기준 파일 누락)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from src.config import Config
from src.downloader.result import DownloadResult
from src.logger import get_logger

_log = get_logger("recover_pipeline")


@dataclass
class MissingItem:
    course: object  # Course
    lec: object  # LectureItem
    kind: str  # "mp4" / "mp3" / "mp4+mp3"


@dataclass
class RecoveryReport:
    total: int
    success: int
    failed_by_reason: Counter


def collect_missing(courses, details) -> list[MissingItem]:
    """LMS completion 기준 누락된 다운로드 항목을 전수 수집한다."""
    download_dir = Config.get_download_dir()
    rule = Config.DOWNLOAD_RULE or "both"
    missing: list[MissingItem] = []

    for course, detail in zip(courses, details, strict=False):
        if detail is None:
            continue
        for lec in detail.all_video_lectures:
            if lec.completion != "completed":
                continue
            if not lec.is_downloadable:
                continue

            mp4, mp3 = lec.expected_paths(download_dir, course.long_name)
            has_video = mp4.exists()
            has_audio = mp3.exists()

            if rule == "video" and not has_video:
                missing.append(MissingItem(course=course, lec=lec, kind="mp4"))
            elif rule == "audio" and not has_audio:
                missing.append(MissingItem(course=course, lec=lec, kind="mp3"))
            elif rule == "both" and not (has_video and has_audio):
                parts: list[str] = []
                if not has_video:
                    parts.append("mp4")
                if not has_audio:
                    parts.append("mp3")
                missing.append(MissingItem(course=course, lec=lec, kind="+".join(parts)))
    return missing


async def run_recovery(
    scraper,
    missing: list[MissingItem],
    *,
    on_progress: Callable[[int, int, MissingItem, DownloadResult | Exception | None], None] | None = None,
) -> RecoveryReport:
    """미싱 항목을 순차적으로 다운로드 재시도한다.

    Args:
        scraper: CourseScraper 인스턴스 (scraper.page 필요)
        missing: collect_missing 결과 리스트
        on_progress: (index, total, item, result) 콜백 — UI에서 per-item 진행 표시용.
                     result가 Exception이면 예외 발생, None이면 진입 전 상태.

    Returns:
        RecoveryReport: 성공 카운트와 실패 사유 분포
    """
    from src.ui.download import run_download

    rule = Config.DOWNLOAD_RULE or "both"
    audio_only = rule == "audio"
    both = rule == "both"

    total = len(missing)
    success = 0
    reasons: Counter = Counter()

    for i, item in enumerate(missing, 1):
        label = f"[{item.course.long_name}] {item.lec.title}"
        if on_progress is not None:
            on_progress(i, total, item, None)
        _log.info("복구 중 (%d/%d): %s", i, total, label)

        try:
            result = await run_download(
                scraper.page, item.lec, item.course, audio_only=audio_only, both=both
            )
        except Exception as e:
            _log.error("복구 예외: %s — %s", label, e, exc_info=True)
            reasons[f"exception:{type(e).__name__}"] += 1
            if on_progress is not None:
                on_progress(i, total, item, e)
            continue

        if result.ok:
            success += 1
            _log.info("복구 성공: %s", label)
        else:
            reasons[result.reason or "unknown"] += 1
            _log.warning("복구 실패: %s — reason=%s", label, result.reason)
        if on_progress is not None:
            on_progress(i, total, item, result)

    _log.info("복구 종료: 성공 %d/%d, 실패=%s", success, total, dict(reasons))
    return RecoveryReport(total=total, success=success, failed_by_reason=reasons)
