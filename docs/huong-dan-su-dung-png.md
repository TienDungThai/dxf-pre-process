# HƯỚNG DẪN SỬ DỤNG `dxfclean` — Input là ảnh PNG/JPG

Áp dụng khi khách gửi ảnh (PNG, JPG) thay vì file DXF/vector. `dxfclean` tự nhận diện đuôi file và trace ảnh thành contour, sau đó chạy qua đúng cùng một pipeline làm sạch (dedupe, despeckle, weld, dò biên ngoài/lỗ, giảm node, kiểm tra) như khi nhận DXF.

---

## 1. Trước khi chạy: hỏi file vector gốc

Giống quy trình PNG2DXF cũ: **luôn hỏi khách có file AI/EPS/PDF/SVG/DXF trước.** Ảnh PNG chỉ nên dùng khi khách không có gì khác — sai số biên luôn tồn tại khi trace từ ảnh, và không thể phục hồi chi tiết nhỏ hơn độ phân giải ảnh gốc.

Công thức ước lượng độ phân giải cần thiết:

```
số pixel cần = khổ cắt (mm) ÷ 25,4 × 300
```

| Khổ cắt | Ảnh cần tối thiểu (300 DPI) |
|---|---|
| 100 mm | 1180 px |
| 200 mm | 2360 px |
| 300 mm | 3540 px |
| 500 mm | 5900 px |

Ảnh dưới 150 DPI hiệu dụng thì nên từ chối hoặc báo khách gửi lại — công cụ sẽ cảnh báo `RASTER_LOW_DPI` nhưng không tự chặn, xem mục 5.

---

## 2. Cài đặt / môi trường

### Trên Windows (xưởng) — dùng file có sẵn, không cần gõ lệnh

Repo có sẵn 2 file ở thư mục gốc:

