# tim_data — AI agent tìm forwarder nước ngoài

Agent dùng Claude + web search để **tự động tìm forwarder/NVOCC nước ngoài** có khả năng có hàng xuất/nhập với
Việt Nam, **chấm điểm** mức phù hợp và **soạn email chào dịch vụ làm đại lý tại Việt Nam** để sales duyệt và gửi.

Thiết kế chi tiết: [docs/DESIGN.md](docs/DESIGN.md)

```
discover (web search agent) → qualify (chấm điểm A/B/C/D) → draft (email + follow-up) → export CSV / Markdown
```

## Cài đặt

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

Yêu cầu Python ≥ 3.11.

## Cấu hình

1. Sửa `config/company_profile.toml`: tên công ty, người gửi, dịch vụ, thế mạnh, network. Email chỉ dùng những gì có ở đây.
2. Xem/sửa `config/campaigns.toml`: mỗi campaign là một thị trường (Mỹ, Đức, Hà Lan, Nhật, Hàn, Úc, Anh, Ấn Độ…).

## Sử dụng

```bash
# Chạy trọn quy trình cho 1 thị trường
python -m forwarder_agent -v run --campaign germany_sea_air

# Hoặc từng bước
python -m forwarder_agent -v discover --campaign us_transpacific
python -m forwarder_agent -v discover --country "Poland" --focus "forwarders importing furniture from Asia" --max 10
python -m forwarder_agent -v qualify
python -m forwarder_agent -v draft --min-score 60
python -m forwarder_agent export          # → output/leads.csv, output/drafts.md

# Nhập danh sách có sẵn (hội chợ, network...) — cột bắt buộc: company_name, website, country
python -m forwarder_agent import-csv danh_sach.csv --campaign hoi_cho_2026

# Sau khi gửi / nhận phản hồi
python -m forwarder_agent set-status alpha-logistics.de contacted
python -m forwarder_agent set-status alpha-logistics.de do_not_contact
python -m forwarder_agent stats
```

### Google AI + Claude: kiểm tra agent có tuyến nhập/xuất Việt Nam

```bash
pip install google-genai
export GEMINI_API_KEY=...            # Google AI Studio
python -m forwarder_agent -v discover --campaign china --provider both   # Claude + Gemini cùng tìm agent
python -m forwarder_agent -v check-lanes --country China                 # kiểm tra tuyến VN
python -m forwarder_agent -v qualify --requalify                         # chấm lại theo kết quả tuyến
python -m forwarder_agent push-sheets --country China
```

- **Gemini (Google Search grounding)** và **Claude (web search)** nghiên cứu độc lập cùng một câu hỏi:
  agent có chuyển hàng **vào** Việt Nam, **ra khỏi** Việt Nam, cả hai, hay không? Có tìm bằng tiếng địa phương
  (VD tiếng Trung: 越南专线, 越南进口).
- **Claude đối chiếu** hai kết quả và chỉ công nhận thông tin có URL nguồn. Kết quả gồm chiều tuyến, phương thức,
  cảng/cửa khẩu VN, có văn phòng tại VN không, độ tin cậy (cao/trung bình/thấp), hai nguồn có khớp nhau không,
  và tóm tắt tiếng Việt.
- Không có `GEMINI_API_KEY` thì chỉ chạy Claude (`--providers claude`).
- Trên Sheet có thêm 7 cột "Tuyến VN: …". Tab tạo bằng phiên bản cũ phải chèn 7 cột sau cột
  "Bằng chứng tuyến Việt Nam" trước khi đẩy; agent sẽ dừng và báo lỗi thay vì ghi lệch cột.

### Đẩy lên Google Sheets (mỗi quốc gia một tab)

```bash
pip install gspread
export GOOGLE_APPLICATION_CREDENTIALS=/duong/dan/service-account.json
export GOOGLE_SHEETS_ID=<id của spreadsheet>
python -m forwarder_agent push-sheets                 # tất cả quốc gia
python -m forwarder_agent push-sheets --country China # chỉ tab China
python -m forwarder_agent run --campaign china --sheet-id <id>   # chạy xong tự đẩy lên
```

1. Tạo service account trên Google Cloud, bật **Google Sheets API**, tải file JSON key.
2. Chia sẻ spreadsheet cho email của service account (quyền Editor).
3. Mỗi quốc gia một tab (China, Germany, United States…). Agent cập nhật theo `Domain`:
   công ty đã có thì sửa tại chỗ, công ty mới thì thêm dòng. **Các cột bên phải cột "Cập nhật"
   (ví dụ "Ghi chú sales") không bao giờ bị ghi đè.**

### Yêu cầu với lead

- **Bắt buộc có website riêng của công ty.** Trang trên made-in-china, alibaba, JCtrans, WCA directory,
  LinkedIn, trang vàng… chỉ dùng để phát hiện; agent phải tìm website chính thức, không có thì bỏ qua.
- **Hiệp hội / network không bắt buộc**: có thì được cộng điểm, không có vẫn giữ (cột "Thành viên hiệp hội?").
- Công ty đã có văn phòng/công ty con tại Việt Nam vẫn được ghi nhận nhưng bị trừ điểm và gắn red flag.

### Chạy thử thị trường Trung Quốc

`samples/china_trial_2026-11.csv` là kết quả chạy thử đầu tiên (25 công ty có website riêng):

```bash
python -m forwarder_agent import-csv samples/china_trial_2026-11.csv --campaign china
python -m forwarder_agent -v qualify && python -m forwarder_agent push-sheets --country China
```

Kết quả:
- `output/leads.csv` — mở bằng Excel: thông tin công ty, điểm, tier, email/điện thoại công khai, nguồn, email nháp.
- `output/drafts.md` — toàn bộ email nháp + follow-up + ghi chú tiếng Việt, để duyệt trước khi gửi.
- `data/leads.db` — SQLite, tự loại trùng theo domain giữa các lần chạy.

## Nguyên tắc

- **Không tự gửi email.** Sales duyệt và gửi thủ công.
- Chỉ dùng **dữ liệu công khai**, có `source_urls` cho từng lead; agent không được đoán email.
- Email luôn có dòng opt-out; ai từ chối → `do_not_contact`. Xem mục tuân thủ GDPR/CAN-SPAM trong `docs/DESIGN.md`.

## Model & cấu hình API

- Mặc định `claude-opus-5-5` (đổi bằng biến môi trường `FORWARDER_AGENT_MODEL`).
- Bật sẵn **server-side refusal fallback** (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`):
  nếu một request bị bộ lọc an toàn từ chối, API tự chạy lại trên model dự phòng. Muốn tắt, bỏ hai dòng
  `betas`/`fallbacks` trong `forwarder_agent/llm.py`.

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Test chạy offline với client giả lập (không tốn API).
