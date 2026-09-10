from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pymupdf as fitz
from PIL import Image

from safe_paths import sanitize_folder_name, unique_unused_path


MAX_SIDES = (1000, 2000, 3000, 4000, 8000)
MIN_SIDE_PX = 100
MAX_SIDE_PX = 8000

FORMAT_PNG = "png"
FORMAT_JPEG = "jpeg"
FORMAT_WEBP = "webp"
IMAGE_FORMATS = (FORMAT_PNG, FORMAT_JPEG, FORMAT_WEBP)
FORMAT_EXTENSIONS = {
    FORMAT_PNG: ".png",
    FORMAT_JPEG: ".jpg",
    FORMAT_WEBP: ".webp",
}

MIN_QUALITY = 0
MAX_QUALITY = 100
DEFAULT_QUALITY = 85


def validate_max_side(max_side: int) -> int:
    if not isinstance(max_side, int) or isinstance(max_side, bool):
        raise ValueError(f"max_side must be an integer between {MIN_SIDE_PX} and {MAX_SIDE_PX}")
    if max_side < MIN_SIDE_PX or max_side > MAX_SIDE_PX:
        raise ValueError(f"max_side must be between {MIN_SIDE_PX} and {MAX_SIDE_PX}")
    return max_side


def validate_quality(quality: int) -> int:
    if not isinstance(quality, int) or isinstance(quality, bool):
        raise ValueError(f"quality must be an integer between {MIN_QUALITY} and {MAX_QUALITY}")
    if quality < MIN_QUALITY or quality > MAX_QUALITY:
        raise ValueError(f"quality must be between {MIN_QUALITY} and {MAX_QUALITY}")
    return quality


def validate_image_format(image_format: str) -> str:
    key = image_format.strip().lower()
    if key == "jpg":
        key = FORMAT_JPEG
    if key not in IMAGE_FORMATS:
        raise ValueError(f"image_format must be one of {IMAGE_FORMATS}")
    return key


DEFAULT_NAME_PREFIX = "page"


@dataclass(frozen=True)
class ExportSettings:
    image_format: str = FORMAT_PNG
    quality: int = DEFAULT_QUALITY
    webp_lossless: bool = False
    name_prefix: str = DEFAULT_NAME_PREFIX
    overwrite: bool = False

    def normalized(self) -> ExportSettings:
        fmt = validate_image_format(self.image_format)
        quality = validate_quality(self.quality)
        return ExportSettings(
            image_format=fmt,
            quality=quality,
            webp_lossless=bool(self.webp_lossless) if fmt == FORMAT_WEBP else False,
            name_prefix=sanitize_name_prefix(self.name_prefix),
            overwrite=bool(self.overwrite),
        )

    @property
    def extension(self) -> str:
        return FORMAT_EXTENSIONS[validate_image_format(self.image_format)]