- **`CAI-DAT.bat`** — chạy 1 lần duy nhất khi mới nhận máy/mới clone repo. Kiểm tra Python, tự cài `dxfclean` cùng mọi thư viện cần thiết (Pillow, opencv, scikit-image, scipy...).
- **`CHAY.bat`** — dùng hằng ngày. Kéo-thả file (`.dxf`, `.png`, `.jpg`, hoặc cả một thư mục) vào file này, nhập khổ rộng (mm) và độ dày tôn (mm) khi được hỏi. Kết quả nằm trong thư mục `ket-qua\` cạnh `CHAY.bat`.

Nếu báo `python is not recognized`: cài lại Python từ https://www.python.org/downloads/, nhớ tick **"Add Python to PATH"**.

### Bản `.exe` độc lập — không cần cài Python trên máy xưởng

Repo có workflow tự động (`.github/workflows/build-windows.yml`) build sẵn `dxfclean.exe` trên máy ảo Windows của GitHub mỗi khi có cập nhật lên nhánh `master`. Tải file này về từ tab **Actions** trên GitHub (chọn lần chạy mới nhất → mục Artifacts → `dxfclean-windows`).

Dùng y hệt cú pháp dòng lệnh, chỉ đổi `python -m dxf_cleaner.cli` thành tên file exe:

```
dxfclean.exe logo.png -o logo.dxf -w 200 -t 2
```

**Lưu ý:** đây chỉ là đóng gói bytecode vào 1 file chạy được (PyInstaller), **không phải mã hóa** — không chống được kỹ sư cố tình dịch ngược, chỉ hạn chế việc mở xem/copy tình cờ. File khá nặng (~150–300MB do có opencv/scipy/scikit-image) và load chậm vài giây mỗi lần chạy.

### Chạy tay bằng dòng lệnh (mọi hệ điều hành)

Cài lần đầu (đã bao gồm các thư viện xử lý ảnh: Pillow, opencv, scikit-image, scipy):

```bash
pip install -e .
```

Chạy công cụ bằng một trong hai cách tương đương:

```bash
dxfclean <input> [options]
```

hoặc nếu lệnh `dxfclean` báo "command not found" / "No module named dxf_cleaner" (thường do môi trường pip bị lỗi cài lại entry point):

```bash
python -m dxf_cleaner.cli <input> [options]
```

---

## 3. Cú pháp cơ bản

```bash
dxfclean logo.png -o logo.dxf -w 200
```

Kết quả:
- `logo.dxf` — file DXF sạch, đơn vị mm, một layer `CUT`.
- `logo_KIEMTRA.png` — **ảnh kiểm tra bắt buộc**, ghi cạnh file ảnh input (không phải cạnh output), luôn được tạo ra kể cả khi có lỗi hoặc chạy `--check`.
- In ra màn hình: mức độ (`ok` / `warning` / `critical`), danh sách cảnh báo/lỗi, và các số liệu (`info`) của lần chạy.

### Các tham số riêng cho ảnh raster

| Tham số | Ý nghĩa |
|---|---|
| `-w, --width-mm FLOAT` | Chiều rộng đích, tính bằng mm. Bắt buộc phải có 1 trong 3 cách xác định tỉ lệ (`-w`, `-H`, hoặc `--px-per-mm`/config). |
| `-H, --height-mm FLOAT` | Chiều cao đích, tính bằng mm. **Không dùng đồng thời** với `-w` (loại trừ lẫn nhau). |
| `--px-per-mm FLOAT` | Ghi đè `raster.pixels_per_mm` — dùng khi biết trước tỉ lệ pixel/mm chính xác thay vì suy từ khổ cắt. |
| `--invert` | Đảo đen/trắng — dùng khi ảnh nền tối, hình sáng (xem mục 6). |
| `--raster-threshold INT` | Ghi đè ngưỡng nhị phân hóa (0–255) thay vì tự động (Otsu). Chỉ dùng khi xử lý sự cố. |
| `-o, --output PATH` | File DXF đích (chế độ 1 file) hoặc thư mục đích (chế độ thư mục). |
| `--check` | Chỉ báo cáo, không ghi DXF. **Vẫn ghi ảnh `_KIEMTRA.png`.** |
| `--config PATH` | File `config.yaml` — dùng để đặt `validate.material_thickness` (độ dày tôn), vì hiện chưa có flag `-t` riêng trên CLI (xem mục 4). |
| `--snap-tol`, `--weld-mode`, `--no-simplify` | Giống hệt luồng DXF, áp dụng chung cho cả ảnh raster. |

Các tham số dưới đây **áp dụng cho DXF, bị bỏ qua với input ảnh**: không có — chỉ 5 tham số ở bảng trên là raster-only, còn lại (`--snap-tol`, `--weld-mode`, `--no-simplify`) dùng chung.

---

## 4. Khai báo độ dày tôn (material thickness)

CLI hiện **chưa có flag riêng** cho độ dày tôn. Phải tạo file config nhỏ và truyền qua `--config`:

```yaml
# config.yaml
validate:
  material_thickness: 2.0
```

```bash
dxfclean logo.png -o logo.dxf -w 200 --config config.yaml
```

Nếu không khai báo, `material_thickness` mặc định là `2.0` (mm) — kiểm tra kỹ giá trị mặc định có đúng đơn hàng không trước khi tin vào kết quả `ok`.

---

## 5. Đọc kết quả in ra màn hình

Ví dụ output thật (đã chạy thử với logo xưởng):

```
/path/logo.png: warning
  WARNING: RASTER_LOW_DPI: Effective resolution 163 DPI is below 300; edges may deviate by roughly 0.078mm
  WARNING: Hole 'raster-31' diameter 1.859mm is smaller than the material-thickness-derived minimum 2.000mm
  dpi: 162.56
  height_mm: 80.31
  min_feature_width_mm: 0.31
  mm_per_px: 0.15625
  n_parts: 2
  node_count_after: 7216
  node_count_before: 15576
  threshold: 127.0
  width_mm: 77.49
