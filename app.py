"""
CreavFlow.

Option A) Import to PureRef (.pur) — render pages in a hidden temp folder, write a .pur, then open it
Option B) Export to PNG / JPEG / WebP — save page images to a chosen folder

Run:
  .venv\\Scripts\\python app.py

Windows exe:
  .venv\\Scripts\\python -m PyInstaller --noconfirm CreavFlow.spec
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import ttkbootstrap as tb
import pymupdf as fitz
from PIL import Image, ImageFont

from pdf_capture import (
    DEFAULT_QUALITY,
    FORMAT_EXTENSIONS,
    FORMAT_JPEG,
    FORMAT_PNG,
    FORMAT_WEBP,
    MAX_SIDES,
    MAX_SIDE_PX,
    MIN_QUALITY,
    MAX_QUALITY,
    MIN_SIDE_PX,
    ExportSettings,
    RenderCancelled,
    RenderedPage,
    layout_measure_pages,
    parse_page_ranges,
    page_infos,
    render_selected_pages,
    validate_max_side,
)
from purformat.items import PurGraphicsImageItem, PurGraphicsTextItem, PurImage
from purformat.purformat import PurFile
from safe_paths import ensure_dir, unique_unused_path

TITLE_HEIGHT = 180
TITLE_SCALE = 8.0
# Fallback average glyph width if system fonts are unavailable.
TITLE_CHAR_WIDTH = 10.0


def estimate_title_width(text: str) -> float:
    """Estimate on-canvas width of a PureRef note (center-origin, scaled text)."""
    sample = text.strip() or " "
    font_size = max(1, int(round(12 * TITLE_SCALE)))
    font_paths = (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    try:
        font = None
        for path in font_paths:
            if path.is_file():
                font = ImageFont.truetype(str(path), font_size)
                break
        if font is None:
            font = ImageFont.load_default()
        left, _top, right, _bottom = font.getbbox(sample)
        return float(max(right - left, 1))
    except Exception:
        return max(len(sample), 1) * TITLE_CHAR_WIDTH * TITLE_SCALE

APP_NAME = "CreavFlow"
APP_SHORT_NAME = "CreavFlow"
APP_VERSION = "1.1.1"
APP_AUTHOR = "wyeejun"
APP_THEME = "everforest-dark"
CHECK_STYLE = "primary-round-toggle"
RADIO_STYLE = "primary"
SCROLL_STYLE = "round"
SCALE_STYLE = "primary"
PATH_BTN_WIDTH = 14  # Choose PDF / Save as… / Output folder
RUN_BTN_WIDTH = 8  # Run / Cancel
PREVIEW_BTN_WIDTH = 10
PREVIEW_CANVAS_W = 320
PREVIEW_CANVAS_H = 200
PREVIEW_PAD = 8
ROW_PADY = 6
PDF_PATH_MAX_CHARS = 52
# README dialog: height fixed; width fits longest Features line (clamped to screen).
WINDOW_SCREEN_MARGIN_W = 40
WINDOW_SCREEN_MARGIN_H = 80
README_WINDOW_MIN_W = 420
README_WINDOW_H = 520
# Body padding, scrollbar, text widget inset, and a little slack so Features don't wrap.
README_WINDOW_CHROME_W = 120
README_FONT = ("Consolas", 10)
GITHUB_URL = "https://github.com/wyeejun88/CreavFlow_release"
ISSUES_URL = f"{GITHUB_URL}/issues"
SPONSOR_URL = "https://ko-fi.com/wyeejun"


def _screen_usable(widget: tk.Misc) -> tuple[int, int]:
    """Max window size that should fit on the current display."""
    return (
        max(640, widget.winfo_screenwidth() - WINDOW_SCREEN_MARGIN_W),
        max(480, widget.winfo_screenheight() - WINDOW_SCREEN_MARGIN_H),
    )


def _readme_features_lines(content: str) -> list[str]:
    """Non-empty lines under ## Features until the next ## heading."""
    lines: list[str] = []
    in_features = False
    for line in content.splitlines():
        if line.startswith("## "):
            if in_features:
                break
            if line.strip().lower() == "## features":
                in_features = True
            continue
        if in_features and line.strip():
            lines.append(line.rstrip())
    return lines


def _readme_width_for_features(content: str, text_widget: tk.Text) -> int:
    """Window width sized to the longest ## Features line (Use section ignored)."""
    from tkinter import font as tkfont

    feature_lines = _readme_features_lines(content)
    measure = tkfont.Font(font=text_widget.cget("font"))
    max_px = max((measure.measure(line) for line in feature_lines), default=0)
    return max(README_WINDOW_MIN_W, max_px + README_WINDOW_CHROME_W)


def _place_fixed_window(
    win: tk.Misc,
    width: int,
    height: int,
    *,
    anchor: tk.Misc | None = None,
) -> None:
    """Size and center a non-resizable window; clamp to the usable screen."""
    max_w, max_h = _screen_usable(win)
    width = min(max(1, width), max_w)
    height = min(max(1, height), max_h)
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    if anchor is not None:
        try:
            x = anchor.winfo_rootx() + (anchor.winfo_width() - width) // 2
            y = anchor.winfo_rooty() + (anchor.winfo_height() - height) // 2
        except tk.TclError:
            x = (screen_w - width) // 2
            y = (screen_h - height) // 2
    else:
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
    x = max(0, min(x, screen_w - width))
    y = max(0, min(y, screen_h - height))
    win.resizable(False, False)
    win.geometry(f"{width}x{height}+{x}+{y}")

BMC_BUTTON_TEXT = "Buy me a coffee"

DEFAULT_MAX_PER_ROW = 8
DEFAULT_IMAGE_SPACING_PCT = 4.0
DEFAULT_GROUP_SPACING_PCT = 30.0
MIN_PER_ROW = 1
MAX_PER_ROW = 20
MIN_SPACING_PCT = 0.0
MAX_IMAGE_SPACING_PCT = 50.0
MAX_GROUP_SPACING_PCT = 100.0

MODE_IMPORT = "import"
MODE_EXTRACT = "extract"
WEBP_LOSSY = "lossy"
WEBP_LOSSLESS = "lossless"


@dataclass(frozen=True)
class LayoutSettings:
    max_per_row: int = DEFAULT_MAX_PER_ROW
    image_spacing_pct: float = DEFAULT_IMAGE_SPACING_PCT
    group_spacing_pct: float = DEFAULT_GROUP_SPACING_PCT

    def gaps_for(self, max_side: int) -> tuple[float, float]:
        image_gap = max_side * (self.image_spacing_pct / 100.0)
        group_gap = max_side * (self.group_spacing_pct / 100.0)
        return image_gap, group_gap


@dataclass(frozen=True)
class PlacedItem:
    path: Path | None
    x: float
    y: float
    width: int
    height: int
    name: str
    is_title: bool = False


def bundle_dir() -> Path:
    """App root for source runs, or PyInstaller extract dir when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """Writable per-user folder for settings (not the frozen bundle)."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return user_data_dir() / "settings.json"


def load_settings() -> dict:
    path = settings_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    path = settings_file()
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def shorten_display_path(path: Path, max_chars: int = PDF_PATH_MAX_CHARS) -> str:
    """Shorten a filesystem path for a single-line label."""
    text = str(path)
    if len(text) <= max_chars:
        return text
    name = path.name
    if len(name) >= max_chars:
        return name[: max_chars - 1] + "…"
    # Keep filename; truncate the parent with a leading ellipsis.
    budget = max_chars - len(name) - 1
    parent = str(path.parent)
    if len(parent) <= budget:
        return text
    return "…" + parent[-(budget - 1) :] + (os.sep if not parent.endswith(os.sep) else "") + name


