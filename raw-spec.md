# SPEC: DXF Cleaner — Công cụ chuẩn hóa file DXF cho máy cắt laser fiber

## 1. Bối cảnh và vấn đề

Xưởng cắt laser fiber (bộ điều khiển FSCUT/CypCut). Designer thiết kế trên CorelDRAW và xuất DXF. File DXF xuất ra không dùng trực tiếp được cho máy cắt vì:

- Corel làm việc với nét vẽ có độ dày (stroke) và mảng màu, không phải đường hình học đơn
- Các hình chồng lên nhau không được hợp nhất, tạo ra đường cắt trùng lặp
- Contour bị hở do sai số làm tròn khi xuất
- Nhiều layer rời rạc, không thống nhất
- Node rác, đoạn siêu nhỏ, spline bị flatten quá mịn

Hiện tại nhân viên phải ngồi dọn tay từng file: gộp layer, weld các hình chồng nhau, khép contour hở, xóa đường trùng. Mỗi file mất 10–20 phút. Đây là công việc lặp lại, cơ học, hoàn toàn tự động hóa được.

## 2. Mục tiêu

Xây dựng công cụ nhận DXF "bẩn" và xuất ra DXF "sạch" sẵn sàng import vào CypCut, không cần chỉnh sửa tay.

**Định nghĩa "sạch":**
- Toàn bộ hình học nằm trên một layer duy nhất
- Mọi contour khép kín (closed)
- Không có đường trùng lặp hoặc chồng lên nhau
- Các hình chồng nhau đã được hợp nhất thành một biên liền
- Quan hệ biên ngoài / lỗ trong được xác định đúng
- Không còn text, hatch, block, entity 3D
- Đơn vị mm, tọa độ Z = 0
- Số lượng node ở mức tối thiểu cần thiết

## 3. Phạm vi

### Trong phạm vi
- Đọc, phân tích, chuẩn hóa, ghi file DXF
- Xử lý hàng loạt cả thư mục
- Chế độ watch folder
- Báo cáo chi tiết những gì đã sửa và những gì không sửa được
- File cấu hình cho các ngưỡng dung sai

### NGOÀI phạm vi (không làm)
- **Nesting / sắp xếp chi tiết lên tấm** — CypNest lo
- **Bù mạch cắt (kerf compensation)** — CypCut lo. Bù hai lần sẽ sai kích thước.
- **Lead-in / lead-out, micro joint, thứ tự cắt** — CypCut lo
- **Thông số công nghệ cắt** (công suất, tốc độ, khí)
- Xuất G-code hoặc định dạng riêng của máy

Nguyên tắc: công cụ này chỉ chịu trách nhiệm về **hình học**, không đụng vào **công nghệ cắt**.

## 4. Ngăn xếp kỹ thuật

- **Python 3.11+**
- **ezdxf** — đọc/ghi DXF, truy cập entity cấp thấp
- **shapely 2.x** — toán hình học phẳng (union, buffer, simplify, quan hệ chứa nhau)
- **numpy** — tính toán vector
- **watchdog** — theo dõi thư mục
- **click** hoặc **typer** — CLI
- **pydantic** + **PyYAML** — cấu hình có kiểm tra kiểu
- **rich** — hiển thị tiến trình và báo cáo trên terminal
- **pytest** — kiểm thử

Không dùng CAD kernel thương mại. Không phụ thuộc AutoCAD hay Corel.

## 5. Kiến trúc

Pipeline nhiều tầng, mỗi tầng là một module độc lập, bật/tắt và chỉnh dung sai được qua config. Mỗi tầng nhận vào và trả ra một cấu trúc dữ liệu hình học nội bộ thống nhất, đồng thời ghi lại thống kê thay đổi.

