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

WELD_MODES = ["off", "overlapping", "all"]


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("DXF Cleaner")
        root.geometry("640x600")
        root.minsize(560, 480)

        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.check_var = tk.BooleanVar(value=False)
        self.snap_tol_var = tk.StringVar()
        self.weld_mode_var = tk.StringVar(value="")
        self.no_simplify_var = tk.BooleanVar(value=False)
        self.width_mm_var = tk.StringVar()
        self.height_mm_var = tk.StringVar()
        self.px_per_mm_var = tk.StringVar()
        self.invert_var = tk.BooleanVar(value=False)
        self.raster_threshold_var = tk.StringVar()
        self.thickness_var = tk.StringVar()

        self._log_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

        self._build_widgets()
        self.root.after(100, self._drain_log_queue)

    # -- UI layout -----------------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        io_frame = ttk.LabelFrame(self.root, text="Input / Output")
        io_frame.pack(fill="x", **pad)

        drop_label = "File hoac thu muc (co the keo-tha vao day)" if _HAS_DND else "File hoac thu muc"
        ttk.Label(io_frame, text=drop_label).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
        input_row = ttk.Frame(io_frame)
        input_row.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        input_row.columnconfigure(0, weight=1)
        input_entry = ttk.Entry(input_row, textvariable=self.input_path_var)
        input_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(input_row, text="Chon file...", command=self._choose_input_file).grid(row=0, column=1, padx=(4, 0))
        ttk.Button(input_row, text="Chon thu muc...", command=self._choose_input_dir).grid(row=0, column=2, padx=(4, 0))

        if _HAS_DND:
            input_entry.drop_target_register(DND_FILES)
            input_entry.dnd_bind("<<Drop>>", self._on_drop)

        ttk.Label(io_frame, text="Output (bo trong = ghi canh file goc)").grid(row=2, column=0, sticky="w", padx=6)
        output_row = ttk.Frame(io_frame)
        output_row.grid(row=3, column=0, sticky="ew", padx=6, pady=(0, 6))
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_row, text="Chon...", command=self._choose_output).grid(row=0, column=1, padx=(4, 0))

        io_frame.columnconfigure(0, weight=1)

        opts_frame = ttk.LabelFrame(self.root, text="Tuy chon xu ly")
        opts_frame.pack(fill="x", **pad)
        for col in range(4):
            opts_frame.columnconfigure(col, weight=1)

        ttk.Checkbutton(opts_frame, text="Chi kiem tra (khong ghi file)", variable=self.check_var) \
            .grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(opts_frame, text="Tat simplify", variable=self.no_simplify_var) \
            .grid(row=0, column=2, columnspan=2, sticky="w", padx=6, pady=2)

        self._labeled_entry(opts_frame, "Snap tolerance", self.snap_tol_var, row=1, col=0)
        self._weld_combo(opts_frame, row=1, col=2)

        self._labeled_entry(opts_frame, "Do day vat lieu (mm)", self.thickness_var, row=2, col=0)
        self._labeled_entry(opts_frame, "Width (mm, raster)", self.width_mm_var, row=2, col=2)

        self._labeled_entry(opts_frame, "Height (mm, raster)", self.height_mm_var, row=3, col=0)
        self._labeled_entry(opts_frame, "Pixels/mm (raster)", self.px_per_mm_var, row=3, col=2)

        self._labeled_entry(opts_frame, "Raster threshold (0-255)", self.raster_threshold_var, row=4, col=0)
        ttk.Checkbutton(opts_frame, text="Dao mau (raster)", variable=self.invert_var) \
            .grid(row=4, column=2, columnspan=2, sticky="w", padx=6, pady=2)

        action_row = ttk.Frame(self.root)
        action_row.pack(fill="x", **pad)
        self.run_button = ttk.Button(action_row, text="Xu ly", command=self._on_run_clicked)
        self.run_button.pack(side="left")
        self.open_output_button = ttk.Button(
            action_row, text="Mo thu muc ket qua", command=self._open_output_dir, state="disabled"
        )
        self.open_output_button.pack(side="left", padx=(8, 0))

        log_frame = ttk.LabelFrame(self.root, text="Ket qua")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=14)
        self.log_text.pack(side="left", fill="both", expand=True)
        for level, color in _LOG_COLORS.items():
            self.log_text.tag_configure(level, foreground=color)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text["yscrollcommand"] = scrollbar.set

        self._last_output_dir: Path | None = None

    def _labeled_entry(self, parent, label, var, row, col) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=6, pady=2)
        ttk.Entry(parent, textvariable=var, width=12).grid(row=row, column=col + 1, sticky="w", padx=6, pady=2)

    def _weld_combo(self, parent, row, col) -> None:
        ttk.Label(parent, text="Weld mode").grid(row=row, column=col, sticky="w", padx=6, pady=2)
        combo = ttk.Combobox(
            parent, textvariable=self.weld_mode_var, values=[""] + WELD_MODES, width=10, state="readonly"
        )
        combo.grid(row=row, column=col + 1, sticky="w", padx=6, pady=2)

    # -- File pickers ----------------------------------------------------

    def _choose_input_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("DXF/Raster", "*.dxf *.png *.jpg *.jpeg"), ("Tat ca", "*.*")]
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

    # -- Run pipeline ------------------------------------------------------

    def _float_or_none(self, s: str) -> float | None:
        s = s.strip()
        return float(s) if s else None

    def _int_or_none(self, s: str) -> int | None:
        s = s.strip()
        return int(s) if s else None

    def _on_run_clicked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        input_str = self.input_path_var.get().strip()
        if not input_str:
            messagebox.showerror("Loi", "Chon file hoac thu muc dau vao truoc.")
            return
        input_path = Path(input_str)
        if not input_path.exists():
            messagebox.showerror("Loi", f"Khong tim thay: {input_path}")
            return

        try:
            width_mm = self._float_or_none(self.width_mm_var.get())
            height_mm = self._float_or_none(self.height_mm_var.get())
            snap_tol = self._float_or_none(self.snap_tol_var.get())
            px_per_mm = self._float_or_none(self.px_per_mm_var.get())
            raster_threshold = self._int_or_none(self.raster_threshold_var.get())
            thickness = self._float_or_none(self.thickness_var.get())
        except ValueError:
            messagebox.showerror("Loi", "Cac truong so phai la so hop le.")
            return

        if width_mm is not None and height_mm is not None:
            messagebox.showerror("Loi", "Width-mm va height-mm khong the dung cung luc.")
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

        self._worker = threading.Thread(
            target=self._run_worker,
            args=(input_path, output_path, config, check, width_mm, height_mm),
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self, input_path, output_path, config, check, width_mm, height_mm) -> None:
        def log(level: str, message: str) -> None:
            self._log_queue.put((level, message))

        try:
            worst, report_path = process_path(input_path, output_path, config, check, log, width_mm, height_mm)
            if report_path is not None:
                log("info", f"Batch report: {report_path}")
                out_dir = report_path.parent
            else:
                out_dir = (output_path.parent if output_path is not None else input_path.parent)
            self._log_queue.put(("__done__", (worst, out_dir)))
        except Exception as exc:
            log("critical", f"System error: {exc}")
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
                    labels = {0: "OK", 1: "CANH BAO", 2: "LOI NGHIEM TRONG", 3: "LOI HE THONG"}
                    self._append_log("info", f"--- Hoan tat: {labels.get(worst, worst)} ---")
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