def resolve_output_path(path: Path, overwrite: bool) -> Path:
    path = Path(path)
    if path.exists() and not overwrite:
        return unique_unused_path(path)
    return path


def default_output_for_new(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".pur")


def default_capture_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(f"{pdf_path.stem}_captures")


def _group_by_section(pages: list[RenderedPage]) -> list[tuple[str, list[RenderedPage]]]:
    groups: list[tuple[str, list[RenderedPage]]] = []
    for page in pages:
        if not groups or groups[-1][0] != page.section:
            groups.append((page.section, [page]))
        else:
            groups[-1][1].append(page)
    return groups


def layout_items(
    pages: list[RenderedPage],
    settings: LayoutSettings | None = None,
    max_side: int | None = None,
    group_by_bookmarks: bool = True,
) -> list[PlacedItem]:
    layout = settings or LayoutSettings()
    if max_side is None:
        max_side = max((max(p.width_px, p.height_px) for p in pages), default=2000)
    image_gap, group_gap = layout.gaps_for(max_side)
    max_per_row = max(MIN_PER_ROW, layout.max_per_row)

    if group_by_bookmarks:
        sections = _group_by_section(pages)
    else:
        sections = [("", pages)]

    placed: list[PlacedItem] = []
    y_cursor = 0.0
    for section, group in sections:
        if not group:
            continue
        if section:
            title_y = y_cursor + TITLE_HEIGHT / 2.0
            y_cursor += TITLE_HEIGHT + image_gap
        else:
            title_y = y_cursor

        # Lay out images first so we can pin the note to the group's left edge.
        section_images: list[PlacedItem] = []
        group_left = 0.0
        for row_start in range(0, len(group), max_per_row):
            row = group[row_start : row_start + max_per_row]
            row_height = max(page.height_px for page in row)
            x_cursor = 0.0
            for page in row:
                section_images.append(
                    PlacedItem(
                        path=page.path,
                        x=x_cursor + page.width_px / 2.0,
                        y=y_cursor + page.height_px / 2.0,
                        width=page.width_px,
                        height=page.height_px,
                        name=f"page_{page.page:04d}",
                    )
                )
                x_cursor += page.width_px + image_gap
            y_cursor += row_height + image_gap
        if section_images:
            group_left = min(item.x - item.width / 2.0 for item in section_images)

        if section:
            # PureRef text x/y is the center point; offset by half width so the
            # left edge of the note lines up with the furthest-left image.
            title_width = estimate_title_width(section)
            placed.append(
                PlacedItem(
                    path=None,
                    x=group_left + title_width / 2.0,
                    y=title_y,
                    width=int(round(title_width)),
                    height=TITLE_HEIGHT,
                    name=section,
                    is_title=True,
                )
            )
        placed.extend(section_images)
        if group_by_bookmarks:
            y_cursor += group_gap - image_gap
    return placed


def _png_bytes(path: Path) -> bytes:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        buffer = BytesIO()
        rgb.save(buffer, format="PNG", compress_level=6)
        return buffer.getvalue()


def find_pureref_exe() -> Path | None:
    """Return PureRef.exe if found on PATH or in common install folders."""
    for name in ("PureRef.exe", "PureRef"):
        found = shutil.which(name)
        if found:
            return Path(found)

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "PureRef"
        / "PureRef.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "PureRef"
        / "PureRef.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PureRef" / "PureRef.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "PureRef" / "PureRef.exe",
        Path(os.environ.get("APPDATA", "")) / "PureRef" / "PureRef.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def confirm_run_without_pureref(parent: tk.Tk) -> bool:
    """Warn that PureRef is missing. True = Run anyway, False = Cancel."""
    result = {"continue": False}
    win = tb.Toplevel(parent)
    win.title("PureRef is not installed")
    win.transient(parent)
    win.resizable(False, False)
    win.grab_set()

    frame = ttk.Frame(win, padding=16)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        frame,
        text=(
            "PureRef is not installed.\n\n"
            "You can still create the .pur file, but it may not open automatically."
        ),
        justify=tk.LEFT,
    ).pack(anchor=tk.W)

    buttons = ttk.Frame(frame)
    buttons.pack(fill=tk.X, pady=(16, 0))

    def terminate():
        result["continue"] = False
        win.destroy()

    def run_anyway():
        result["continue"] = True
        win.destroy()

    ttk.Button(buttons, text="Cancel", command=terminate).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="Run anyway", command=run_anyway).pack(
        side=tk.RIGHT, padx=(0, 8)
    )

    win.protocol("WM_DELETE_WINDOW", terminate)
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    parent.wait_window(win)
    return bool(result["continue"])


def open_pur_file(path: Path) -> None:
    """Open a .pur with the OS default app (PureRef when associated)."""
    path = Path(path).resolve()
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    raise OSError(f"Cannot open {path} on this platform")


def open_folder(path: Path) -> None:
    """Open a folder in the OS file manager."""
    path = Path(path).resolve()
    if not path.is_dir():
        path = path.parent
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    raise OSError(f"Cannot open folder {path} on this platform")


def write_new_pur(placed: list[PlacedItem], output_path: Path, overwrite: bool) -> Path:
    dest = resolve_output_path(output_path, overwrite=overwrite)
    if dest.exists() and not overwrite:
        dest = unique_unused_path(dest)

    pur = PurFile()
    min_x = min_y = 0.0
    max_x = max_y = 0.0
    images: list[PurImage] = []
    notes: list[PurGraphicsTextItem] = []

    for item in placed:
        left = item.x - item.width / 2.0
        top = item.y - item.height / 2.0
        right = item.x + item.width / 2.0
        bottom = item.y + item.height / 2.0
        min_x = min(min_x, left)
        min_y = min(min_y, top)
        max_x = max(max_x, right)
        max_y = max(max_y, bottom)

        if item.is_title:
            note = PurGraphicsTextItem()
            note.text = item.name
            note.x = item.x
            note.y = item.y
            note.matrix = [TITLE_SCALE, 0.0, 0.0, TITLE_SCALE]
            note.opacityBackground = 0
            note.zLayer = 2.0
            notes.append(note)
            continue

        if item.path is None:
            raise ValueError(f"Image item has no path: {item.name}")
        pur_image = PurImage()
        pur_image.pngBinary = bytearray(_png_bytes(item.path))
        transform = PurGraphicsImageItem()
        transform.reset_crop(item.width, item.height)
        transform.name = item.name
        transform.source = item.name
        transform.x = item.x
        transform.y = item.y
        transform.zLayer = 1.0
        pur_image.transforms = [transform]
        images.append(pur_image)

    pur.images = images
    pur.text = notes
    pad = 500.0
    pur.canvas = [min_x - pad, min_y - pad, max_x + pad, max_y + pad]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        dest = unique_unused_path(dest)
    pur.write(str(dest))
    return dest