```
dxf_cleaner/
├── __init__.py
├── cli.py                  # giao diện dòng lệnh
├── config.py               # schema cấu hình (pydantic)
├── pipeline.py             # điều phối các stage
├── model.py                # cấu trúc dữ liệu hình học nội bộ
├── reader.py               # DXF -> model
├── writer.py               # model -> DXF
├── report.py               # sinh báo cáo
├── watcher.py              # watch folder
└── stages/
    ├── explode.py          # phá block, xóa entity không hợp lệ
    ├── flatten.py          # chuyển spline/ellipse thành đường
    ├── snap.py             # snap endpoint, nối đoạn hở
    ├── dedupe.py           # xóa đường trùng và chồng nhau
    ├── despeckle.py        # xóa đối tượng siêu nhỏ
    ├── weld.py             # hợp nhất hình chồng nhau
    ├── hierarchy.py        # xác định biên ngoài / lỗ trong
    ├── simplify.py         # giảm node
    └── validate.py         # kiểm tra cuối, sinh cảnh báo
```

### 5.1. Cấu trúc dữ liệu nội bộ (`model.py`)

Điểm mấu chốt của toàn bộ thiết kế. Shapely **không có khái niệm cung tròn** — mọi thứ đều là đoạn thẳng. Nếu chuyển hết sang Shapely rồi ghi ngược ra, mọi đường tròn và cung sẽ biến thành polyline hàng trăm điểm. Điều này làm bộ điều khiển máy chạy giật, giảm tốc độ cắt thực tế và để lại vết trên biên cắt.

Giải pháp: mô hình lai.

```python
@dataclass
class Segment:
    """Một đoạn của contour. Giữ nguyên bản chất hình học."""
    kind: Literal["line", "arc"]
    start: tuple[float, float]
    end: tuple[float, float]
    # chỉ dùng khi kind == "arc"
    center: tuple[float, float] | None
    radius: float | None
    ccw: bool | None

@dataclass
class Contour:
    segments: list[Segment]
    is_closed: bool
    source_layer: str          # để truy vết
    source_handle: str         # handle của entity gốc trong DXF

    def to_shapely(self, arc_tolerance: float) -> LinearRing | LineString:
        """Rời rạc hóa để tính toán. KHÔNG dùng để ghi file."""

@dataclass
class Part:
    exterior: Contour
    interiors: list[Contour]
```

Quy tắc bắt buộc: **Shapely chỉ dùng để tính toán và ra quyết định. Khi ghi file, luôn ghi từ `Segment` gốc, không ghi từ Shapely.** Chỉ những contour thực sự bị biến đổi (đã weld, đã snap) mới ghi từ dạng rời rạc hóa.

## 6. Chi tiết từng tầng xử lý

### 6.1. `reader.py` — Đọc DXF

Xử lý được các entity sau:

| Entity | Cách xử lý |
|---|---|
| `LINE` | → Segment(line) |
| `ARC` | → Segment(arc), **giữ nguyên** |
| `CIRCLE` | → Contour khép kín gồm 2 Segment(arc), **giữ nguyên** |
| `LWPOLYLINE` | tách thành Segment; **đọc bulge để khôi phục arc** |
| `POLYLINE` (2D) | như trên |
| `POLYLINE` (3D) | chiếu xuống Z=0, cảnh báo |
| `SPLINE` | flatten theo dung sai (xem 6.2) |
| `ELLIPSE` | flatten theo dung sai |
| `INSERT` (block) | explode đệ quy, áp dụng ma trận biến đổi |
| `HATCH` | mặc định bỏ; tùy chọn lấy đường biên |
| `TEXT` / `MTEXT` | **bỏ + cảnh báo** (designer phải convert to curves) |
| `DIMENSION`, `LEADER` | bỏ, không cảnh báo |
| `POINT` | bỏ |
| `3DFACE`, `SOLID`, `MESH` | bỏ + cảnh báo |

**Bulge trong LWPOLYLINE là chi tiết quan trọng.** Đây là cách DXF mã hóa cung tròn trong polyline. Bỏ qua bulge sẽ biến mọi góc bo và lỗ tròn thành cạnh thẳng. Phải đọc và chuyển thành Segment(arc). Công thức: bulge = tan(góc chắn cung / 4).

