from pathlib import Path


def unique_unused_path(path: Path) -> Path:
    """Return path if it does not exist; otherwise path_2, path_3, ... that do not exist."""
    path = Path(path)
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_folder_name(raw: str, fallback: str = "Pages") -> str:
    """Make a TOC title safe as a single folder name."""
    text = (raw or "").strip() or fallback
    cleaned_chars: list[str] = []
    for char in text:
        if char in '<>:"/\\|?*' or ord(char) < 32:
            cleaned_chars.append("_")
        else:
            cleaned_chars.append(char)
    cleaned = "".join(cleaned_chars).rstrip(". ").strip() or fallback
    return cleaned[:120]
