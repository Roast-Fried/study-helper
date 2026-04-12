from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

_BASE_URL = "https://canvas.ssu.ac.kr"


class LectureType(Enum):
    MOVIE = "movie"
    READYSTREAM = "readystream"
    SCREENLECTURE = "screenlecture"
    EVERLEC = "everlec"
    ZOOM = "zoom"
    MP4 = "mp4"
    ASSIGNMENT = "assignment"
    WIKI_PAGE = "wiki_page"
    QUIZ = "quiz"
    DISCUSSION = "discussion"
    FILE = "file"
    OTHER = "other"


VIDEO_LECTURE_TYPES = {
    LectureType.MOVIE,
    LectureType.READYSTREAM,
    LectureType.SCREENLECTURE,
    LectureType.EVERLEC,
    LectureType.MP4,
}


@dataclass
class Course:
    id: str
    long_name: str
    href: str
    term: str
    is_favorited: bool = False

    @property
    def full_url(self) -> str:
        return f"{_BASE_URL}{self.href}"

    @property
    def lectures_url(self) -> str:
        return f"{_BASE_URL}/courses/{self.id}/external_tools/71"


@dataclass
class LectureItem:
    title: str
    item_url: str
    lecture_type: LectureType
    week_label: str = ""
    lesson_label: str = ""
    duration: str | None = None
    attendance: str = "none"
    completion: str = "incomplete"
    content_type_label: str = ""
    is_upcoming: bool = False
    start_date: str | None = None
    end_date: str | None = None

    @property
    def is_video(self) -> bool:
        return self.lecture_type in VIDEO_LECTURE_TYPES

    @property
    def full_url(self) -> str:
        if self.item_url.startswith("http"):
            return self.item_url
        return f"{_BASE_URL}{self.item_url}"

    @property
    def needs_watch(self) -> bool:
        return self.is_video and self.completion != "completed" and not self.is_upcoming

    @property
    def is_downloadable(self) -> bool:
        """구조적으로 다운로드 가능한지 여부.

        learningx 플레이어는 mp4/HLS URL 노출이 없어 다운로드 불가.
        이 판정은 URL 패턴만으로 수행되며, 실제 추출 실패는 별도 경로에서 처리.
        """
        return self.is_video and "learningx" not in self.full_url

    def expected_paths(self, download_dir: str | Path, course_long_name: str) -> tuple[Path, Path]:
        """다운로드 규칙 계산에 필요한 (mp4, mp3) 경로를 반환한다.

        경로 구조 `과목명/N주차/강의명.mp4`는 downloader.make_filepath가 단일 진입점으로
        유지하되, 호출 쪽 레이어에서 import 중복을 없애기 위해 모델 메서드로 래핑한다.
        """
        from src.downloader.video_downloader import make_filepath

        mp4_rel = make_filepath(course_long_name, self.week_label, self.title)
        mp4 = (Path(download_dir) / mp4_rel).resolve()
        mp3 = mp4.with_suffix(".mp3")
        return mp4, mp3

    def file_present(self, download_dir: str | Path, course_long_name: str, rule: str) -> bool:
        """DOWNLOAD_RULE에 따라 기대되는 파일이 모두 존재하는지 확인한다."""
        mp4, mp3 = self.expected_paths(download_dir, course_long_name)
        if rule == "video":
            return mp4.exists()
        if rule == "audio":
            return mp3.exists()
        if rule == "both":
            return mp4.exists() and mp3.exists()
        # 규칙 미설정 — 둘 중 하나만 있어도 present 간주
        return mp4.exists() or mp3.exists()


@dataclass
class Week:
    title: str
    week_number: int
    lectures: list[LectureItem] = field(default_factory=list)

    @property
    def video_lectures(self) -> list[LectureItem]:
        return [lec for lec in self.lectures if lec.is_video]

    @property
    def pending_count(self) -> int:
        return sum(1 for lec in self.lectures if lec.needs_watch)


@dataclass
class CourseDetail:
    course: Course
    course_name: str
    professors: str
    weeks: list[Week] = field(default_factory=list)

    @property
    def all_video_lectures(self) -> list[LectureItem]:
        result = []
        for week in self.weeks:
            result.extend(week.video_lectures)
        return result

    @property
    def total_video_count(self) -> int:
        return len(self.all_video_lectures)

    @property
    def pending_video_count(self) -> int:
        return sum(1 for lec in self.all_video_lectures if lec.needs_watch)
