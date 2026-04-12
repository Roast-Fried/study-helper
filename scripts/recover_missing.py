"""누락 다운로드 복구 스크립트 (CLI).

재생(출석)은 완료됐지만 data/downloads에 파일이 없는 강의를 전수 검사한 뒤,
재생 단계를 건너뛴 채 다운로드→mp3→STT→요약 파이프라인만 재실행한다.

사용법:
    python -m scripts.recover_missing            # 대화형 (확인 후 실행)
    python -m scripts.recover_missing --dry-run  # 목록만 출력
    python -m scripts.recover_missing --course <course_id>  # 특정 과목만
    python -m scripts.recover_missing --yes      # 확인 프롬프트 생략

구조적으로 다운로드 불가능한 항목(learningx)은 자동 제외된다.
수집·실행·집계 로직은 src/service/recover_pipeline.py가 단일 소스로 제공한다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ffmpeg PATH 추가 (Windows winget 설치 경로)
_FFMPEG_DIR = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
)
if os.path.isdir(_FFMPEG_DIR):
    os.environ["PATH"] = _FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import Config  # noqa: E402
from src.downloader.result import DownloadResult  # noqa: E402
from src.logger import get_logger  # noqa: E402
from src.scraper.course_scraper import CourseScraper  # noqa: E402
from src.service.recover_pipeline import MissingItem, collect_missing, run_recovery  # noqa: E402

_log = get_logger("recover_missing_cli")


async def main() -> int:
    parser = argparse.ArgumentParser(description="누락 다운로드 복구")
    parser.add_argument("--dry-run", action="store_true", help="목록만 출력하고 종료")
    parser.add_argument("--course", type=str, default=None, help="특정 course_id만 대상")
    parser.add_argument("--yes", "-y", action="store_true", help="확인 프롬프트 생략")
    args = parser.parse_args()

    if not Config.has_credentials():
        print("[오류] LMS 자격증명이 설정되지 않았습니다. .env를 확인하세요.")
        return 1

    # 로컬 Windows 실행 시 Docker 경로 보정
    download_dir = Config.get_download_dir()
    if download_dir.startswith("/data") and sys.platform == "win32":
        local_fallback = Path("data/downloads").resolve()
        local_fallback.mkdir(parents=True, exist_ok=True)
        Config.DOWNLOAD_DIR = str(local_fallback)
        print(f"  [보정] DOWNLOAD_DIR = {Config.DOWNLOAD_DIR}")

    rule = Config.DOWNLOAD_RULE or "both"
    print(f"  다운로드 규칙: {rule}")
    print(f"  다운로드 경로: {Config.get_download_dir()}")
    print()

    scraper = CourseScraper(username=Config.LMS_USER_ID, password=Config.LMS_PASSWORD)
    try:
        print("  LMS 로그인 중...")
        await scraper.start()
        print("  → 로그인 완료")
        print()

        courses = await scraper.fetch_courses()
        if args.course:
            courses = [c for c in courses if c.id == args.course]
            if not courses:
                print(f"[오류] course_id={args.course} 과목을 찾을 수 없습니다.")
                return 1

        print(f"  과목 {len(courses)}개 강의 정보 로딩 중...")
        details = await scraper.fetch_all_details(courses, concurrency=3)

        missing = collect_missing(courses, details)
        if not missing:
            print("  누락된 다운로드가 없습니다.")
            return 0

        print()
        print(f"  누락 {len(missing)}건:")
        for item in missing:
            print(
                f"    - [{item.course.long_name}] {item.lec.week_label} {item.lec.title} ({item.kind})"
            )
        print()

        if args.dry_run:
            print("  --dry-run: 종료")
            return 0

        if not args.yes:
            ans = input(f"  위 {len(missing)}건을 복구하시겠습니까? [y/N] ").strip().lower()
            if ans != "y":
                print("  취소")
                return 0

        def _on_progress(
            index: int, total: int, item: MissingItem, result: DownloadResult | Exception | None
        ) -> None:
            label = f"[{item.course.long_name}] {item.lec.title}"
            if result is None:
                print(f"\n  [{index}/{total}] {label}")
                return
            if isinstance(result, Exception):
                print(f"    → 실패 (예외={type(result).__name__})")
                return
            if result.ok:
                print("    → 성공")
            else:
                print(f"    → 실패 (사유={result.reason})")

        _log.info("CLI 복구 시작: %d건", len(missing))
        report = await run_recovery(scraper, missing, on_progress=_on_progress)

        print()
        print("=" * 60)
        print(f"  복구 결과: 성공 {report.success}/{report.total}")
        if report.failed_by_reason:
            print("  실패 사유 분포:")
            for r, n in report.failed_by_reason.most_common():
                print(f"    {r}: {n}건")
        return 0 if report.success == report.total else 2
    finally:
        await scraper.close()
        try:
            from src.stt.transcriber import unload_model

            unload_model()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
