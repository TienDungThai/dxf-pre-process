# Roadmap cải tiến `dxfclean` — thu hẹp khoảng cách với `png2dxf.py`

Nguồn: mục 8 của [huong-dan-su-dung-png.md](huong-dan-su-dung-png.md) — các điểm `dxfclean` hiện đang kém hơn công cụ `png2dxf.py` cũ. Ghi lại thành roadmap để không quên và có thứ tự ưu tiên khi làm.

Không mục nào ở đây ảnh hưởng đến độ chính xác cắt (accuracy) — toàn bộ là tiện ích vận hành (UX/workflow). Việc cắt đã đúng nhờ tái dùng chung pipeline với DXF.

---

## 1. Flag `-t/--thickness` trên CLI

**Hiện trạng:** phải tạo file `config.yaml` khai báo `validate.material_thickness` rồi truyền qua `--config` — vòng qua nhiều bước hơn cần thiết cho việc chỉ đổi 1 con số mỗi đơn hàng.

**Đề xuất:** thêm `-t, --thickness FLOAT` vào `cli.py`, override `config.validate.material_thickness` — theo đúng pattern override đã có sẵn cho `--snap-tol`/`--weld-mode`/`--px-per-mm`.

**Độ khó:** thấp — 1 option + 1 dòng override, giống hệt các override khác đã có.

**Ưu tiên:** cao — dùng hằng ngày, mỗi đơn hàng đều cần đổi số này.

---

## 2. Ảnh `_KIEMTRA.png` phân biệt màu biên ngoài / biên lỗ

**Hiện trạng:** mọi ranh giới vẽ cùng một màu trong `render_preview`, vì phân loại biên-ngoài/lỗ (`build_hierarchy`) chỉ chạy sau khi ảnh kiểm tra đã được ghi (bên trong `read_raster`, trước khi vào pipeline chính).

**Đề xuất:** dời lệnh gọi `render_preview` ra khỏi `read_raster`, chuyển sang gọi ở `pipeline.py`/`cli.py` **sau** bước `build_hierarchy` — lúc đó đã có `Part.exterior`/`Part.interiors` thật, truyền đúng `holes_by_ring` thay vì hard-code `[False] * n`.

**Rủi ro cần lưu ý:** `render_preview` hiện nhận `mask`/`dist`/`skel` (tính trong `read_raster`, không có sẵn ở `pipeline.py`) — cần hoặc (a) trả các mảng này ra kèm `ReadResult` để pipeline dùng lại, hoặc (b) giữ nguyên vị trí gọi nhưng truyền thêm thông tin phân loại hole/exterior vào sau khi có (yêu cầu đổi thứ tự pipeline — raster phải chạy `build_hierarchy` xong rồi mới ghi preview, tức là tách preview ra khỏi tất cả các bước read).

**Độ khó:** trung bình — đụng đến ranh giới giữa `raster.py` và `pipeline.py`, cần thiết kế lại luồng dữ liệu, không phải chỉ đổi 1 chỗ.

**Ưu tiên:** trung bình — không sai về mặt cắt, chỉ gây khó đọc ảnh kiểm tra cho người vận hành đã quen `png2dxf.py` cũ.

---

## 3. Báo cáo CSV khi chạy hàng loạt (thư mục)

**Hiện trạng:** chế độ thư mục (`dxfclean thumuc/ -o ketqua/`) chạy được nhưng chỉ in báo cáo từng file ra màn hình (stdout), không gom lại thành 1 file `BAO-CAO.csv` như `png2dxf.py`.

**Đề xuất:** trong `cli.py`, khi `input_path.is_dir()`, tích lũy `report.info` + `report.level` của từng file vào 1 danh sách, ghi ra `<output_dir>/BAO-CAO.csv` sau khi xử lý xong cả thư mục. Cột tối thiểu nên khớp với các trường đã có trong `report.info` (`width_mm`, `height_mm`, `dpi`, `min_feature_width_mm`, `n_parts`, `node_count_before/after`, `threshold`) cộng thêm `file`, `status`.

**Độ khó:** thấp-trung bình — logic gom dữ liệu đơn giản, chỉ cần đảm bảo hoạt động đúng cho cả input DXF (không có `raster_stats`) lẫn PNG trong cùng 1 thư mục hỗn hợp.

**Ưu tiên:** trung bình-cao nếu khối lượng xử lý hàng loạt lớn (theo tiêu chí cũ trong `QUY-TRINH-PNG-SANG-DXF.md`: >20 file/tuần).

---

## 4. Nhắc micro-joint tự động khi `n_parts > 1`

**Hiện trạng:** `n_parts` (đo bằng skeleton/connected components, xem `dxf_cleaner/raster.py::measure_min_width_px`) đã có trong `report.info`, nhưng không có dòng nhắc nhở rõ ràng như `png2dxf.py` ("N mảnh rời — phải đặt micro joint trong CypCut").

**Đề xuất:** trong `validate()`, khi `stats.get("n_parts", 1) > 1`, thêm một `warnings.append(...)` với nội dung tương tự — theo đúng pattern các cảnh báo khác đã có trong file (`min_feature_width_mm`, hole diameter...).

**Lưu ý:** `n_parts` hiện chỉ được raster tính ra (không có cho DXF) — cần `stats.get("n_parts")` trả `None` an toàn cho input DXF, giống cách `min_feature_width_mm` đã xử lý.

**Độ khó:** thấp — vài dòng trong `validate.py`, có thể làm cùng lúc với việc thêm flag `-t` (mục 1) vì đụng cùng file.

**Ưu tiên:** trung bình — thông tin đã có sẵn trong info, chỉ thiếu phần "nhắc" chủ động; người vận hành cẩn thận vẫn tự đọc được `n_parts` trong info hiện tại.

---

## Thứ tự đề xuất triển khai

1. **Mục 1** (flag `-t`) + **Mục 4** (nhắc micro-joint) — làm chung 1 đợt, cùng chạm `cli.py`/`validate.py`, độ khó thấp, giá trị vận hành ngay lập tức.
2. **Mục 3** (CSV hàng loạt) — độc lập, làm khi có nhu cầu chạy batch thật sự.
3. **Mục 2** (màu biên ngoài/lỗ trong ảnh kiểm tra) — để cuối vì cần thiết kế lại luồng dữ liệu giữa `raster.py` và `pipeline.py`, rủi ro cao hơn nếu làm vội.

Mỗi mục nên qua lại quy trình brainstorming → design ngắn → implement như đã làm với tính năng PNG→DXF ban đầu, vì đây vẫn là thay đổi vào code sản xuất, dù nhỏ.
