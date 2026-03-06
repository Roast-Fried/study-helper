"""
다운로드 관련 UI.

다운로드 경로 설정 및 다운로드 진행률 화면을 제공한다.
"""

from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, SpinnerColumn, TextColumn, TransferSpeedColumn
from rich.prompt import Prompt
from rich.text import Text

from src.config import Config, _default_download_dir

console = Console()


def ask_download_dir() -> str:
    """
    다운로드 경로를 사용자에게 묻고, .env에 저장한 뒤 경로를 반환한다.

    - 저장된 경로가 있으면 바로 반환 (묻지 않음)
    - 없으면 기본 경로를 안내하고 Enter 또는 직접 입력 받음
    """
    if Config.has_download_dir():
        return Config.get_download_dir()

    default_dir = _default_download_dir()

    console.print()
    console.print(Panel(
        Text("다운로드 경로 설정", justify="center", style="bold"),
        border_style="dim",
        padding=(0, 2),
    ))
    console.print()
    console.print(f"  [dim]기본 경로는 다운로드 폴더에 저장됩니다:[/dim]")
    console.print(f"  [cyan]{default_dir}[/cyan]")
    console.print()
    console.print("  [dim]다른 경로를 원하면 입력하고, 기본값을 사용하려면 Enter를 누르세요.[/dim]")
    console.print()

    while True:
        user_input = Prompt.ask("  다운로드 경로", default="").strip()

        save_dir = user_input if user_input else default_dir

        # 경로 유효성 검사
        path = Path(save_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            console.print(f"  [red]경로를 생성할 수 없습니다: {e}[/red]")
            console.print("  [dim]다시 입력해주세요.[/dim]")
            continue

        Config.save_download_dir(str(path.resolve()))
        console.print(f"  [green]저장되었습니다:[/green] {Config.DOWNLOAD_DIR}")
        console.print()
        return Config.DOWNLOAD_DIR


async def run_download(page, lec, course) -> bool:
    """
    강의 영상을 다운로드하고 진행률을 Progress bar로 표시한다.

    Args:
        page:   CourseScraper._page (Playwright Page)
        lec:    다운로드할 LectureItem
        course: 과목 Course (파일명 생성에 사용)

    Returns:
        True: 정상 완료 / False: 오류
    """
    from src.downloader.video_downloader import extract_video_url, download_video_with_browser, make_filename

    console.print()
    console.print(Panel(
        Text(lec.title, justify="center", style="bold cyan"),
        border_style="cyan",
        padding=(0, 4),
    ))
    console.print()

    # 다운로드 경로 확인 (없으면 설정 먼저)
    download_dir = ask_download_dir()

    # 1. video URL 추출
    console.print("  [dim]영상 URL 추출 중...[/dim]")
    video_url = await extract_video_url(page, lec.full_url)
    if not video_url:
        console.print("  [bold red]오류:[/bold red] 영상 URL을 찾지 못했습니다.")
        return False


    # 2. 파일 경로 결정
    filename = make_filename(course.long_name, lec.title)
    save_path = Path(download_dir) / filename

    console.print(f"  [dim]저장 경로: {save_path}[/dim]")
    console.print()

    # 3. 다운로드 + Progress bar
    progress = Progress(
        SpinnerColumn(),
        TextColumn("  [bold]{task.description}"),
        BarColumn(bar_width=36),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        expand=False,
    )
    task_id = progress.add_task(lec.title[:40], total=None)

    success = False
    try:
        with Live(progress, console=console, refresh_per_second=8):
            def on_progress(downloaded: int, total: int):
                progress.update(task_id, completed=downloaded, total=total)

            await download_video_with_browser(page, video_url, save_path, on_progress=on_progress)
        success = True
    except Exception as e:
        console.print(f"  [bold red]다운로드 실패:[/bold red] {e}")
        return False

    console.print()
    if success:
        console.print(f"  [bold green]다운로드 완료![/bold green]")
        console.print(f"  [dim]{save_path}[/dim]")

    return success
