"""공용 TUI 위젯 (ARCH-012).

여러 UI 화면에서 동일한 `Panel(Text(... justify="center" ...))` 헤더 렌더링을
복제하던 것을 단일 헬퍼로 수렴. 스타일 튜닝 시 여기만 수정.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text


def header_panel(
    title: str,
    *,
    style: str = "bold cyan",
    border_style: str = "cyan",
    padding: tuple[int, int] = (0, 4),
) -> Panel:
    """중앙 정렬 타이틀을 담은 헤더 Panel 을 반환한다."""
    return Panel(
        Text(title, justify="center", style=style),
        border_style=border_style,
        padding=padding,
    )