```

### Mức độ (level)

| Mức | Ý nghĩa | Có ghi DXF không |
|---|---|---|
| `ok` | Không phát hiện vấn đề | Có |
| `warning` | Có điểm cần người vận hành xem lại, nhưng vẫn xuất được | Có |
| `critical` | Có lỗi nghiêm trọng, **không xuất DXF** | Không — chỉ ảnh `_KIEMTRA.png` được ghi |

### Các mã cảnh báo/lỗi riêng của input ảnh (raster-specific)

| Mã | Loại | Ý nghĩa | Xử lý |
|---|---|---|---|
| `RASTER_LOW_DPI` | Warning | Độ phân giải hiệu dụng < 300 DPI, biên có thể lệch khoảng `mm_per_px / 2` | Nếu lệch quá lớn so với yêu cầu, báo khách gửi ảnh nét hơn |
| `RASTER_POSSIBLE_INVERTED` | Warning | Tỉ lệ pixel "giữ lại" quá cao hoặc viền ảnh gần như toàn là ink — nghi ảnh bị đảo đen/trắng | Mở `_KIEMTRA.png`, nếu phần xám nằm ở chỗ đáng lẽ là nền thì chạy lại với `--invert` |
| Thinnest feature < độ dày tôn | **Critical** | Nét mảnh nhất nhỏ hơn độ dày tôn — không cắt được | Tăng khổ chi tiết, đổi tôn mỏng hơn, hoặc từ chối đơn |
| Thinnest feature < 2× độ dày tôn | Warning | Nét mảnh nhất dưới ngưỡng an toàn (2 lần độ dày tôn) — rủi ro cong vênh/cháy cạnh | Báo tổ trưởng trước khi cắt |
| Hole diameter nhỏ hơn ngưỡng | Warning | Lỗ nhỏ hơn mức tối thiểu suy ra từ độ dày tôn | Xem lại thiết kế, có thể phải bỏ lỗ đó |

### Các số liệu (info) cần đối chiếu

| Trường | Ý nghĩa | Cần chú ý |
|---|---|---|
| `width_mm` / `height_mm` | Kích thước thật sẽ cắt ra | **Đối chiếu đơn hàng** |
| `dpi` | Độ phân giải hiệu dụng | Dưới 300 là thiếu nét, dưới 150 nên báo khách |
| `mm_per_px` | Một pixel bằng bao nhiêu mm | Sai số biên khoảng nửa giá trị này |
| `min_feature_width_mm` | Nét mảnh nhất đo được | Phải lớn hơn 2× độ dày tôn |
| `n_parts` | Số mảnh rời (từ đo skeleton, không phải số Part sau cùng) | Lớn hơn 1 thì cân nhắc micro joint khi qua CypCut |
| `node_count_before` / `node_count_after` | Số node trước/sau khi giảm | Nếu `after` không giảm nhiều so với `before`, xem mục 7 |
| `threshold` | Ngưỡng đen/trắng đã dùng (Otsu tự động nếu không truyền `--raster-threshold`) | Chỉ dùng khi xử lý sự cố |

---

## 6. Quy trình 6 bước hằng ngày

### Bước 1 — Hỏi file vector gốc
Xem mục 1.

### Bước 2 — Kiểm tra ảnh đủ nét chưa
Xem bảng ở mục 1. Thiếu quá 2 lần thì báo khách gửi lại — phóng to ảnh không tạo thêm chi tiết.

### Bước 3 — Chạy `dxfclean`

```bash
dxfclean logo.png -o ket-qua/logo.dxf -w <chiều_rộng_mm> --config config.yaml
```

### Bước 4 — Đọc kết quả trên màn hình
Xem mục 5. Chỉ `ok`/`warning` mới có file DXF được ghi ra.

### Bước 5 — Mở ảnh `_KIEMTRA.png` — bắt buộc, không có ngoại lệ

Ảnh ghi cạnh **file ảnh input**, không phải cạnh file DXF output. Ví dụ input `.../logo.png` → ảnh kiểm tra là `.../logo_KIEMTRA.png` trong cùng thư mục.

| Màu trên ảnh | Nghĩa |
|---|---|
| Xám | Phần được giữ lại (đã nhị phân hóa) |
| Trắng | Phần bỏ đi (nền) |
| Đường viền (xanh) | Biên đã trace được — hiện tại bản này **chưa phân biệt màu biên ngoài và biên lỗ** (cả hai đều vẽ cùng màu), khác với công cụ `png2dxf.py` cũ. Xem mục 8. |

Ba việc phải xác nhận bằng mắt:
1. Phần xám có đúng là phần cần giữ lại không? Nếu ngược thì ảnh bị đảo đen/trắng — chạy lại với `--invert`.
2. Hình có đủ chi tiết không? Thiếu nét nhỏ thì xem mục 7.
3. Số lỗ/mảnh trace được (đếm bằng mắt) có khớp với thiết kế không?

### Bước 6 — Bàn giao
File `.dxf` đưa thẳng vào CypCut, giống hệt luồng DXF thường — không cần chạy `SCALE`, đơn vị và tỉ lệ đã đúng mm.

---

## 7. Xử lý sự cố

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `RASTER_POSSIBLE_INVERTED` hoặc ảnh kiểm tra xám nằm ở phần nền | Ảnh nền tối, hình sáng (hoặc ngược lại so với giả định) | Thêm `--invert` |
| `node_count_after` gần bằng `node_count_before` (giảm node không hiệu quả) | Contour bị coi là "đã phẳng" nhưng chưa qua bước giảm node đúng cách, hoặc tolerance quá nhỏ | Kiểm tra bản `dxfclean` đang dùng có mới nhất chưa (bug từng gặp: raster input bỏ qua bước simplify — đã fix); nếu vẫn không giảm, tăng `simplify.tolerance` qua `--config` |
| Biên gợn sóng, lởm chởm | Ảnh có bóng đổ/gradient, ngưỡng Otsu tự động chọn sai | Thử `--raster-threshold 128` (hoặc giá trị khác) |
| Mất chi tiết nhỏ | Bộ lọc nhiễu tối thiểu (`raster.min_area_px`) quá cao, hoặc đốm nhiễu bị `despeckle` xóa | Giảm `raster.min_area_px` qua `--config` |
| `CONTOUR_OPENED_BY_DEDUPE` / `OPEN_CONTOUR_SKIPPED_FROM_HIERARCHY` (critical) | Ảnh quá thấp DPI so với khổ cắt yêu cầu — các đoạn sub-pixel dày đặc gây lỗi khi ghép/khử trùng | Yêu cầu ảnh gốc nét hơn, hoặc giảm khổ cắt xuống mức phù hợp với độ phân giải ảnh (xem `dpi` trong info) |
| Nét mảnh nhất < độ dày tôn (critical) | Chi tiết thiết kế nhỏ hơn khả năng cắt của tôn đang chọn | Tăng khổ chi tiết, đổi tôn mỏng hơn, hoặc từ chối đơn — không tự ý cắt |
| `dxfclean: command not found` hoặc `ModuleNotFoundError: No module named 'dxf_cleaner'` | Entry point cài lỗi (thường do lỗi cache/TLS của pip khi `pip install -e .`) | Dùng `python -m dxf_cleaner.cli ...` thay cho `dxfclean ...` |
| Báo thiếu thư viện (`cv2`, `PIL`, `skimage`, `scipy`...) | Chưa cài đủ dependency | Chạy lại `pip install -e .` |

Sau mỗi lần chạy lại với tham số mới, **phải mở lại `_KIEMTRA.png`**.

---

## 8. Khác biệt đã biết so với công cụ `png2dxf.py` cũ

- **Chưa có flag `-t`/`--thickness` riêng** — phải khai báo qua `--config` (mục 4).
- **Ảnh `_KIEMTRA.png` chưa phân biệt màu biên ngoài (xanh lá) và biên lỗ (xanh dương)** như bản cũ — mọi biên hiện vẽ cùng màu, vì việc phân loại biên-ngoài/lỗ chỉ xảy ra ở bước xử lý sau khi ảnh kiểm tra đã được ghi. Đây là giới hạn đã biết, không phải lỗi.
- **Không có báo cáo CSV hàng loạt** (`BAO-CAO.csv`) như bản cũ khi chạy cả thư mục — mỗi file in báo cáo riêng ra màn hình. Chạy chế độ thư mục (`dxfclean thumuc/ -o ketqua/ -w 200`) vẫn hoạt động, nhưng không tổng hợp thành 1 file CSV.
- **Không đo và cảnh báo số mảnh rời (`n_parts`) như một cảnh báo micro-joint riêng** — số liệu `n_parts` vẫn có trong info nhưng không tự động nhắc "phải đặt micro joint" như bản cũ; người vận hành tự đối chiếu.

Nếu khối lượng công việc lớn hoặc cần các tính năng trên, cân nhắc dùng `png2dxf.py` (script cũ, độc lập) cho những đơn hàng đó, hoặc yêu cầu bổ sung tính năng vào `dxfclean`.

---

## 9. Cách đọc cảnh báo "nét mảnh" cho đúng — đầu nhọn lồi khác cầu nối mỏng

`min_feature_width_mm` và các vùng đỏ trên `_KIEMTRA.png` đo **mù theo bề rộng cục bộ** (khoảng cách tới biên gần nhất × 2), không phân biệt được 2 tình huống có rủi ro rất khác nhau khi cắt thật:

- **Cầu nối mỏng (bridge/web)** — một dải kim loại mỏng nối 2 vùng lớn, bị cắt hở cả 2 bên (ví dụ chân chữ dính sát nhau, gọng kính). Loại này **dễ cong vênh, cháy cạnh, gãy khi thao tác** — đúng đối tượng mà quy tắc "nét mảnh nhất ≥ 2× độ dày tôn" nhắm tới. **Phải xử lý theo cảnh báo** (tăng khổ, đổi tôn, hoặc từ chối đơn).
- **Đầu nhọn lồi (convex tip)** — như đầu tia nắng, đầu vảy lông, mũi lá — chỉ có 1 cạnh là biên cắt, phía sau vẫn liền khối kim loại lớn, không bị treo lơ lửng hai đầu. Loại này **thường vẫn cắt được bình thường**, mũi nhọn chỉ hơi tù đi vài trăm micromet so với thiết kế — không phải nguy cơ cong vênh/rơi rớt như cầu nối, dù công thức đo vẫn báo động y hệt.

Vì công cụ không tự phân biệt được 2 loại này, khi gặp `CRITICAL`/`WARNING` do nét mảnh:

1. Mở `_KIEMTRA.png`, nhìn từng vùng đỏ.
2. Hỏi: **vùng đỏ này có 2 phía đều là khoảng trống (cắt hở cả 2 bên) hay chỉ 1 phía là biên, phía kia vẫn liền khối?**
   - 2 phía hở → cầu nối thật, làm theo cảnh báo (tăng khổ / đổi tôn / báo khách sửa file).
   - Chỉ 1 phía hở, đúng dạng đầu nhọn của một chi tiết trang trí (tia nắng, lông, gai...) → có thể chấp nhận cắt, không cần làm tù nét gốc, nhưng **vẫn phải báo tổ trưởng duyệt tay** trước khi cắt hàng loạt (đúng quy trình mục 6 bước 4-5), vì công cụ không tự động phân loại được.
3. Không tự ý bỏ qua cảnh báo mà không có người duyệt — đây là quyết định nghiệp vụ, không phải lỗi phần mềm.

*(Ghi chú kỹ thuật: việc dạy công cụ tự phân biệt 2 trường hợp này — ví dụ chỉ báo CRITICAL cho các điểm mảnh nằm giữa 2 vùng rỗng, hạ mức đầu nhọn lồi xuống INFO — đang được cân nhắc bổ sung, chưa có trong bản hiện tại.)*

---

## 10. Ví dụ đầy đủ

```bash
# Tạo config khai báo độ dày tôn 1 lần, tái sử dụng cho nhiều đơn
cat > config.yaml <<EOF
validate:
  material_thickness: 2.0
EOF

# Chạy 1 file, biết trước khổ rộng 200mm
dxfclean logo.png -o ket-qua/logo.dxf -w 200 --config config.yaml

# Ảnh nghi bị đảo màu
dxfclean logo.png -o ket-qua/logo.dxf -w 200 --config config.yaml --invert

# Chạy cả thư mục ảnh, cùng một khổ rộng cho tất cả
dxfclean anh-dat-hang/ -o ket-qua/ -w 200 --config config.yaml

# Chỉ muốn xem báo cáo và ảnh kiểm tra, chưa muốn xuất DXF
dxfclean logo.png -w 200 --config config.yaml --check
```