**Chuẩn hóa đơn vị:** đọc biến header `$INSUNITS`. Nếu là inch (giá trị 1), nhân toàn bộ tọa độ với 25.4. Nếu là 0 (không xác định — trường hợp phổ biến của Corel), dùng giá trị `assumed_input_unit` trong config và ghi cảnh báo rõ ràng vào báo cáo. Sai tỉ lệ 25.4 lần là lỗi tốn kém nhất trong toàn bộ quy trình, phải bắt được ngay đầu vào.

### 6.2. `flatten.py` — Rời rạc hóa spline và ellipse

Flatten theo **sai số dây cung (chord tolerance)**, không theo số đoạn cố định. Sai số mặc định **0.02mm** (config được).

Nghĩa là: khoảng cách lớn nhất từ đường cong thật đến đoạn thẳng xấp xỉ không vượt quá 0.02mm. Đường cong bán kính lớn sẽ có ít đoạn, bán kính nhỏ có nhiều đoạn — tự động cân bằng giữa độ chính xác và số node.

**Tối ưu bổ sung (nên có):** trước khi flatten, thử nhận diện xem spline có thực chất là cung tròn không. Nhiều spline từ Corel là cung tròn bị chuyển đổi. Kiểm tra bằng cách lấy 3 điểm trên spline, dựng đường tròn qua 3 điểm đó, rồi kiểm tra toàn bộ các điểm còn lại có nằm trên đường tròn trong dung sai không. Nếu có → chuyển thành Segment(arc) thay vì flatten. Việc này giảm số node rất mạnh và cải thiện chất lượng cắt rõ rệt.

### 6.3. `snap.py` — Snap endpoint và nối đoạn hở

Đây là tầng sửa lỗi contour hở, nguyên nhân phổ biến nhất khiến CypCut từ chối file.

Thuật toán:
1. Thu thập toàn bộ endpoint của mọi Segment
2. Dựng chỉ mục không gian (`shapely.STRtree` hoặc `scipy.spatial.cKDTree`)
3. Gom cụm các endpoint nằm trong bán kính `snap_tolerance` (mặc định **0.05mm**)
4. Với mỗi cụm, hợp nhất về một tọa độ chung — dùng **trung bình cộng**, không dùng điểm đầu tiên, để tránh trôi tích lũy
5. Nối các Segment chia sẻ endpoint thành chuỗi liên tục
6. Chuỗi có endpoint đầu trùng endpoint cuối → contour khép kín

**Xử lý khe hở lớn hơn dung sai:** không tự nối. Ghi vào báo cáo kèm tọa độ chính xác và độ lớn khe hở, đánh dấu file cần xem lại. Việc tự động nối khe hở lớn có thể tạo ra hình dạng sai hoàn toàn — chi phí sửa sai lớn hơn nhiều so với chi phí kiểm tra tay.

**Ngưỡng dung sai:** 0.05mm đủ để bắt sai số làm tròn của Corel (thường ở mức 1e-3 đến 1e-2 mm) mà không đủ để vô tình nối hai chi tiết đặt sát nhau. Nếu xưởng có chi tiết đặt cách nhau dưới 0.1mm thì phải hạ ngưỡng xuống.

### 6.4. `dedupe.py` — Xóa đường trùng và chồng nhau

Ba trường hợp cần phân biệt:

**a. Trùng hoàn toàn** — hai Segment cùng endpoint (trong dung sai), cùng loại. Giữ một, xóa còn lại. Xử lý bằng hash tọa độ đã làm tròn.

**b. Chồng một phần** — hai đoạn thẳng cùng phương, đè lên nhau một phần. Chiếu lên vector chỉ phương, hợp nhất khoảng giá trị tham số, tạo một đoạn duy nhất.

**c. Biên chung giữa hai chi tiết liền kề** — hai contour riêng biệt chia sẻ một cạnh. Đây **không phải lỗi** khi làm common-line cutting, nhưng là lỗi khi hai chi tiết đáng lẽ phải rời nhau. Mặc định: **giữ nguyên và báo cáo**, không tự động xóa. Cung cấp cờ `--merge-common-edges` cho ai muốn xử lý tự động.

### 6.5. `despeckle.py` — Xóa đối tượng siêu nhỏ