class App(tb.Window):
    def __init__(self):
        super().__init__(
            title=APP_SHORT_NAME,
            theme=APP_THEME,
            size=(960, 860),
        )
        self.pdf_path: Path | None = None
        self.page_count = 0
        self._syncing_quality = False
        self._cancel_event = threading.Event()
        self._ui_queue: queue.Queue = queue.Queue()
        self._busy = False
        self._settings = load_settings()
        self._pdf_tip: tb.ToolTip | None = None

        self._build()
        self._apply_settings()
        self._sync_mode_ui()
        self._set_busy(False)
        self._fit_window_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _make_section(self, parent: ttk.Frame) -> tuple[tk.Frame, ttk.Frame]:
        """Untitled bordered box: continuous outline (avoids LabelFrame title gap)."""
        shell = tk.Frame(parent, background=self.style.colors.border, bd=0)
        body = ttk.Frame(shell, padding=10)
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        return shell, body

    def _build(self):
        pad = {"padx": 10, "pady": (0, 10)}
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._build_footer(root).pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))

        # Settings (locked while a job runs). Run / Cancel / log stay outside.
        self._settings_zone = ttk.Frame(root)
        self._settings_zone.pack(fill=tk.X)

        pdf_shell, pdf_box = self._make_section(self._settings_zone)
        pdf_shell.pack(fill=tk.X, **pad)
        pdf_box.columnconfigure(1, weight=1)
        label_opts = {"sticky": tk.W, "pady": ROW_PADY}

        self._choose_pdf_btn = ttk.Button(
            pdf_box, text="Choose PDF", command=self.choose_pdf, width=PATH_BTN_WIDTH
        )
        self._choose_pdf_btn.grid(row=0, column=0, sticky=tk.W, pady=ROW_PADY)
        self.pdf_label = ttk.Label(pdf_box, text="No PDF selected")
        self.pdf_label.grid(row=0, column=1, sticky=tk.W, pady=ROW_PADY)
        self._pdf_tip = tb.ToolTip(self.pdf_label, text="", wraplength=420)

        self.info_label = ttk.Label(pdf_box, text="")
        self.info_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, ROW_PADY))

        ttk.Label(pdf_box, text="Pages").grid(row=2, column=0, **label_opts)
        range_controls = ttk.Frame(pdf_box)
        range_controls.grid(row=2, column=1, sticky=tk.EW, pady=ROW_PADY)
        range_controls.columnconfigure(0, weight=1)
        self.range_var = tk.StringVar()
        ttk.Entry(range_controls, textvariable=self.range_var).grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 8)
        )
        ttk.Button(
            range_controls, text="All pages", command=self.select_all_pages
        ).grid(row=0, column=1, sticky=tk.E)

        ttk.Label(pdf_box, text="Max side (px)").grid(
            row=3, column=0, **label_opts
        )
        size_controls = ttk.Frame(pdf_box)
        size_controls.grid(row=3, column=1, sticky=tk.W, pady=ROW_PADY)
        self.max_side_var = tk.StringVar(value="2000")
        ttk.Entry(size_controls, textvariable=self.max_side_var, width=8).pack(
            side=tk.LEFT
        )
        for value in MAX_SIDES:
            ttk.Button(
                size_controls,
                text=str(value),
                command=lambda v=value: self.max_side_var.set(str(v)),
                width=5,
            ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            size_controls,
            text=f"({MIN_SIDE_PX}–{MAX_SIDE_PX})",
        ).pack(side=tk.LEFT, padx=(8, 0))

        self.mode_var = tk.StringVar(value=MODE_IMPORT)

        mode_shell, mode_box = self._make_section(self._settings_zone)
        mode_shell.pack(fill=tk.X, **pad)

        mode_row = ttk.Frame(mode_box)
        mode_row.pack(fill=tk.X, pady=(0, ROW_PADY))
        tb.Radiobutton(
            mode_row,
            text="Import to PureRef (.pur)",
            value=MODE_IMPORT,
            variable=self.mode_var,
            command=self._sync_mode_ui,
            bootstyle=RADIO_STYLE,
        ).pack(side=tk.LEFT, padx=(0, 20))
        tb.Radiobutton(
            mode_row,
            text="Export to PNG / JPEG / WebP",
            value=MODE_EXTRACT,
            variable=self.mode_var,
            command=self._sync_mode_ui,
            bootstyle=RADIO_STYLE,
        ).pack(side=tk.LEFT)

        self.import_panel = ttk.Frame(mode_box)
        self.import_panel.columnconfigure(1, weight=1)
        self.import_panel.columnconfigure(2, weight=1)
        self.import_panel.rowconfigure(6, weight=1)

        save_row = ttk.Frame(self.import_panel)
        save_row.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=ROW_PADY)
        save_row.columnconfigure(1, weight=1)
        self.save_pur_btn = ttk.Button(
            save_row,
            text="Save as…",
            command=self.choose_output,
            width=PATH_BTN_WIDTH,
        )
        self.save_pur_btn.grid(row=0, column=0, sticky=tk.W)
        self.output_var = tk.StringVar()
        self.output_entry = ttk.Entry(save_row, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=1, sticky=tk.EW)

        self.overwrite = tk.BooleanVar(value=False)
        self.overwrite_check = tb.Checkbutton(
            self.import_panel,
            text="Allow overwriting existing .pur",
            variable=self.overwrite,
            bootstyle=CHECK_STYLE,
        )
        self.overwrite_check.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=ROW_PADY)

        self.bookmark_groups_var = tk.BooleanVar(value=True)
        self.bookmark_groups_check = tb.Checkbutton(
            self.import_panel,
            text="Organized by PDF bookmarks",
            variable=self.bookmark_groups_var,
            command=self._on_bookmark_groups_toggle,
            bootstyle=CHECK_STYLE,
        )
        self.bookmark_groups_check.grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=ROW_PADY
        )

        self.advanced_var = tk.BooleanVar(value=False)
        self.advanced_check = tb.Checkbutton(
            self.import_panel,
            text="Custom layout",
            variable=self.advanced_var,
            command=self._on_advanced_toggle,
            bootstyle=CHECK_STYLE,
        )
        self.advanced_check.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=ROW_PADY)

        self.max_per_row_label = ttk.Label(self.import_panel, text="Max images per row")
        self.max_per_row_label.grid(
            row=4, column=0, sticky=tk.W, padx=(0, 12), pady=ROW_PADY
        )
        self.max_per_row_var = tk.StringVar(value=str(DEFAULT_MAX_PER_ROW))
        self.max_per_row_entry = ttk.Entry(
            self.import_panel, textvariable=self.max_per_row_var, width=6
        )
        self.max_per_row_entry.grid(row=4, column=1, sticky=tk.W, pady=ROW_PADY)

        self.image_spacing_label = ttk.Label(
            self.import_panel, text="Gap between images (%)"
        )
        self.image_spacing_label.grid(
            row=5, column=0, sticky=tk.W, padx=(0, 12), pady=ROW_PADY
        )
        self.image_spacing_var = tk.StringVar(value=str(int(DEFAULT_IMAGE_SPACING_PCT)))
        self.image_spacing_entry = ttk.Entry(
            self.import_panel, textvariable=self.image_spacing_var, width=6
        )
        self.image_spacing_entry.grid(row=5, column=1, sticky=tk.W, pady=ROW_PADY)

        self.group_spacing_label = ttk.Label(
            self.import_panel, text="Gap between groups (%)"
        )
        self.group_spacing_label.grid(
            row=6, column=0, sticky=tk.NW, padx=(0, 12), pady=ROW_PADY
        )
        self.group_spacing_var = tk.StringVar(value=str(int(DEFAULT_GROUP_SPACING_PCT)))
        self.group_spacing_entry = ttk.Entry(
            self.import_panel, textvariable=self.group_spacing_var, width=6
        )
        self.group_spacing_entry.grid(row=6, column=1, sticky=tk.NW, pady=ROW_PADY)

        preview_box = ttk.Frame(self.import_panel)
        preview_box.grid(
            row=1, column=2, rowspan=6, sticky=tk.NSEW, padx=(20, 0), pady=ROW_PADY
        )
        preview_box.rowconfigure(0, weight=1)
        preview_box.columnconfigure(0, weight=1)
        colors = self.style.colors
        self.preview_canvas = tk.Canvas(
            preview_box,
            width=PREVIEW_CANVAS_W,
            height=PREVIEW_CANVAS_H,
            bg=colors.inputbg,
            highlightthickness=1,
            highlightbackground=colors.border,
            relief=tk.FLAT,
            borderwidth=0,
        )
        self.preview_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        self.preview_button = ttk.Button(
            preview_box,
            text="Preview",
            command=self.preview_layout,
            width=PREVIEW_BTN_WIDTH,
        )
        self.preview_button.grid(row=1, column=0, sticky=tk.W, pady=(ROW_PADY, 0))
        self._clear_layout_preview()

        self.extract_panel = ttk.Frame(mode_box)
        self.extract_panel.columnconfigure(1, weight=1)
        label_opts = {"sticky": tk.W, "pady": ROW_PADY}
        ctrl_opts = {"sticky": tk.EW, "pady": ROW_PADY}

        ttk.Label(self.extract_panel, text="Output format").grid(
            row=0, column=0, **label_opts
        )
        format_btns = ttk.Frame(self.extract_panel)
        format_btns.grid(row=0, column=1, sticky=tk.W, pady=ROW_PADY)
        self.format_var = tk.StringVar(value=FORMAT_PNG)
        self.format_radios: list[tb.Radiobutton] = []
        for label, value in (
            ("PNG", FORMAT_PNG),
            ("JPEG", FORMAT_JPEG),
            ("WebP", FORMAT_WEBP),
        ):
            radio = tb.Radiobutton(
                format_btns,
                text=label,
                value=value,
                variable=self.format_var,
                command=self._sync_export_ui,
                bootstyle=RADIO_STYLE,
            )
            radio.pack(side=tk.LEFT, padx=(0, 8))
            self.format_radios.append(radio)

        self.webp_label = ttk.Label(self.extract_panel, text="WebP mode")
        self.webp_label.grid(row=1, column=0, **label_opts)
        self.webp_row = ttk.Frame(self.extract_panel)
        self.webp_row.grid(row=1, column=1, sticky=tk.W, pady=ROW_PADY)
        self.webp_mode_var = tk.StringVar(value=WEBP_LOSSY)
        self.webp_radios: list[tb.Radiobutton] = []
        for text, value in (("Lossy", WEBP_LOSSY), ("Lossless", WEBP_LOSSLESS)):
            radio = tb.Radiobutton(
                self.webp_row,
                text=text,
                value=value,
                variable=self.webp_mode_var,
                command=self._sync_export_ui,
                bootstyle=RADIO_STYLE,
            )
            radio.pack(side=tk.LEFT, padx=(0, 8))
            self.webp_radios.append(radio)

        self.quality_label = ttk.Label(self.extract_panel, text="Quality")
        self.quality_label.grid(row=2, column=0, **label_opts)
        self.quality_row = ttk.Frame(self.extract_panel)
        self.quality_row.grid(row=2, column=1, sticky=tk.EW, pady=ROW_PADY)
        self.quality_var = tk.IntVar(value=DEFAULT_QUALITY)
        self.quality_entry_var = tk.StringVar(value=str(DEFAULT_QUALITY))
        self.quality_scale = tb.Scale(
            self.quality_row,
            from_=MIN_QUALITY,
            to=MAX_QUALITY,
            orient=tk.HORIZONTAL,
            length=220,
            command=self._on_quality_scale,
            bootstyle=SCALE_STYLE,
        )
        self.quality_scale.set(DEFAULT_QUALITY)
        self.quality_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.quality_entry = ttk.Entry(
            self.quality_row, textvariable=self.quality_entry_var, width=4
        )
        self.quality_entry.pack(side=tk.LEFT, padx=(8, 0))
        self.quality_entry.bind("<FocusOut>", self._on_quality_entry)
        self.quality_entry.bind("<Return>", self._on_quality_entry)

        ttk.Label(self.extract_panel, text="Name prefix").grid(
            row=3, column=0, **label_opts
        )
        self.rename_row = ttk.Frame(self.extract_panel)
        self.rename_row.grid(row=3, column=1, sticky=tk.W, pady=ROW_PADY)
        self.rename_var = tk.StringVar(value="page")
        self.rename_entry = ttk.Entry(
            self.rename_row, textvariable=self.rename_var, width=12
        )
        self.rename_entry.pack(side=tk.LEFT)
        self.rename_example_label = ttk.Label(
            self.rename_row, text="(e.g. page_0001.png)"
        )
        self.rename_example_label.pack(side=tk.LEFT, padx=(8, 0))
        self.rename_var.trace_add("write", lambda *_: self._update_rename_example())

        self.save_folder_btn = ttk.Button(
            self.extract_panel,
            text="Output folder",
            command=self.choose_capture,
            width=PATH_BTN_WIDTH,
        )
        self.save_folder_btn.grid(row=4, column=0, sticky=tk.W, pady=ROW_PADY)
        self.capture_var = tk.StringVar()
        self.capture_entry = ttk.Entry(
            self.extract_panel, textvariable=self.capture_var
        )
        self.capture_entry.grid(row=4, column=1, **ctrl_opts)

        self.overwrite_images_var = tk.BooleanVar(value=False)
        self.overwrite_images_check = tb.Checkbutton(
            self.extract_panel,
            text="Allow overwriting existing images",
            variable=self.overwrite_images_var,
            bootstyle=CHECK_STYLE,
        )
        self.overwrite_images_check.grid(
            row=5, column=0, columnspan=2, sticky=tk.W, pady=ROW_PADY
        )

        self.toc_folders_var = tk.BooleanVar(value=False)
        self.toc_folders_check = tb.Checkbutton(
            self.extract_panel,
            text="Create folders from PDF bookmarks",
            variable=self.toc_folders_var,
            bootstyle=CHECK_STYLE,
        )
        self.toc_folders_check.grid(
            row=6, column=0, columnspan=2, sticky=tk.W, pady=ROW_PADY
        )

        self.run_row = ttk.Frame(root)
        self.run_row.pack(fill=tk.X, **pad)
        self.run_button = ttk.Button(
            self.run_row, text="Run", command=self.run_capture, width=RUN_BTN_WIDTH
        )
        self.run_button.pack(side=tk.LEFT)
        self.progress_var = tk.StringVar(value="")
        self.progress_label = ttk.Label(self.run_row, textvariable=self.progress_var)
        self.progress_label.pack(side=tk.LEFT, padx=12)
        self.cancel_button = ttk.Button(
            self.run_row,
            text="Cancel",
            command=self.request_cancel,
            width=RUN_BTN_WIDTH,
        )
        self.cancel_button.pack(side=tk.LEFT)

        self.log = tk.Text(root, height=6, wrap=tk.WORD, **self._text_widget_kwargs())
        self.log.pack(fill=tk.BOTH, expand=True, **pad)
        self._configure_log_tags(self.log)

        self._pdf_box = pdf_box
        self._align_form_columns()

    def _align_form_columns(self) -> None:
        """Keep left label/button columns the same width across sections."""
        self.update_idletasks()
        col0 = max(self._choose_pdf_btn.winfo_reqwidth(), 1)
        for frame in (self._pdf_box, self.import_panel, self.extract_panel):
            frame.columnconfigure(0, minsize=col0)
            frame.columnconfigure(1, weight=1)

    def _fit_window_to_content(self) -> None:
        """Grow window so controls are not clipped at launch."""
        self.update_idletasks()
        need_w = max(960, self.winfo_reqwidth() + 24)
        need_h = max(780, self.winfo_reqheight() + 24)
        self.minsize(860, 640)
        self.geometry(f"{need_w}x{need_h}")

    def _ensure_window_fits_content(self) -> None:
        """Grow the window if content or the log needs more space (never shrink)."""
        self.update_idletasks()
        need_w = max(960, self.winfo_reqwidth() + 24)
        need_h = max(780, self.winfo_reqheight() + 24)
        # If the log was squeezed by newly shown controls, grow enough to restore it.
        log_min = 120
        try:
            log_h = int(self.log.winfo_height())
            if 0 < log_h < log_min:
                need_h = max(need_h, self.winfo_height() + (log_min - log_h))
        except (tk.TclError, AttributeError):
            pass
        cur_w = max(self.winfo_width(), 1)
        cur_h = max(self.winfo_height(), 1)
        if need_w > cur_w or need_h > cur_h:
            self.geometry(f"{max(need_w, cur_w)}x{max(need_h, cur_h)}")

    def _text_widget_kwargs(self) -> dict:
        colors = self.style.colors
        return {
            "bg": colors.inputbg,
            "fg": colors.inputfg,
            "insertbackground": colors.inputfg,
            "selectbackground": colors.selectbg,
            "selectforeground": colors.selectfg,
            "highlightthickness": 0,
            "relief": tk.FLAT,
            "borderwidth": 0,
        }

    def _configure_log_tags(self, widget: tk.Text) -> None:
        colors = self.style.colors
        widget.tag_configure("ok", foreground=colors.success)
        widget.tag_configure("err", foreground=colors.danger)

    def _build_footer(self, parent: ttk.Frame) -> ttk.Frame:
        footer = ttk.Frame(parent)
        colors = self.style.colors
        muted = {"fg": colors.border, "bg": colors.bg}
        link_style = {"fg": colors.info, "bg": colors.bg, "cursor": "hand2"}

        def sep() -> None:
            tk.Label(footer, text=" · ", **muted).pack(side=tk.LEFT)

        tk.Label(footer, text=f"© 2026 {APP_AUTHOR}", **muted).pack(side=tk.LEFT)
        sep()
        github = tk.Label(footer, text="GitHub", **link_style)
        github.pack(side=tk.LEFT)
        github.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))
        sep()
        issues = tk.Label(footer, text="Issues", **link_style)
        issues.pack(side=tk.LEFT)
        issues.bind("<Button-1>", lambda _e: webbrowser.open(ISSUES_URL))
        sep()
        about = tk.Label(footer, text="About", **link_style)
        about.pack(side=tk.LEFT)
        about.bind("<Button-1>", lambda _e: self.show_about())
        sep()
        sponsor = tk.Label(
            footer, text=f"☕ {BMC_BUTTON_TEXT}", **link_style
        )
        sponsor.pack(side=tk.LEFT)
        sponsor.bind("<Button-1>", lambda _e: webbrowser.open(SPONSOR_URL))
        return footer

    def _readme_path(self) -> Path:
        return bundle_dir() / "README.md"

    def show_about(self):
        win = tb.Toplevel(self)
        win.title("About")
        win.transient(self)
        win.resizable(False, False)
        win.grab_set()

        body = ttk.Frame(win, padding=16)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=APP_SHORT_NAME, font=("", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(
            body,
            text=(
                "Designed for creative workflows.\n"
                "Quick and easy PDF-to-image conversion.\n"
                "Supports direct import to PureRef.\n"
                f"\nVersion {APP_VERSION}\n"
                "\nCreavFlow is not affiliated with PureRef.\n"
                "PureRef is a trademark of its respective owners.\n"
                "\nLicensed under AGPL-3.0.\n"
                "PDF rendering uses PyMuPDF (AGPL).\n"
                "See LICENSE and NOTICE.txt.\n"
                f"\n© 2026 {APP_AUTHOR}"
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(16, 0))
        ttk.Button(
            buttons, text="Open README", command=self.open_readme
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons, text="GitHub", command=lambda: webbrowser.open(GITHUB_URL)
        ).pack(side=tk.LEFT, padx=(8, 0))

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{x}+{y}")

    def open_readme(self):
        path = self._readme_path()
        if not path.is_file():
            messagebox.showwarning("README missing", f"Could not find:\n{path}")
            return
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Could not open README", str(exc))
            return

        # About uses grab_set(); release so README can receive input, then restore.
        about = self.grab_current()
        if about is not None:
            about.grab_release()

        win = tb.Toplevel(self)
        win.title("README")
        win.transient(about if about is not None else self)
        win.grab_set()

        def close_readme():
            win.destroy()
            if about is None:
                return
            try:
                if about.winfo_exists():
                    about.grab_set()
                    about.lift()
                    about.focus_set()
            except tk.TclError:
                pass

        body = ttk.Frame(win, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        # Pack Close first at the bottom so it stays visible.
        close_row = ttk.Frame(body)
        close_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        ttk.Button(close_row, text="Close", command=close_readme).pack(anchor=tk.E)

        text_host = ttk.Frame(body)
        text_host.pack(fill=tk.BOTH, expand=True)
        scroll = tb.Scrollbar(text_host, bootstyle=SCROLL_STYLE)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        viewer = tk.Text(
            text_host,
            wrap=tk.WORD,
            yscrollcommand=scroll.set,
            font=README_FONT,
            **self._text_widget_kwargs(),
        )
        viewer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=viewer.yview)
        viewer.insert("1.0", content)
        viewer.configure(state=tk.DISABLED)

        win.protocol("WM_DELETE_WINDOW", close_readme)

        win.update_idletasks()
        _place_fixed_window(
            win,
            _readme_width_for_features(content, viewer),
            README_WINDOW_H,
            anchor=about if about is not None else self,
        )

    def _on_quality_scale(self, value: str):
        if self._syncing_quality:
            return
        quality = int(round(float(value)))
        self._syncing_quality = True
        try:
            self.quality_var.set(quality)
            self.quality_entry_var.set(str(quality))
        finally:
            self._syncing_quality = False

    def _on_quality_entry(self, _event=None):
        if self._syncing_quality:
            return
        raw = self.quality_entry_var.get().strip()
        try:
            quality = int(raw)
        except ValueError:
            quality = self.quality_var.get()
        quality = max(MIN_QUALITY, min(MAX_QUALITY, quality))
        self._syncing_quality = True
        try:
            self.quality_var.set(quality)
            self.quality_entry_var.set(str(quality))
            self.quality_scale.set(quality)
        finally:
            self._syncing_quality = False

    def _quality_enabled(self) -> bool:
        fmt = self.format_var.get()
        if fmt == FORMAT_JPEG:
            return True
        if fmt == FORMAT_WEBP and self.webp_mode_var.get() == WEBP_LOSSY:
            return True
        return False

    def _sync_export_ui(self):
        """Keep all Export options visible; gray out ones that do not apply."""
        fmt = self.format_var.get()
        webp_on = fmt == FORMAT_WEBP
        quality_on = self._quality_enabled()

        for radio in self.webp_radios:
            radio.state(["!disabled"] if webp_on else ["disabled"])
        self.webp_label.state(["!disabled"] if webp_on else ["disabled"])
        self.quality_scale.state(["!disabled"] if quality_on else ["disabled"])
        self.quality_entry.state(["!disabled"] if quality_on else ["disabled"])
        self.quality_label.state(["!disabled"] if quality_on else ["disabled"])

        self._update_rename_example()

    def _update_rename_example(self):
        fmt = self.format_var.get()
        ext = FORMAT_EXTENSIONS.get(fmt, ".png")
        prefix = self.rename_var.get().strip() or "page"
        self.rename_example_label.config(text=f"(e.g. {prefix}_0001{ext})")

    def _on_advanced_toggle(self):
        self._sync_advanced_ui()
        self._clear_layout_preview()

    def _on_bookmark_groups_toggle(self):
        self._sync_bookmark_group_ui()
        self._clear_layout_preview()

    def _sync_advanced_ui(self):
        custom_on = bool(self.advanced_var.get())
        state = ["!disabled"] if custom_on else ["disabled"]
        for widget in (
            self.max_per_row_label,
            self.max_per_row_entry,
            self.image_spacing_label,
            self.image_spacing_entry,
        ):
            widget.state(state)
        self._sync_bookmark_group_ui()

    def _sync_bookmark_group_ui(self):
        grouping_on = bool(self.bookmark_groups_var.get()) and bool(
            self.advanced_var.get()
        )
        state = ["!disabled"] if grouping_on else ["disabled"]
        self.group_spacing_label.state(state)
        self.group_spacing_entry.state(state)

    def _sync_mode_ui(self):
        self.import_panel.pack_forget()
        self.extract_panel.pack_forget()
        if self.mode_var.get() == MODE_IMPORT:
            self.import_panel.pack(fill=tk.BOTH, expand=True)
            self._sync_advanced_ui()
            self._ensure_window_fits_content()
        else:
            self.extract_panel.pack(fill=tk.X)
            self._sync_export_ui()
            self._ensure_window_fits_content()

    def log_line(self, text: str, tag: str | None = None):
        if not self.winfo_exists():
            return
        if tag:
            self.log.insert(tk.END, text + "\n", tag)
        else:
            self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.update_idletasks()

    def request_cancel(self):
        if self._busy:
            self._cancel_event.set()
            self.progress_var.set("Canceling…")
            self.update_idletasks()

    def _set_settings_interactive(self, enabled: bool) -> None:
        """Enable/disable PDF + mode options (not Run/Cancel/log/footer)."""

        def apply(widget: tk.Misc) -> None:
            try:
                widget.state(["!disabled"] if enabled else ["disabled"])
            except (AttributeError, tk.TclError):
                try:
                    widget.configure(state=tk.NORMAL if enabled else tk.DISABLED)
                except (AttributeError, tk.TclError):
                    pass
            for child in widget.winfo_children():
                apply(child)

        apply(self._settings_zone)
        if enabled:
            self._sync_export_ui()
            self._sync_advanced_ui()

    def _set_busy(self, busy: bool):
        self._busy = busy
        if busy:
            self._cancel_event.clear()
        self._set_settings_interactive(not busy)
        if busy:
            self.run_button.state(["disabled"])
            self.cancel_button.state(["!disabled"])
        else:
            self.run_button.state(["!disabled"])
            self.cancel_button.state(["disabled"])
            self.progress_var.set("")

    def _on_progress(self, current: int, total: int):
        self._ui_queue.put(("progress", current, total))

    def _should_cancel(self) -> bool:
        return self._cancel_event.is_set()

    def _poll_ui_queue(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                message = self._ui_queue.get_nowait()
                self._handle_ui_message(message)
        except queue.Empty:
            pass
        if self._busy and self.winfo_exists():
            self.after(50, self._poll_ui_queue)

    def _handle_ui_message(self, message: tuple):
        kind = message[0]
        if kind == "progress":
            _, current, total = message
            if self._cancel_event.is_set():
                self.progress_var.set("Canceling…")
            else:
                self.progress_var.set(f"Rendering page {current} of {total}…")
        elif kind == "log":
            _, text, tag = message
            self.log_line(text, tag)
        elif kind == "done":
            self._finish_job_done(message[1])
        elif kind == "canceled":
            self._finish_job_canceled(message[1])
        elif kind == "error":
            self._finish_job_error(message[1])

    def _finish_job_done(self, payload: dict):
        mode = payload["mode"]
        self._set_busy(False)
        self._persist_settings()
        if mode == MODE_IMPORT:
            dest: Path = payload["dest"]
            self.log_line(f"Wrote {dest}")
            try:
                open_pur_file(dest)
                self.log_line("Done", "ok")
                messagebox.showinfo("Done", f"Saved and opened:\n{dest}")
            except OSError as exc:
                self.log_line(f"Could not open .pur: {exc}", "err")
                self.log_line("Done", "ok")
                messagebox.showinfo(
                    "Done",
                    f"Saved:\n{dest}\n\nCould not open it automatically:\n{exc}",
                )
            return

        capture_dir: Path = payload["capture_dir"]
        self.log_line(f"Saved images to {capture_dir}")
        self.log_line("Done", "ok")
        try:
            open_folder(capture_dir)
        except OSError as exc:
            messagebox.showinfo(
                "Export complete",
                f"Images saved in:\n{capture_dir}\n\n"
                f"Could not open the folder automatically:\n{exc}",
            )
            return
        messagebox.showinfo("Export complete", f"Images saved in:\n{capture_dir}")

    def _finish_job_canceled(self, payload: dict):
        mode = payload["mode"]
        done = payload["done"]
        total = payload["total"]
        self.log_line("Canceled by user", "err")
        if mode == MODE_EXTRACT and done:
            self.log_line(
                f"Stopped after {done} of {total} pages. Partial files kept."
            )
        self._set_busy(False)
        self._persist_settings()

    def _finish_job_error(self, message: str):
        self.log_line(f"Error: {message}", "err")
        self._set_busy(False)
        self._persist_settings()
        messagebox.showerror("Capture failed", message)

    def _on_close(self):
        if self._busy:
            self._cancel_event.set()
        self._persist_settings()
        self.destroy()

    def _set_pdf_label(self, path: Path | None) -> None:
        if path is None:
            self.pdf_label.config(text="No PDF selected")
            if self._pdf_tip is not None:
                self._pdf_tip.configure(text="")
            return
        full = str(path)
        short = shorten_display_path(path)
        self.pdf_label.config(text=short)
        if self._pdf_tip is not None:
            self._pdf_tip.configure(text=full if short != full else "")

    def _dialog_initial_dir(self, *candidates: str | Path | None) -> str | None:
        for raw in candidates:
            if not raw:
                continue
            path = Path(raw)
            folder = path if path.is_dir() else path.parent
            if folder.is_dir():
                return str(folder)
        return None

    def _apply_settings(self) -> None:
        """Restore remembered folders only; other options stay at defaults."""
        capture = self._settings.get("capture_dir")
        if capture:
            self.capture_var.set(str(capture))

    def _persist_settings(self) -> None:
        """Remember folder locations only (PDF / PureRef / export)."""
        data: dict = {}
        if self.pdf_path is not None:
            data["pdf_dir"] = str(self.pdf_path.parent)
        elif self._settings.get("pdf_dir"):
            data["pdf_dir"] = self._settings["pdf_dir"]
        output = self.output_var.get().strip()
        if output:
            data["pur_dir"] = str(Path(output).parent)
        elif self._settings.get("pur_dir"):
            data["pur_dir"] = self._settings["pur_dir"]
        capture = self.capture_var.get().strip()
        if capture:
            data["capture_dir"] = capture
        elif self._settings.get("capture_dir"):
            data["capture_dir"] = self._settings["capture_dir"]
        self._settings = data
        save_settings(data)

    def choose_pdf(self):
        initial = self._dialog_initial_dir(
            self.pdf_path,
            self._settings.get("pdf_dir"),
        )
        chosen = filedialog.askopenfilename(
            title="Choose PDF",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")],
            initialdir=initial,
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            with fitz.open(path) as doc:
                self.page_count = doc.page_count
                infos = page_infos(doc)
        except Exception as exc:
            messagebox.showerror("Could not read PDF", str(exc))
            return
        self.pdf_path = path
        self._set_pdf_label(path)
        landscape = sum(1 for info in infos if info.landscape)
        portrait = self.page_count - landscape
        self.info_label.config(
            text=f"{self.page_count} pages · {portrait} portrait · {landscape} landscape"
        )
        self.range_var.set(f"1-{self.page_count}")
        self._sync_defaults()
        self._persist_settings()
        self._clear_layout_preview()

    def select_all_pages(self):
        if self.page_count:
            self.range_var.set(f"1-{self.page_count}")
            self._clear_layout_preview()

    def choose_output(self):
        initial = self._dialog_initial_dir(
            self.output_var.get().strip(),
            self._settings.get("pur_dir"),
            self.pdf_path,
        )
        chosen = filedialog.asksaveasfilename(
            title="Save PureRef as",
            defaultextension=".pur",
            filetypes=[("PureRef", "*.pur")],
            initialdir=initial,
        )
        if chosen:
            self.output_var.set(chosen)
            self._persist_settings()

    def choose_capture(self):
        initial = self._dialog_initial_dir(
            self.capture_var.get().strip(),
            self._settings.get("capture_dir"),
            self.pdf_path,
        )
        chosen = filedialog.askdirectory(
            title="Folder for exported page images",
            initialdir=initial,
        )
        if chosen:
            self.capture_var.set(chosen)
            self._persist_settings()

    def _sync_defaults(self):
        if not self.pdf_path:
            return
        self.output_var.set(str(default_output_for_new(self.pdf_path)))
        if not self.capture_var.get():
            self.capture_var.set(str(default_capture_for(self.pdf_path)))

    def selected_pages(self) -> list[int] | None:
        try:
            return parse_page_ranges(self.range_var.get().strip(), self.page_count)
        except ValueError as exc:
            messagebox.showwarning("Pages needed", str(exc))
            return None

    def selected_max_side(self) -> int | None:
        raw = self.max_side_var.get().strip()
        try:
            value = int(raw)
        except ValueError:
            messagebox.showwarning(
                "Max side needed",
                f"Enter a whole number between {MIN_SIDE_PX} and {MAX_SIDE_PX}.",
            )
            return None
        try:
            return validate_max_side(value)
        except ValueError as exc:
            messagebox.showwarning("Max side needed", str(exc))
            return None

    def selected_layout_settings(self) -> LayoutSettings | None:
        if not self.advanced_var.get():
            return LayoutSettings()

        try:
            max_per_row = int(self.max_per_row_var.get().strip())
        except ValueError:
            messagebox.showwarning(
                "Layout needed",
                f"Max images per row must be a whole number between {MIN_PER_ROW} and {MAX_PER_ROW}.",
            )
            return None
        if max_per_row < MIN_PER_ROW or max_per_row > MAX_PER_ROW:
            messagebox.showwarning(
                "Layout needed",
                f"Max images per row must be between {MIN_PER_ROW} and {MAX_PER_ROW}.",
            )
            return None

        try:
            image_pct = float(self.image_spacing_var.get().strip())
            group_pct = float(self.group_spacing_var.get().strip())
        except ValueError:
            messagebox.showwarning(
                "Layout needed",
                "Image and group spacing must be numbers (percent of max image size).",
            )
            return None
        if image_pct < MIN_SPACING_PCT or image_pct > MAX_IMAGE_SPACING_PCT:
            messagebox.showwarning(
                "Layout needed",
                f"Image spacing must be between {MIN_SPACING_PCT:g}% and {MAX_IMAGE_SPACING_PCT:g}%.",
            )
            return None
        if group_pct < MIN_SPACING_PCT or group_pct > MAX_GROUP_SPACING_PCT:
            messagebox.showwarning(
                "Layout needed",
                f"Group spacing must be between {MIN_SPACING_PCT:g}% and {MAX_GROUP_SPACING_PCT:g}%.",
            )
            return None

        return LayoutSettings(
            max_per_row=max_per_row,
            image_spacing_pct=image_pct,
            group_spacing_pct=group_pct,
        )

    def _clear_layout_preview(self, message: str = "Click Preview") -> None:
        canvas = getattr(self, "preview_canvas", None)
        if canvas is None:
            return
        colors = self.style.colors
        canvas.delete("all")
        canvas.create_text(
            PREVIEW_CANVAS_W // 2,
            PREVIEW_CANVAS_H // 2,
            text=message,
            fill=colors.border,
            font=("Segoe UI", 9),
        )

    def _draw_layout_preview(self, placed: list[PlacedItem]) -> None:
        canvas = self.preview_canvas
        canvas.delete("all")
        colors = self.style.colors
        images = [item for item in placed if not item.is_title]
        if not images:
            self._clear_layout_preview("Nothing to preview")
            return

        left = min(item.x - item.width / 2.0 for item in images)
        top = min(item.y - item.height / 2.0 for item in images)
        right = max(item.x + item.width / 2.0 for item in images)
        bottom = max(item.y + item.height / 2.0 for item in images)
        world_w = max(right - left, 1.0)
        world_h = max(bottom - top, 1.0)

        canvas.update_idletasks()
        view_w = max(int(canvas.winfo_width()), PREVIEW_CANVAS_W)
        view_h = max(int(canvas.winfo_height()), PREVIEW_CANVAS_H)
        pad = PREVIEW_PAD
        scale = min((view_w - 2 * pad) / world_w, (view_h - 2 * pad) / world_h)
        offset_x = pad + ((view_w - 2 * pad) - world_w * scale) / 2.0
        offset_y = pad + ((view_h - 2 * pad) - world_h * scale) / 2.0

        def to_view(x: float, y: float) -> tuple[float, float]:
            return (
                offset_x + (x - left) * scale,
                offset_y + (y - top) * scale,
            )

        for item in images:
            x0, y0 = to_view(item.x - item.width / 2.0, item.y - item.height / 2.0)
            x1, y1 = to_view(item.x + item.width / 2.0, item.y + item.height / 2.0)
            canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                fill="#f0f0f0",
                outline=colors.border,
                width=1,
            )

    def preview_layout(self):
        if self._busy:
            return
        if not self.pdf_path:
            messagebox.showwarning("PDF needed", "Choose a PDF first.")
            return
        pages = self.selected_pages()
        if pages is None:
            return
        if not pages:
            messagebox.showwarning("Pages needed", "Enter at least one valid PDF page.")
            return
        max_side = self.selected_max_side()
        if max_side is None:
            return
        layout = self.selected_layout_settings()
        if layout is None:
            return

        group_by_bookmarks = bool(self.bookmark_groups_var.get())
        try:
            with fitz.open(self.pdf_path) as doc:
                measured = layout_measure_pages(doc, pages, max_side)
        except Exception as exc:
            messagebox.showerror("Could not preview", str(exc))
            return

        placed = layout_items(
            measured,
            layout,
            max_side,
            group_by_bookmarks=group_by_bookmarks,
        )
        self._draw_layout_preview(placed)

    def selected_export_settings(self) -> ExportSettings | None:
        self._on_quality_entry()
        fmt = self.format_var.get()
        try:
            return ExportSettings(
                image_format=fmt,
                quality=int(self.quality_var.get()),
                webp_lossless=fmt == FORMAT_WEBP
                and self.webp_mode_var.get() == WEBP_LOSSLESS,
                name_prefix=self.rename_var.get(),
                overwrite=bool(self.overwrite_images_var.get()),
            ).normalized()
        except ValueError as exc:
            messagebox.showwarning("Name needed", str(exc))
            return None

    def run_capture(self):
        if self._busy:
            return
        if not self.pdf_path:
            messagebox.showwarning("PDF needed", "Choose a PDF first.")
            return
        pages = self.selected_pages()
        if pages is None:
            return
        if not pages:
            messagebox.showwarning("Pages needed", "Enter at least one valid PDF page.")
            return
        max_side = self.selected_max_side()
        if max_side is None:
            return

        mode = self.mode_var.get()
        if mode == MODE_IMPORT:
            layout = self.selected_layout_settings()
            if layout is None:
                return
            output = Path(self.output_var.get().strip())
            if not output.name:
                messagebox.showwarning("Output needed", "Choose where to save the .pur file.")
                return
            if find_pureref_exe() is None:
                if not confirm_run_without_pureref(self):
                    self.log_line("Canceled by user", "err")
                    return
            overwrite = bool(self.overwrite.get())
            self._start_job(
                mode=mode,
                pages=pages,
                max_side=max_side,
                layout=layout,
                output=output,
                overwrite=overwrite,
                group_by_bookmarks=bool(self.bookmark_groups_var.get()),
            )
            return

        capture_value = self.capture_var.get().strip()
        if not capture_value:
            messagebox.showwarning(
                "Folder needed", "Choose a folder for the exported images."
            )
            return
        settings = self.selected_export_settings()
        if settings is None:
            return
        self._start_job(
            mode=mode,
            pages=pages,
            max_side=max_side,
            capture_dir=ensure_dir(Path(capture_value)),
            settings=settings,
            toc_subfolders=bool(self.toc_folders_var.get()),
        )

    def _start_job(
        self,
        *,
        mode: str,
        pages: list[int],
        max_side: int,
        layout: LayoutSettings | None = None,
        output: Path | None = None,
        overwrite: bool = False,
        capture_dir: Path | None = None,
        settings: ExportSettings | None = None,
        toc_subfolders: bool = False,
        group_by_bookmarks: bool = True,
    ):
        while True:
            try:
                self._ui_queue.get_nowait()
            except queue.Empty:
                break
        self._set_busy(True)
        worker = threading.Thread(
            target=self._worker_run,
            kwargs={
                "mode": mode,
                "pages": pages,
                "max_side": max_side,
                "layout": layout,
                "output": output,
                "overwrite": overwrite,
                "capture_dir": capture_dir,
                "settings": settings,
                "toc_subfolders": toc_subfolders,
                "group_by_bookmarks": group_by_bookmarks,
            },
            daemon=True,
        )
        worker.start()
        self._poll_ui_queue()

    def _worker_run(
        self,
        *,
        mode: str,
        pages: list[int],
        max_side: int,
        layout: LayoutSettings | None,
        output: Path | None,
        overwrite: bool,
        capture_dir: Path | None,
        settings: ExportSettings | None,
        toc_subfolders: bool,
        group_by_bookmarks: bool,
    ):
        try:
            if mode == MODE_IMPORT:
                assert layout is not None and output is not None
                self._worker_import(
                    pages,
                    max_side,
                    layout,
                    output,
                    overwrite,
                    group_by_bookmarks,
                )
            else:
                assert capture_dir is not None and settings is not None
                self._worker_extract(
                    pages, max_side, capture_dir, settings, toc_subfolders
                )
        except RenderCancelled as exc:
            self._ui_queue.put(
                (
                    "canceled",
                    {
                        "mode": mode,
                        "done": len(exc.rendered),
                        "total": len(pages),
                    },
                )
            )
        except Exception as exc:
            self._ui_queue.put(("error", str(exc)))

    def _worker_import(
        self,
        pages: list[int],
        max_side: int,
        layout: LayoutSettings,
        output: Path,
        overwrite: bool,
        group_by_bookmarks: bool,
    ):
        image_gap, group_gap = layout.gaps_for(max_side)
        group_note = (
            f", group gap {group_gap:.0f}px, bookmark groups on"
            if group_by_bookmarks
            else ", bookmark groups off"
        )
        self._ui_queue.put(
            (
                "log",
                (
                    f"Rendering {len(pages)} page(s) at max side {max_side} "
                    f"(row {layout.max_per_row}, image gap {image_gap:.0f}px"
                    f"{group_note})..."
                ),
                None,
            )
        )
        with tempfile.TemporaryDirectory(prefix="pdf_to_pureref_") as temp_name:
            capture_dir = Path(temp_name)
            rendered = render_selected_pages(
                self.pdf_path,
                pages,
                capture_dir,
                max_side,
                ExportSettings(image_format=FORMAT_PNG),
                on_progress=self._on_progress,
                should_cancel=self._should_cancel,
            )
            placed = layout_items(
                rendered,
                layout,
                max_side,
                group_by_bookmarks=group_by_bookmarks,
            )
            dest = write_new_pur(placed, output, overwrite)
        self._ui_queue.put(("done", {"mode": MODE_IMPORT, "dest": dest}))

    def _worker_extract(
        self,
        pages: list[int],
        max_side: int,
        capture_dir: Path,
        settings: ExportSettings,
        toc_subfolders: bool,
    ):
        label = settings.image_format.upper()
        if settings.image_format == FORMAT_WEBP:
            label += " lossless" if settings.webp_lossless else f" lossy q{settings.quality}"
        elif settings.image_format == FORMAT_JPEG:
            label += f" q{settings.quality}"
        self._ui_queue.put(
            (
                "log",
                (
                    f"Rendering {len(pages)} page(s) as {label} "
                    f"named '{settings.name_prefix}_####' at max side {max_side}"
                    f"{' with folders from PDF bookmarks' if toc_subfolders else ''}..."
                ),
                None,
            )
        )
        render_selected_pages(
            self.pdf_path,
            pages,
            capture_dir,
            max_side,
            settings,
            on_progress=self._on_progress,
            should_cancel=self._should_cancel,
            toc_subfolders=toc_subfolders,
        )
        self._ui_queue.put(
            ("done", {"mode": MODE_EXTRACT, "capture_dir": capture_dir})
        )


def _enable_windows_dpi_awareness() -> None:
    """Make Tk render sharply on high-DPI Windows displays."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # Per-monitor DPI awareness v2 (Windows 10 1703+)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    _enable_windows_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