def sanitize_name_prefix(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Image name cannot be empty.")
    cleaned_chars: list[str] = []
    for char in text:
        if char in '<>:"/\\|?*' or ord(char) < 32:
            cleaned_chars.append("_")
        else:
            cleaned_chars.append(char)
    cleaned = "".join(cleaned_chars).rstrip(". ")
    if not cleaned:
        raise ValueError(
            "Image name must contain letters or numbers "
            "(avoid only spaces or punctuation)."
        )
    return cleaned


@dataclass(frozen=True)
class TocEntry:
    index: int
    level: int
    title: str
    start_page: int
    end_page: int


@dataclass(frozen=True)
class PageInfo:
    page: int
    width_pts: float
    height_pts: float
    landscape: bool


@dataclass(frozen=True)
class RenderedPage:
    page: int
    path: Path
    width_px: int
    height_px: int
    section: str


def parse_page_ranges(spec: str, page_count: int) -> list[int]:
    pages: set[int] = set()

    def parse_page_token(token: str) -> int:
        text = token.strip()
        if not text or not text.isdigit():
            raise ValueError(
                f"Invalid page value '{token.strip()}'. "
                "Use whole page numbers or ranges (example: 1-4, 9)."
            )
        return int(text)

    for raw in spec.replace(";", ",").split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = parse_page_token(left)
            end = parse_page_token(right)
            if start > end:
                start, end = end, start
            for page in range(start, end + 1):
                if 1 <= page <= page_count:
                    pages.add(page)
        else:
            page = parse_page_token(part)
            if 1 <= page <= page_count:
                pages.add(page)
    return sorted(pages)


def toc_entries(doc: fitz.Document) -> list[TocEntry]:
    raw = doc.get_toc(simple=True) or []
    page_count = doc.page_count
    entries: list[TocEntry] = []
    for index, item in enumerate(raw):
        level, title, start_page = int(item[0]), str(item[1]), int(item[2])
        start_page = max(1, min(start_page, page_count))
        end_page = page_count
        for later in raw[index + 1 :]:
            later_level, _, later_page = int(later[0]), later[1], int(later[2])
            if later_level <= level:
                end_page = max(start_page, int(later_page) - 1)
                break
        entries.append(
            TocEntry(
                index=index,
                level=level,
                title=title.strip() or f"Section {index + 1}",
                start_page=start_page,
                end_page=end_page,
            )
        )
    return entries


def page_infos(doc: fitz.Document) -> list[PageInfo]:
    infos: list[PageInfo] = []
    for index in range(doc.page_count):
        rect = doc[index].rect
        infos.append(
            PageInfo(
                page=index + 1,
                width_pts=float(rect.width),
                height_pts=float(rect.height),
                landscape=rect.width > rect.height,
            )
        )
    return infos


def scaled_page_pixels(
    width_pts: float, height_pts: float, max_side: int
) -> tuple[int, int]:
    """Pixel size after scaling the longest side to max_side (matches render scale)."""
    max_side = validate_max_side(max_side)
    longest = max(float(width_pts), float(height_pts))
    if longest <= 0:
        return 1, 1
    scale = max_side / longest
    return (
        max(1, int(width_pts * scale + 0.5)),
        max(1, int(height_pts * scale + 0.5)),
    )


def layout_measure_pages(
    doc: fitz.Document,
    pages: list[int],
    max_side: int,
) -> list[RenderedPage]:
    """Page sizes + bookmark sections for layout preview (no rasterization)."""
    max_side = validate_max_side(max_side)
    entries = toc_entries(doc)
    measured: list[RenderedPage] = []
    for page_number in pages:
        rect = doc[page_number - 1].rect
        width_px, height_px = scaled_page_pixels(
            float(rect.width), float(rect.height), max_side
        )
        measured.append(
            RenderedPage(
                page=page_number,
                path=Path("."),
                width_px=width_px,
                height_px=height_px,
                section=section_for_page(entries, page_number),
            )
        )
    return measured


def section_for_page(entries: list[TocEntry], page: int) -> str:
    """Assign a board section title for a page from the PDF TOC.

    Uses the deepest matching bookmark (leaf). When the TOC is nested, groups
    by that leaf's immediate parent. When the TOC is flat, keeps the leaf title.
    """
    matching = [entry for entry in entries if entry.start_page <= page <= entry.end_page]
    if not matching:
        return "Pages"
    matching.sort(key=lambda entry: (-entry.level, entry.start_page, entry.index))
    leaf = matching[0]
    parents = [entry for entry in matching if entry.level < leaf.level]
    if not parents:
        return leaf.title
    parents.sort(key=lambda entry: (-entry.level, entry.start_page, entry.index))
    return parents[0].title


def _pixmap_to_rgb_image(pixmap: fitz.Pixmap) -> Image.Image:
    if pixmap.alpha:
        pixmap = fitz.Pixmap(pixmap, 0)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _save_image(image: Image.Image, dest: Path, settings: ExportSettings) -> None:
    settings = settings.normalized()
    fmt = settings.image_format
    if fmt == FORMAT_PNG:
        image.save(dest, format="PNG", compress_level=6)
    elif fmt == FORMAT_JPEG:
        image.save(dest, format="JPEG", quality=settings.quality, optimize=True)
    else:
        if settings.webp_lossless:
            image.save(dest, format="WEBP", lossless=True)
        else:
            image.save(dest, format="WEBP", quality=settings.quality)


class PageRenderCancelled(Exception):
    """Raised when cancel is requested while rendering a single page."""


# Check cancel about this often while rasterizing a page (pixel rows).
_RENDER_BAND_PX = 512


def _render_page_rgb(
    page: fitz.Page,
    max_side: int,
    should_cancel: Callable[[], bool] | None = None,
) -> Image.Image:
    """Rasterize a page in horizontal bands so cancel can stop mid-page."""
    rect = page.rect
    longest = max(rect.width, rect.height)
    scale = max_side / longest
    mat = fitz.Matrix(scale, scale)
    out_h = max(1, int(rect.height * scale + 0.5))
    band_pts = max(rect.height * (_RENDER_BAND_PX / out_h), 1e-3)

    bands: list[Image.Image] = []
    y0 = float(rect.y0)
    while y0 < rect.y1 - 1e-6:
        if should_cancel and should_cancel():
            raise PageRenderCancelled()
        y1 = min(y0 + band_pts, float(rect.y1))
        clip = fitz.Rect(rect.x0, y0, rect.x1, y1)
        pixmap = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        bands.append(_pixmap_to_rgb_image(pixmap))
        y0 = y1

    if should_cancel and should_cancel():
        raise PageRenderCancelled()

    if len(bands) == 1:
        return bands[0]

    width = max(band.width for band in bands)
    height = sum(band.height for band in bands)
    image = Image.new("RGB", (width, height))
    y_px = 0
    for band in bands:
        if band.width != width:
            padded = Image.new("RGB", (width, band.height))
            padded.paste(band, (0, 0))
            band = padded
        image.paste(band, (0, y_px))
        y_px += band.height
    return image


def render_page_image(
    doc: fitz.Document,
    page_number: int,
    dest_dir: Path,
    max_side: int,
    settings: ExportSettings | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[Path, int, int]:
    settings = (settings or ExportSettings()).normalized()
    max_side = validate_max_side(max_side)
    if should_cancel and should_cancel():
        raise PageRenderCancelled()
    page = doc[page_number - 1]
    image = _render_page_rgb(page, max_side, should_cancel=should_cancel)
    if should_cancel and should_cancel():
        raise PageRenderCancelled()
    target = dest_dir / f"{settings.name_prefix}_{page_number:04d}{settings.extension}"
    dest = target if settings.overwrite else unique_unused_path(target)
    _save_image(image, dest, settings)
    return dest, image.width, image.height


def render_page_png(
    doc: fitz.Document,
    page_number: int,
    dest_dir: Path,
    max_side: int,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[Path, int, int]:
    """Backward-compatible PNG render used by PureRef import."""
    return render_page_image(
        doc,
        page_number,
        dest_dir,
        max_side,
        ExportSettings(image_format=FORMAT_PNG),
        should_cancel=should_cancel,
    )


class RenderCancelled(Exception):
    """Raised when the user cancels mid-render. Partial pages are attached."""

    def __init__(self, rendered: list[RenderedPage]):
        super().__init__("Canceled by user")
        self.rendered = rendered


def render_selected_pages(
    pdf_path: Path,
    pages: list[int],
    dest_dir: Path,
    max_side: int,
    settings: ExportSettings | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    toc_subfolders: bool = False,
) -> list[RenderedPage]:
    # When called from a worker thread, on_progress / should_cancel must be
    # thread-safe (no Tk widget access); marshal UI updates on the main thread.
    settings = (settings or ExportSettings()).normalized()
    dest_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[RenderedPage] = []
    total = len(pages)
    section_folders: dict[str, Path] = {}
    used_folder_names: set[str] = set()
    with fitz.open(pdf_path) as doc:
        entries = toc_entries(doc)
        for index, page_number in enumerate(pages, start=1):
            if should_cancel and should_cancel():
                raise RenderCancelled(rendered)
            if on_progress:
                on_progress(index, total)
            section = section_for_page(entries, page_number)
            page_dir = dest_dir
            if toc_subfolders:
                if section not in section_folders:
                    base_name = sanitize_folder_name(section)
                    name = base_name
                    suffix = 2
                    while name.casefold() in used_folder_names:
                        name = f"{base_name}_{suffix}"
                        suffix += 1
                    used_folder_names.add(name.casefold())
                    folder = dest_dir / name
                    folder.mkdir(parents=True, exist_ok=True)
                    section_folders[section] = folder
                page_dir = section_folders[section]
            try:
                path, width, height = render_page_image(
                    doc,
                    page_number,
                    page_dir,
                    max_side,
                    settings,
                    should_cancel=should_cancel,
                )
            except PageRenderCancelled:
                raise RenderCancelled(rendered) from None
            rendered.append(
                RenderedPage(
                    page=page_number,
                    path=path,
                    width_px=width,
                    height_px=height,
                    section=section,
                )
            )
    return rendered
