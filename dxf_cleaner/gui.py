"""Tkinter desktop GUI for dxf-cleaner.

Thin front end over dxf_cleaner.core -- all pipeline-driving logic lives
there so the CLI and this GUI stay in sync.
"""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from dxf_cleaner.config import load_config
from dxf_cleaner.core import (
    RASTER_SUFFIXES,
    apply_overrides,
    apply_raster_overrides,
    apply_thickness_override,
    process_path,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

_LOG_COLORS = {"critical": "#c0392b", "warning": "#b8860b", "info": "#222222"}
_HINT_STYLE = {"foreground": "#666666", "font": ("", 9)}

# (gia_tri_truyen_cho_pipeline, nhan_hien_thi_tren_giao_dien)
WELD_MODES = [
    ("", "(Mặc định: overlapping)"),
    ("off", "Không hàn nét chồng nhau"),
    ("overlapping", "Chỉ hàn nét chồng khớp nhau (khuyên dùng)"),
    ("all", "Hàn mọi nét ở gần nhau"),
]
# Quy đổi DPI -> pixels/mm: 1 inch = 25.4mm.
MM_PER_INCH = 25.4

_RESULT_LABELS = {
    0: "OK",
    1: "CÓ CẢNH BÁO",
    2: "LỖI NGHIÊM TRỌNG - KHÔNG XUẤT FILE",
    3: "LỖI HỆ THỐNG",
}


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("DXF Cleaner - Dọn file cho máy cắt laser")
        root.geometry("700x720")
        root.minsize(640, 580)

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.check_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)
        self.snap_tol_var = tk.StringVar()
        self.weld_mode_var = tk.StringVar(value="")
        self.no_simplify_var = tk.BooleanVar(value=False)
        self.thickness_var = tk.StringVar()

        # Chỉ 1 trong 3 giá trị này được dùng để quy đổi pixel sang mm cho
        # ảnh raster -- loại trừ lẫn nhau bằng radio button (không chỉ bằng
        # validation) để người dùng không thể vô tình nhập cả 3 cùng lúc.
        self.raster_scale_mode = tk.StringVar(value="width")
        self.width_mm_var = tk.StringVar()
        self.height_mm_var = tk.StringVar()
        self.dpi_var = tk.StringVar()

        self.invert_var = tk.BooleanVar(value=False)
        self.raster_threshold_var = tk.StringVar()

        self._log_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_output_dir: Path | None = None

        self._build_widgets()
        self._update_raster_scale_state()
        self.root.after(100, self._drain_log_queue)

    # -- Bố cục giao diện -----------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        io_frame = ttk.LabelFrame(self.root, text="Đầu vào / Đầu ra")
        io_frame.pack(fill="x", **pad)
        io_frame.columnconfigure(0, weight=1)

        drop_hint = " (có thể kéo-thả file vào ô bên dưới)" if _HAS_DND else ""
        ttk.Label(io_frame, text=f"File DXF/PNG/JPG, hoặc cả thư mục chứa nhiều file{drop_hint}:") \
            .grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
        input_row = ttk.Frame(io_frame)
        input_row.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 2))
        input_row.columnconfigure(0, weight=1)
        input_entry = ttk.Entry(input_row, textvariable=self.input_path_var)
        input_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(input_row, text="Chọn file...", command=self._choose_input_file) \
            .grid(row=0, column=1, padx=(4, 0))
        ttk.Button(input_row, text="Chọn thư mục...", command=self._choose_input_dir) \
            .grid(row=0, column=2, padx=(4, 0))

        if _HAS_DND:
            input_entry.drop_target_register(DND_FILES)
            input_entry.dnd_bind("<<Drop>>", self._on_drop)

        ttk.Label(
            io_frame,
            text="Nếu chọn cả thư mục: mọi tùy chọn bên dưới sẽ áp dụng CHUNG cho tất cả file trong đó.",
            **_HINT_STYLE,
        ).grid(row=2, column=0, sticky="w", padx=6, pady=(0, 6))

        ttk.Label(io_frame, text="Nơi lưu kết quả (để trống = lưu cạnh file gốc):") \
            .grid(row=3, column=0, sticky="w", padx=6)
        output_row = ttk.Frame(io_frame)
        output_row.grid(row=4, column=0, sticky="ew", padx=6, pady=(0, 6))
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_row, text="Chọn...", command=self._choose_output).grid(row=0, column=1, padx=(4, 0))

        # -- Tùy chọn chung -------------------------------------------
        general_frame = ttk.LabelFrame(self.root, text="Tùy chọn chung")
        general_frame.pack(fill="x", **pad)
        general_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            general_frame,
            text="Chỉ kiểm tra, không ghi file DXF (vẫn ghi ảnh _KIEMTRA.png)",
            variable=self.check_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        ttk.Checkbutton(
            general_frame,
            text="Vẫn ghi file DXF dù có LỖI NGHIÊM TRỌNG (ví dụ nét/lỗ quá mảnh để cắt)",
            variable=self.force_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        ttk.Label(
            general_frame,
            text="Cẩn thận: các cảnh báo vẫn hiện đầy đủ bên dưới -- hãy đọc kỹ trước khi đưa file đi cắt.",
            **_HINT_STYLE,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        ttk.Label(general_frame, text="Độ dày vật liệu / tôn (mm):").grid(row=3, column=0, sticky="w", padx=6)
        ttk.Entry(general_frame, textvariable=self.thickness_var, width=10) \
            .grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(
            general_frame,
            text="Dùng để kiểm tra nét/lỗ có đủ rộng để cắt không (mặc định 2.0mm nếu để trống).",
            **_HINT_STYLE,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        # -- Làm sạch nét -------------------------------------------
        clean_frame = ttk.LabelFrame(self.root, text="Làm sạch nét (áp dụng cho cả DXF và ảnh)")
        clean_frame.pack(fill="x", **pad)
        clean_frame.columnconfigure(1, weight=1)
        clean_frame.columnconfigure(3, weight=1)

        ttk.Label(clean_frame, text="Dung sai hàn nối (mm):").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Entry(clean_frame, textvariable=self.snap_tol_var, width=10) \
            .grid(row=0, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(
            clean_frame,
            text="Khoảng cách tối đa để tự hàn 2 đầu đoạn gần nhau (mặc định 0.05mm).",
            **_HINT_STYLE,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        ttk.Label(clean_frame, text="Chế độ hàn nét chồng nhau (weld):") \
            .grid(row=0, column=2, sticky="w", padx=6, pady=2)
        self.weld_combo = ttk.Combobox(
            clean_frame, textvariable=self.weld_mode_var,
            values=[label for _, label in WELD_MODES], width=32, state="readonly",
        )
        self.weld_combo.current(0)
        self.weld_combo.grid(row=0, column=3, sticky="w", padx=6, pady=2)
        self.weld_combo.bind("<<ComboboxSelected>>", self._on_weld_combo_changed)

        ttk.Checkbutton(
            clean_frame,
            text="Tắt giảm node (giữ nguyên tất cả chi tiết, file sẽ nặng hơn)",
            variable=self.no_simplify_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))

        # -- Tùy chọn riêng cho ảnh raster ---------------------------------
        raster_frame = ttk.LabelFrame(
            self.root, text="Chỉ dành cho ảnh PNG/JPG (không dùng khi đầu vào là DXF)"
        )
        raster_frame.pack(fill="x", **pad)
        for col in range(2):
            raster_frame.columnconfigure(col, weight=1)

        ttk.Label(
            raster_frame,
            text="Chọn ĐÚNG 1 trong 3 cách để quy đổi pixel sang mm (2 ô còn lại sẽ tự khóa):",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))

        self.width_radio = ttk.Radiobutton(
            raster_frame, text="Theo chiều rộng thật (mm)", value="width",
            variable=self.raster_scale_mode, command=self._update_raster_scale_state,
        )
        self.width_radio.grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.width_entry = ttk.Entry(raster_frame, textvariable=self.width_mm_var, width=12)
        self.width_entry.grid(row=1, column=1, sticky="w", padx=6, pady=2)

        self.height_radio = ttk.Radiobutton(
            raster_frame, text="Theo chiều cao thật (mm)", value="height",
            variable=self.raster_scale_mode, command=self._update_raster_scale_state,
        )
        self.height_radio.grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.height_entry = ttk.Entry(raster_frame, textvariable=self.height_mm_var, width=12)
        self.height_entry.grid(row=2, column=1, sticky="w", padx=6, pady=2)

        self.dpi_radio = ttk.Radiobutton(
            raster_frame, text="Theo độ phân giải ảnh gốc (DPI)", value="dpi",
            variable=self.raster_scale_mode, command=self._update_raster_scale_state,
        )
        self.dpi_radio.grid(row=3, column=0, sticky="w", padx=6, pady=2)
        self.dpi_entry = ttk.Entry(raster_frame, textvariable=self.dpi_var, width=12)
        self.dpi_entry.grid(row=3, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(
            raster_frame,
            text="VD: ảnh ghi chú '300 DPI' thì nhập 300 vào đây (KHÔNG phải pixels/mm).",
            **_HINT_STYLE,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        ttk.Label(raster_frame, text="Ngưỡng đen/trắng (0-255):").grid(row=5, column=0, sticky="w", padx=6, pady=2)
        ttk.Entry(raster_frame, textvariable=self.raster_threshold_var, width=10) \
            .grid(row=5, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(
            raster_frame,
            text="Để trống = tự động (Otsu). Chỉ tự chỉnh khi ảnh có bóng đổ hoặc gradient.",
            **_HINT_STYLE,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        ttk.Checkbutton(
            raster_frame,
            text="Đảo màu đen/trắng (dùng khi ảnh nền tối, hình sáng)",
            variable=self.invert_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6))

        # -- Hành động -------------------------------------------------------
        action_row = ttk.Frame(self.root)
        action_row.pack(fill="x", **pad)
        self.run_button = ttk.Button(action_row, text="Xử lý", command=self._on_run_clicked)
        self.run_button.pack(side="left")
        self.open_output_button = ttk.Button(
            action_row, text="Mở thư mục kết quả", command=self._open_output_dir, state="disabled"
        )
        self.open_output_button.pack(side="left", padx=(8, 0))

        # -- Kết quả -------------------------------------------------------
        log_frame = ttk.LabelFrame(self.root, text="Kết quả")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=14)
        self.log_text.pack(side="left", fill="both", expand=True)
        for level, color in _LOG_COLORS.items():
            self.log_text.tag_configure(level, foreground=color)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text["yscrollcommand"] = scrollbar.set

    def _on_weld_combo_changed(self, _event=None) -> None:
        index = self.weld_combo.current()
        self.weld_mode_var.set(WELD_MODES[index][0])

    def _update_raster_scale_state(self) -> None:
        mode = self.raster_scale_mode.get()
        self.width_entry["state"] = "normal" if mode == "width" else "disabled"
        self.height_entry["state"] = "normal" if mode == "height" else "disabled"
        self.dpi_entry["state"] = "normal" if mode == "dpi" else "disabled"

    # -- Chọn file/thư mục ----------------------------------------------------

    def _choose_input_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("DXF/Ảnh", "*.dxf *.png *.jpg *.jpeg"), ("Tất cả", "*.*")]
        )
        if path:
            self.input_path_var.set(path)

    def _choose_input_dir(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.input_path_var.set(path)

    def _choose_output(self) -> None:
        input_path = self.input_path_var.get().strip()
        if input_path and Path(input_path).is_dir():
            path = filedialog.askdirectory()
        else:
            path = filedialog.asksaveasfilename(defaultextension=".dxf", filetypes=[("DXF", "*.dxf")])
        if path:
            self.output_path_var.set(path)

    def _on_drop(self, event) -> None:
        data = event.data
        path = self.root.tk.splitlist(data)[0] if data else ""
        if path:
            self.input_path_var.set(path)

    def _open_output_dir(self) -> None:
        if self._last_output_dir is None:
            return
        import subprocess
        import sys

        if sys.platform == "win32":
            import os
            os.startfile(self._last_output_dir)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(self._last_output_dir)])
        else:
            subprocess.run(["xdg-open", str(self._last_output_dir)])

    # -- Chạy pipeline ------------------------------------------------------

    def _float_or_none(self, s: str) -> float | None:
        s = s.strip()
        return float(s) if s else None

    def _int_or_none(self, s: str) -> int | None:
        s = s.strip()
        return int(s) if s else None

    def _is_raster_input(self, path: Path) -> bool:
        if path.is_dir():
            return any(f.suffix.lower() in RASTER_SUFFIXES for f in path.iterdir())
        return path.suffix.lower() in RASTER_SUFFIXES

    def _on_run_clicked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        input_str = self.input_path_var.get().strip()
        if not input_str:
            messagebox.showerror("Lỗi", "Chọn file hoặc thư mục đầu vào trước.")
            return
        input_path = Path(input_str)
        if not input_path.exists():
            messagebox.showerror("Lỗi", f"Không tìm thấy: {input_path}")
            return

        try:
            snap_tol = self._float_or_none(self.snap_tol_var.get())
            raster_threshold = self._int_or_none(self.raster_threshold_var.get())
            thickness = self._float_or_none(self.thickness_var.get())

            width_mm = height_mm = px_per_mm = None
            mode = self.raster_scale_mode.get()
            is_raster_input = self._is_raster_input(input_path)
            if mode == "width":
                width_mm = self._float_or_none(self.width_mm_var.get())
                if is_raster_input and width_mm is None:
                    messagebox.showerror("Lỗi", "Đã chọn 'Theo chiều rộng thật (mm)' nhưng ô này đang trống.")
                    return
            elif mode == "height":
                height_mm = self._float_or_none(self.height_mm_var.get())
                if is_raster_input and height_mm is None:
                    messagebox.showerror("Lỗi", "Đã chọn 'Theo chiều cao thật (mm)' nhưng ô này đang trống.")
                    return
            else:
                dpi = self._float_or_none(self.dpi_var.get())
                if is_raster_input and dpi is None:
                    messagebox.showerror("Lỗi", "Đã chọn 'Theo độ phân giải ảnh gốc (DPI)' nhưng ô này đang trống.")
                    return
                px_per_mm = (dpi / MM_PER_INCH) if dpi is not None else None
        except ValueError:
            messagebox.showerror("Lỗi", "Các trường số phải là số hợp lệ.")
            return

        for label, value in (("chiều rộng (mm)", width_mm), ("chiều cao (mm)", height_mm)):
            if value is not None and value <= 0:
                messagebox.showerror("Lỗi", f"Giá trị {label} phải lớn hơn 0.")
                return
        if px_per_mm is not None and px_per_mm <= 0:
            messagebox.showerror("Lỗi", "DPI phải lớn hơn 0.")
            return

        output_str = self.output_path_var.get().strip()
        output_path = Path(output_str) if output_str else None

        weld_mode = self.weld_mode_var.get().strip() or None

        config = load_config(None)
        config = apply_overrides(config, snap_tol, weld_mode, self.no_simplify_var.get())
        config = apply_raster_overrides(config, px_per_mm, self.invert_var.get(), raster_threshold)
        config = apply_thickness_override(config, thickness)

        self._clear_log()
        self.run_button["state"] = "disabled"
        self.open_output_button["state"] = "disabled"
        check = self.check_var.get()
        force = self.force_var.get()

        self._worker = threading.Thread(
            target=self._run_worker,
            args=(input_path, output_path, config, check, force, width_mm, height_mm),
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self, input_path, output_path, config, check, force, width_mm, height_mm) -> None:
        def log(level: str, message: str) -> None:
            self._log_queue.put((level, message))

        try:
            worst, report_path = process_path(
                input_path, output_path, config, check, log, width_mm, height_mm, force
            )
            if report_path is not None:
                log("info", f"Báo cáo tổng hợp: {report_path}")
                out_dir = report_path.parent
            else:
                out_dir = (output_path.parent if output_path is not None else input_path.parent)
            self._log_queue.put(("__done__", (worst, out_dir)))
        except Exception as exc:
            log("critical", f"Lỗi hệ thống: {exc}")
            self._log_queue.put(("__done__", (3, None)))

    def _clear_log(self) -> None:
        self.log_text["state"] = "normal"
        self.log_text.delete("1.0", "end")
        self.log_text["state"] = "disabled"

    def _append_log(self, level: str, message: str) -> None:
        self.log_text["state"] = "normal"
        self.log_text.insert("end", message + "\n", level if level in _LOG_COLORS else "info")
        self.log_text.see("end")
        self.log_text["state"] = "disabled"

    def _drain_log_queue(self) -> None:
        try:
            while True:
                level, payload = self._log_queue.get_nowait()
                if level == "__done__":
                    worst, out_dir = payload
                    self.run_button["state"] = "normal"
                    self._last_output_dir = out_dir
                    if out_dir is not None:
                        self.open_output_button["state"] = "normal"
                    self._append_log("info", f"--- Hoàn tất: {_RESULT_LABELS.get(worst, worst)} ---")
                else:
                    self._append_log(level, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)


def main() -> None:
    root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