Xóa contour có:
- Chu vi < `min_perimeter` (mặc định **0.5mm**)
- Diện tích < `min_area` (mặc định **0.1mm²**)
- Đường kính khung bao nhỏ hơn đường kính tia laser

Luôn ghi vào báo cáo số lượng và tọa độ những gì bị xóa. Không xóa im lặng — có thể đó là lỗ định vị thật sự nhỏ.

### 6.6. `weld.py` — Hợp nhất hình chồng nhau

Tầng trung tâm, giải quyết đúng bài toán "gộp thành một khối thống nhất".

```python
polygons = [Polygon(c.to_shapely(arc_tol)) for c in closed_contours]
polygons = [p if p.is_valid else make_valid(p) for p in polygons]
welded = unary_union(polygons)
```

`unary_union` hợp nhất mọi polygon giao nhau thành biên ngoài liền mạch, tự động xử lý lỗ. Kết quả là `Polygon` hoặc `MultiPolygon`.

Ba điểm phải xử lý cẩn thận:

**Polygon không hợp lệ.** Hình tự cắt (self-intersecting) từ Corel rất phổ biến. Phải gọi `shapely.make_valid()` trước khi union, nếu không `unary_union` sẽ ném exception hoặc trả kết quả sai. Ghi cảnh báo khi phải sửa.

**Chỉ weld khi cần.** Nếu các polygon rời nhau hoàn toàn thì union không thay đổi gì, nhưng vẫn làm mất thông tin arc. Phải kiểm tra trước: dùng `STRtree` tìm các cặp polygon thực sự giao nhau, chỉ weld những cụm đó, giữ nguyên Contour gốc cho những polygon độc lập.

**Chế độ weld.** Ba tùy chọn trong config:
- `off` — không weld
- `overlapping` (mặc định) — chỉ weld các hình thực sự chồng nhau
- `all` — weld toàn bộ thành một khối, kể cả các cụm rời nhau (tương đương Weld toàn bộ trong Corel)

### 6.7. `hierarchy.py` — Xác định biên ngoài và lỗ trong

Với mỗi contour khép kín, xác định nó là biên ngoài của chi tiết hay là lỗ:

1. Dựng cây chứa nhau bằng `STRtree` + phép `contains`
2. Độ sâu chẵn (0, 2, 4...) → biên ngoài
3. Độ sâu lẻ (1, 3, 5...) → lỗ
4. Đặt chiều: biên ngoài CCW, lỗ CW (quy ước tiêu chuẩn, CypCut hiểu đúng)

Xử lý được cấu trúc lồng nhiều lớp: chi tiết → lỗ → chi tiết nhỏ nằm trong lỗ (dạng "đảo").

### 6.8. `simplify.py` — Giảm node

Chỉ áp dụng cho contour đã bị biến đổi (weld, flatten), **không** áp dụng cho contour giữ nguyên từ file gốc.

- Douglas–Peucker với dung sai **0.01mm** (config được), dùng `simplify(preserve_topology=True)`
- Gộp các đoạn thẳng liên tiếp có góc lệch < 0.1° thành một đoạn
- Sau khi simplify, **kiểm tra lại** diện tích không lệch quá 0.1% so với trước. Nếu lệch quá → hoàn tác, giữ bản chưa simplify.

Bước kiểm tra này quan trọng: simplify quá tay làm sai kích thước chi tiết mà không ai phát hiện ra cho đến khi cắt xong.

### 6.9. `validate.py` — Kiểm tra cuối

Chạy sau toàn bộ pipeline, không sửa gì, chỉ phân loại kết quả.

**Lỗi nghiêm trọng (file KHÔNG dùng được):**
- Còn contour hở sau khi đã snap
- Contour tự cắt không sửa được
- File rỗng sau xử lý
- Kích thước tổng thể vượt khổ tấm cấu hình sẵn

**Cảnh báo (dùng được nhưng cần xem lại):**
- Có entity bị bỏ (text, hatch, 3D)
- Đơn vị đầu vào không xác định
- Khe hở lớn hơn dung sai không nối được
- Chi tiết trùng lặp hoàn toàn (cùng hình dạng, cùng vị trí)
- Đường kính lỗ nhỏ hơn độ dày tôn (thực tế không cắt được sạch)
- Khoảng cách giữa hai contour nhỏ hơn 2 lần độ rộng vệt cắt

