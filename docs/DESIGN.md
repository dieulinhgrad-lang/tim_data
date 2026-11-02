# Thiết kế: AI agent thu thập data forwarder nước ngoài

## 1. Mục tiêu

Tự động hóa phần tốn thời gian nhất của sales quốc tế (overseas BD) tại một forwarder Việt Nam:

1. **Tìm** các công ty forwarder / NVOCC / customs broker ở nước ngoài có khả năng có hàng xuất/nhập với Việt Nam.
2. **Xác minh và làm giàu dữ liệu**: dịch vụ, tuyến, network (WCA, JCtrans, FIATA…), email/điện thoại công khai, bằng chứng có hàng Việt Nam.
3. **Chấm điểm** mức phù hợp để làm đại lý (agent) tại Việt Nam cho họ.
4. **Soạn email chào dịch vụ** cá nhân hóa + 2 follow-up, để sales **duyệt rồi gửi thủ công**.
5. **Theo dõi trạng thái** từng lead (new → qualified → drafted → contacted → replied / rejected / do_not_contact).

Ngoài phạm vi (cố ý): tự động gửi email hàng loạt, đăng nhập vào trang thành viên (WCA, JCtrans…), scrape LinkedIn.

## 2. Kiến trúc tổng quan

```
           config/campaigns.toml            config/company_profile.toml
                    │                                   │
                    ▼                                   ▼
 ┌──────────────────────────────┐   ┌───────────────────────────────┐
 │ 1. DISCOVERY AGENT           │   │  Claude (claude-opus-5-5)     │
 │  vòng lặp tool-use:          │◄─►│  server tools: web_search,    │
 │  web_search → web_fetch →    │   │  web_fetch (Anthropic chạy)   │
 │  save_forwarder (client tool)│   │  client tool: save_forwarder  │
 └──────────────┬───────────────┘   └───────────────────────────────┘
                │ validate (pydantic) + lọc VN + dedupe theo domain
                ▼
        ┌───────────────┐      import-csv (danh sách hội chợ, network bạn có quyền dùng)
        │ SQLite leads  │◄─────────────────────────────────────────
        └──────┬────────┘
               ▼
 ┌──────────────────────────────┐
 │ 2. QUALIFY (structured out)  │  rubric 100 điểm → tier A/B/C/D, góc tiếp cận, red flags
 └──────────────┬───────────────┘
               ▼  (score ≥ min_score)
 ┌──────────────────────────────┐
 │ 3. OUTREACH DRAFT            │  subject + email ≤160 từ + 2 follow-up + ghi chú tiếng Việt
 └──────────────┬───────────────┘
               ▼
   output/leads.csv  +  output/drafts.md   ──►  Sales duyệt, gửi, rồi `set-status ... contacted`
```

### Vì sao chia thành pipeline 3 bước thay vì 1 agent "làm hết"?

| Bước | Kiểu | Lý do |
|---|---|---|
| Discovery | **Agent** (vòng lặp tool-use, model tự quyết tìm gì tiếp) | Việc tìm kiếm mở, nhiều bước, không biết trước nguồn nào có kết quả |
| Qualify | **1 lần gọi**, structured output (Pydantic) | Đầu vào rõ ràng, cần kết quả ổn định, dễ kiểm tra, rẻ |
| Outreach | **1 lần gọi**, structured output | Tương tự; tách riêng để chạy lại khi đổi giọng văn mà không phải tìm lại |

Tách bước giúp: chạy lại từng phần, kiểm soát chi phí, và con người can thiệp được giữa các bước.

## 3. Các thành phần

| File | Vai trò |
|---|---|
| `forwarder_agent/discovery.py` | Agent loop. Tools: `web_search_20260209`, `web_fetch_20260209` (server-side) + `save_forwarder` (client, `strict: true`). Xử lý `pause_turn`, `refusal`, giới hạn số lượt, dừng khi đủ `target_count`. |
| `forwarder_agent/qualify.py` | `messages.parse(output_format=Qualification)` với rubric chấm điểm. |
| `forwarder_agent/outreach.py` | `messages.parse(output_format=EmailDraft)`; luật viết email (không bịa số liệu, có dòng opt-out). |
| `forwarder_agent/sheets.py` | Đẩy lên Google Sheets: 1 tab/quốc gia, upsert theo domain, không ghi đè cột ghi chú của sales; ghi RAW để số `+86...` không bị hiểu thành công thức. |
| `forwarder_agent/store.py` | SQLite, khóa chính = domain chuẩn hóa; merge dữ liệu khi gặp lại công ty; export CSV (UTF-8 BOM cho Excel) và Markdown. |
| `forwarder_agent/models.py` | Pydantic: `Lead`, `ContactPerson`, `Qualification`, `EmailDraft`, `LeadStatus`. |
| `forwarder_agent/llm.py` | Model, effort, adaptive thinking, server-side refusal fallback (`fallbacks: "default"`). |
| `config/company_profile.toml` | Hồ sơ công ty bạn — nguồn sự thật duy nhất cho dịch vụ/thế mạnh trong email. |
| `config/campaigns.toml` | Thị trường mục tiêu (Mỹ, Đức, Hà Lan, Nhật, Hàn, Úc, Anh, Ấn Độ…). |

## 4. Dữ liệu thu thập (schema `Lead`)

`company_name, website, country, city, company_type, services, transport_modes, trade_lanes,
vietnam_evidence, networks, general_emails, phones, linkedin_url, contacts[name,title,email,source_url],
source_urls, notes` + `campaign, status, score, tier`.

Mỗi lead **bắt buộc có `source_urls`** để sales kiểm chứng.

## 5. Rubric chấm điểm (0–100)

