"""누락 다운로드 복구 파이프라인 — UI/CLI 공용.

`src/ui/recover.py`(TUI 메뉴)와 `scripts/recover_missing.py`(CLI)에서 동일하게 사용한다.
두 진입점은 입력(scraper/courses/details)과 출력(console/stdout) 어댑터만 담당하고,
수집·실행·집계는 이 모듈이 단일 소스로 제공한다.

수집 대상:
- `lec.completion == "completed"` (LMS 기준 출석 완료)
- `lec.is_downloadable` True (learningx 등 구조적 불가는 제외)
- 파일시스템에서 현재 DOWNLOAD_RULE 기준 파일 누락
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.config import Config
from src.downloader.paths import expected_paths
from src.downloader.result import DownloadResult
from src.logger import get_logger

if TYPE_CHECKING:
    from src.scraper.course_scraper import CourseScraper
    from src.scraper.models import Course, CourseDetail, LectureItem

_log = get_logger("recover_pipeline")


@dataclass
class MissingItem:
    course: Course
    lec: LectureItem
    kind: str  # "mp4" / "mp3" / "mp4+mp3"


@dataclass
class RecoveryReport:
    total: int
    success: int
    failed_by_reason: Counter[str] = field(default_factory=Counter)


ProgressCallback = Callable[[int, int, MissingItem, DownloadResult | Exception | None], None]


def collect_missing(
    courses: list[Course],
    details: list[CourseDetail | None],
) -> list[MissingItem]:
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

            mp4, mp3 = expected_paths(download_dir, course.long_name, lec)
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
    scraper: CourseScraper,
    missing: list[MissingItem],
    *,
    on_progress: ProgressCallback | None = None,
) -> RecoveryReport:
    """미싱 항목을 순차적으로 다운로드 재시도한다.

    Args:
        scraper: CourseScraper 인스턴스 (scraper.page 필요)
        missing: collect_missing 결과 리스트
        on_progress: (index, total, item, result) 콜백 — UI에서 per-item 진행 표시용.
                     `result`가 None이면 진입 전, DownloadResult면 완료, Exception이면 예외.
                     콜백 내부 예외는 복구 루프에 영향을 주지 않도록 격리된다(SEC-104).

    Returns:
        RecoveryReport: 성공 카운트와 실패 사유 분포
    """
    from src.ui.download import run_download

    rule = Config.DOWNLOAD_RULE or "both"
    audio_only = rule == "audio"
    both = rule == "both"

    total = len(missing)
    success = 0
    reasons: Counter[str] = Counter()

    def _notify(index: int, item: MissingItem, payload: DownloadResult | Exception | None) -> None:
        if on_progress is None:
            return
        try:
            on_progress(index, total, item, payload)
        except Exception as cb_exc:  # SEC-104: 콜백 예외 격리
            _log.warning("on_progress 콜백 예외 무시: %s", cb_exc)

    for i, item in enumerate(missing, 1):
        label = f"[{item.course.long_name}] {item.lec.title}"
        _notify(i, item, None)
        _log.info("복구 중 (%d/%d): %s", i, total, label)

        try:
            result = await run_download(
                scraper.page, item.lec, item.course, audio_only=audio_only, both=both
            )
        except Exception as e:
            _log.error("복구 예외: %s — %s", label, e, exc_info=True)
            reasons[f"exception:{type(e).__name__}"] += 1
            _notify(i, item, e)
            continue

        if result.ok:
            success += 1
            _log.info("복구 성공: %s", label)
        else:
            reasons[result.reason or "unknown"] += 1
            _log.warning("복구 실패: %s — reason=%s", label, result.reason)
        _notify(i, item, result)

    _log.info("복구 종료: 성공 %d/%d, 실패=%s", success, total, dict(reasons))
    return RecoveryReport(total=total, success=success, failed_by_reason=reasons)