**Thông tin:**
- Số contour trước/sau
- Số node trước/sau
- Số đường trùng đã xóa
- Số contour đã khép
- Số cụm đã weld
- Tổng chiều dài đường cắt (để ước tính thời gian)
- Diện tích chi tiết (để tính vật liệu)

### 6.10. `writer.py` — Ghi DXF

- Phiên bản đích: **AC1015 (R2000)** — tương thích rộng nhất với CypCut. Cấu hình được.
- Một layer duy nhất, tên đặt trong config (mặc định `CUT`), màu 7
- Ghi `LWPOLYLINE` cho contour hỗn hợp, **dùng bulge để mã hóa arc**
- Ghi `CIRCLE` nguyên bản cho đường tròn, không chuyển thành polyline
- Đặt `$INSUNITS = 4` (mm)
- Mọi tọa độ Z = 0
- Đặt `$EXTMIN` / `$EXTMAX` đúng
- Không có block, không có entity nào ngoài hình học cắt

**Ghi bulge là bắt buộc.** Nếu ghi arc thành chuỗi đoạn thẳng, mọi công sức bảo toàn arc ở các tầng trên trở nên vô nghĩa.

## 7. Giao diện dòng lệnh

```bash
# Một file
dxfclean input.dxf -o output.dxf

# Cả thư mục
dxfclean ./input/ -o ./output/ --report report.html

# Chỉ kiểm tra, không ghi file
dxfclean input.dxf --check

# Theo dõi thư mục, xử lý tự động file mới
dxfclean watch ./drop/ -o ./clean/ --failed ./review/

# Ghi đè tham số
dxfclean input.dxf --snap-tol 0.1 --weld-mode all --no-simplify
```

Mã thoát: `0` thành công, `1` có cảnh báo, `2` có lỗi nghiêm trọng, `3` lỗi hệ thống.

Chế độ watch:
- File sạch → thư mục output
- File có lỗi nghiêm trọng → thư mục `review/` kèm file `.report.txt` cùng tên
- File gốc luôn được giữ nguyên, không bao giờ ghi đè
- Chống xử lý file đang copy dở: đợi kích thước file ổn định 2 giây trước khi xử lý

## 8. Cấu hình

`config.yaml`:

```yaml
input:
  assumed_unit: mm          # mm | inch — dùng khi $INSUNITS không xác định
  include_hatch_boundary: false

flatten:
  chord_tolerance: 0.02     # mm
  detect_circular_splines: true

snap:
  tolerance: 0.05           # mm
  max_reportable_gap: 2.0   # khe hở lớn hơn mức này không báo (coi là cố ý)

dedupe:
  enabled: true
  merge_common_edges: false

despeckle:
  min_perimeter: 0.5
  min_area: 0.1

weld:
  mode: overlapping         # off | overlapping | all

simplify:
  enabled: true
  tolerance: 0.01
  max_area_deviation: 0.001 # 0.1%

validate:
  sheet_width: 1500
  sheet_height: 3000
  material_thickness: 2.0
  kerf_width: 0.15
  min_hole_diameter_ratio: 1.0   # lỗ nhỏ hơn (tỉ lệ × độ dày) sẽ bị cảnh báo

output:
  dxf_version: AC1015
  layer_name: CUT
  preserve_arcs: true
```

## 9. Báo cáo

**Terminal** (dùng `rich`): bảng tóm tắt mỗi file, tô màu theo mức độ.

**HTML** (`--report`): bảng tổng hợp toàn bộ lô + trang chi tiết từng file. Với mỗi lỗi, hiển thị tọa độ và ảnh SVG preview có đánh dấu vị trí lỗi bằng vòng tròn đỏ. Người vận hành nhìn là biết ngay chỗ nào cần sửa, không phải mở CAD dò tìm.

**JSON** (`--json`): để nối vào hệ thống khác sau này.