| Tiêu chí | Điểm |
|---|---|
| Có bằng chứng tuyến Việt Nam / ĐNÁ / châu Á, hoặc gom hàng LCL từ châu Á | 30 |
| Là forwarder/NVOCC có năng lực sea/air khớp dịch vụ của mình | 20 |
| Thành viên network/hiệp hội (WCA, JCtrans, GLA, PPL, X2, FIATA, hiệp hội quốc gia) | 15 |
| Phục vụ ngành hàng có nguồn cung VN (nội thất, may mặc, giày dép, điện tử, thủy sản, cà phê, nông sản) | 15 |
| Có email doanh nghiệp công khai / người liên hệ sales-agency | 10 |
| Quy mô ổn định (nhiều văn phòng, tự consol, lâu năm) | 10 |

Tier: **A ≥ 75**, **B 55–74**, **C 35–54**, **D < 35** (D hoặc không phải forwarder → `rejected`).
Mặc định chỉ soạn email cho lead ≥ 55 điểm (A + B).

## 6. Kiểm soát chất lượng & chống "bịa"

- Prompt yêu cầu mọi dữ kiện phải đến từ trang đã xem, ghi `source_urls`; cấm đoán/ghép email theo pattern.
- `save_forwarder` dùng `strict: true` → input luôn đúng schema; thêm validate Pydantic phía client.
- Lọc cứng: domain `.vn` hoặc country = Vietnam → bỏ qua; dedupe theo domain trong lần chạy và trong DB
  (danh sách domain đã biết được đưa vào prompt để agent tránh trùng).
- Email nháp chỉ được dùng dịch vụ/thế mạnh có trong `company_profile.toml`.
- Không gửi tự động: con người duyệt `output/drafts.md`.

## 6b. Ghi chú riêng thị trường Trung Quốc

- Nhiều forwarder TQ chạy "越南专线" (tuyến chuyên Việt Nam) đường bộ qua Bằng Tường/Hữu Nghị, Đông Hưng và đường
  biển tới Hải Phòng/Cát Lái — họ cần đối tác Việt Nam cho thông quan đầu nhập, giao hàng nội địa và hàng xuất ngược về TQ.
- Một số đã có công ty con ở Việt Nam → vẫn liệt kê nhưng trừ 15 điểm và gắn red flag; góc tiếp cận chuyển sang
  vùng/phương thức họ chưa phủ (VD: họ mạnh miền Bắc đường bộ → mình chào miền Nam/đường biển).
- Agent tìm cả tiếng Anh và tiếng Trung (越南专线, 中越物流, 越南海运 货代).
- Email công ty TQ thường là @163.com/@qq.com hoặc chỉ có WeChat/WhatsApp — cân nhắc liên hệ qua WeChat/WhatsApp.

## 7. Tuân thủ pháp lý & đạo đức (quan trọng khi chào hàng ra nước ngoài)

- **Chỉ dữ liệu công khai, B2B**: email chung của công ty (info@, sales@) hoặc người được công bố là đầu mối
  kinh doanh. Không thu thập dữ liệu cá nhân nhạy cảm.
- **EU/UK (GDPR, PECR)**: cold email B2B dựa trên "lợi ích hợp pháp" phải liên quan đến công việc của người nhận
  và **có cách từ chối dễ dàng** → mỗi email có dòng opt-out; khi ai đó từ chối, chạy
  `set-status <domain> do_not_contact`. Ở một số nước (VD: Đức) quy định cold email chặt hơn — nên ưu tiên
  email chung của công ty và cân nhắc kênh khác (LinkedIn thủ công, hội chợ, network meeting).
- **Mỹ (CAN-SPAM)**: tiêu đề trung thực, có địa chỉ công ty trong chữ ký, xử lý opt-out.
- **Canada (CASL)**, **Úc (Spam Act)**: chỉ gửi tới địa chỉ được công bố công khai cho mục đích kinh doanh, nội dung
  liên quan đến vai trò của người nhận.
- Tôn trọng điều khoản sử dụng của directory: không đăng nhập, không vượt paywall, không scrape khu vực thành viên.
- Gửi số lượng nhỏ, cá nhân hóa (khuyến nghị ≤ 30–50 email/ngày/hộp thư) để bảo vệ uy tín domain.

## 8. Chi phí & vận hành

- Discovery dùng effort `high` (nghiên cứu nhiều bước); qualify/draft dùng `medium`.
- Mỗi lượt discovery giới hạn `max_uses` của web_search (25) / web_fetch (20) và `max_turns` (40).
- Chạy định kỳ: cron/Task Scheduler `python -m forwarder_agent run --all-campaigns` hàng tuần; DB tự dedupe.
- Muốn tiết kiệm hơn: đặt `FORWARDER_AGENT_MODEL` sang model rẻ hơn cho qualify/draft sau khi so sánh chất lượng.

## 9. Lộ trình mở rộng

1. ~~Đẩy lên Google Sheets~~ (đã làm: `forwarder_agent/sheets.py`, mỗi quốc gia một tab). Tiếp theo: CRM (HubSpot, Salesforce).
2. **Gmail draft**: tạo bản nháp trong Gmail (không gửi) để sales bấm gửi.
3. **Theo dõi phản hồi**: đọc inbox, tự đổi trạng thái `replied`, gợi ý trả lời + chuyển sang quy trình báo giá
   (kết nối quy trình theo dõi inquiry hiện có).
4. **Làm giàu thêm**: đọc trang "Our network/agents" của lead để xem họ đã có agent VN chưa (red flag hoặc cơ hội thay thế).
5. **Đo lường**: tỷ lệ phản hồi theo campaign/tier/góc tiếp cận → tinh chỉnh rubric và prompt.
