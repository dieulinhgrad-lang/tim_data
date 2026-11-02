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