## 10. Kiểm thử

**Unit test** cho từng tầng, dùng hình học dựng sẵn trong code.

**Integration test** với bộ file DXF mẫu, mỗi file cô lập một loại lỗi:
- `open_contour.dxf` — contour hở 0.02mm
- `duplicate_lines.dxf` — hai đường trùng hoàn toàn
- `overlapping_shapes.dxf` — hai hình chồng nhau cần weld
- `nested_holes.dxf` — chi tiết có lỗ, trong lỗ có chi tiết nhỏ
- `spline_heavy.dxf` — nhiều spline
- `text_present.dxf` — có text chưa convert
- `inch_units.dxf` — đơn vị inch
- `self_intersecting.dxf` — polygon tự cắt
- `blocks_nested.dxf` — block lồng nhiều lớp
- `real_corel_export.dxf` — file thật xuất từ Corel

**Test hồi quy về kích thước:** với mỗi file mẫu, ghi lại diện tích và khung bao trước/sau. Bất kỳ thay đổi nào vượt 0.1% mà không phải do weld đều là lỗi. Đây là lưới an toàn quan trọng nhất — sai kích thước là loại lỗi tốn kém nhất và khó phát hiện nhất.

**Test hiệu năng:** file 5.000 contour phải xử lý xong dưới 30 giây.

## 11. Tiêu chí nghiệm thu

1. File DXF xuất ra import vào CypCut không hiện cảnh báo nào
2. CypCut nhận diện đúng biên ngoài và lỗ mà không cần chạy "Auto Seek Contour"
3. Không có đường cắt trùng lặp
4. Kích thước chi tiết sai lệch dưới 0.02mm so với thiết kế gốc
5. Số node của contour có arc không tăng quá 20% so với file gốc
6. Xử lý 100 file trong một lần chạy không lỗi
7. Thời gian xử lý trung bình dưới 5 giây/file
8. Người vận hành không cần mở CAD để sửa với ít nhất 90% file đầu vào thực tế

## 12. Lộ trình

**Giai đoạn 1 — Nhân lõi**
`model`, `reader`, `writer`, `explode`, `flatten`. Mục tiêu: đọc DXF ghi lại DXF, kích thước không đổi, arc được bảo toàn. Chưa sửa lỗi gì. Có bộ test hồi quy kích thước.

**Giai đoạn 2 — Sửa lỗi**
`snap`, `dedupe`, `despeckle`, `hierarchy`, `validate`. Đây là phần giải quyết phần lớn công việc thủ công hiện tại.

**Giai đoạn 3 — Weld và tối ưu**
`weld`, `simplify`. Chạy thử trên file thật của xưởng, so sánh với kết quả làm tay.

**Giai đoạn 4 — Vận hành**
CLI đầy đủ, watch folder, báo cáo HTML, đóng gói thành file `.exe` bằng PyInstaller để cài trên máy Windows của xưởng không cần Python.

**Giai đoạn 5 (tùy chọn) — GUI**
Giao diện kéo thả, xem trước hình học, đánh dấu lỗi trực quan. Chỉ làm sau khi CLI đã chạy ổn định trong sản xuất.

## 13. Nguyên tắc thiết kế

**Không bao giờ làm sai kích thước trong im lặng.** Mọi phép biến đổi làm thay đổi hình học phải được ghi lại và kiểm chứng. Thà trả file về cho người sửa còn hơn xuất ra một file trông sạch nhưng sai 0.5mm.

**Báo cáo quan trọng ngang kết quả.** Công cụ không sửa được 100% file. Giá trị nằm ở chỗ nó chỉ chính xác cái gì sai và ở đâu, thay vì bắt người ta dò tìm.

**Không ghi đè file gốc.** Luôn ghi ra file mới. Giữ file gốc để đối chiếu.

**Mọi ngưỡng dung sai đều nằm trong config, không hard-code.** Xưởng khác nhau, vật liệu khác nhau sẽ cần ngưỡng khác nhau.

**Ưu tiên đúng hơn nhanh.** 5 giây/file là quá đủ so với 15 phút làm tay.
