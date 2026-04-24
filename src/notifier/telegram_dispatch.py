"""Telegram credential 체크 + 알림 디스패처.

ARCH-004: 이전에는 7+ 호출 사이트가 각자 `creds = Config.get_telegram_credentials();
if not creds: return; token, chat_id = creds; notify_xxx(token, chat_id, ...)` 보일러플레이트를 반복했다.
본 모듈은 한 줄 호출로 축약한다.

사용 예:
    from src.notifier.telegram_notifier import notify_playback_complete
    from src.notifier.telegram_dispatch import dispatch_if_configured

    dispatch_if_configured(
        notify_playback_complete,
        course_name=course.long_name,
        week_label=lec.week_label,
        lecture_title=lec.title,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.config import Config
from src.logger import get_logger

_log = get_logger("notifier.dispatch")


def dispatch_if_configured(notify_fn: Callable[..., Any], /, **kwargs: Any) -> Any:
    """Telegram 자격증명이 있으면 notify_fn 을 호출하고, 없으면 no-op.

    `notify_fn` 은 `bot_token=...`, `chat_id=...` 를 포함한 키워드 인자들을
    받는다는 프로토콜을 가진다. 호출자는 `course_name` 같은 비-credential
    인자만 전달하면 되고, 이 함수가 token/chat_id 를 주입한다.

    Returns:
        notify_fn 의 반환값. 미설정 시 None.
    """
    creds = Config.get_telegram_credentials()
    if not creds:
        return None
    token, chat_id = creds
    try:
        return notify_fn(bot_token=token, chat_id=chat_id, **kwargs)
    except Exception as e:
        _log.warning("텔레그램 알림 실패: %s: %s", type(e).__name__, e)
        return None
