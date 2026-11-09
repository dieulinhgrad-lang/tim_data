---
name: daily-agent-hunt
description: Mỗi ngày tìm 50 agent/forwarder nước ngoài MỚI (có website riêng, hiệp hội không bắt buộc), kiểm tra agent có tuyến nhập/xuất Việt Nam không, chấm điểm, lưu leads/<ngày>.csv và đẩy lên Google Sheet (mỗi quốc gia một tab). Dùng khi người dùng nói "tìm agent hằng ngày", "chạy daily", "tìm 50 agent", "/daily-agent-hunt", hoặc khi tác vụ định kỳ hằng ngày kích hoạt.
---

# Tìm 50 agent nước ngoài mỗi ngày

Mục tiêu: thêm **50 agent MỚI mỗi ngày** (số này đặt ở `total_per_day` trong `config/daily.toml`), không trùng với bất kỳ agent nào đã có trên Google Sheet.
"Ngày" tính theo **giờ Việt Nam** (Asia/Ho_Chi_Minh).

Cấu hình nằm trong repo:
- `config/daily.toml`: `spreadsheet_id`, `total_per_day`, hạn mức theo thị trường `[quota]` (tổng = `total_per_day`, hiện là 50).
- `config/campaigns.toml`: quốc gia, trọng tâm và thành phố cho từng thị trường.
- `config/company_profile.toml`: hồ sơ công ty mình (dùng khi chấm điểm và soạn email).

## Tiêu chí bắt buộc cho mỗi agent

1. **Có website riêng của công ty** (tên miền riêng). Sàn thương mại, danh bạ, mạng xã hội (made-in-china,
   alibaba, JCtrans, WCA directory, LinkedIn, trang vàng...) chỉ dùng để phát hiện; phải tìm ra website chính thức,
   không có thì bỏ qua.
2. **Hiệp hội / network không bắt buộc.** Có (WCA, JCtrans, FIATA, CIFA, hiệp hội quốc gia...) thì ghi lại; không có vẫn nhận.
3. Là forwarder, NVOCC, customs broker hoặc 3PL **đặt tại nước mục tiêu**. Loại công ty có trụ sở ở Việt Nam,
   hãng tàu, hãng bay, chủ hàng, nhà cung cấp phần mềm.
4. **Kiểm tra tuyến Việt Nam**: chiều nhập vào VN, xuất từ VN, cả hai, không có, hay chưa rõ. Ghi kèm phương thức,
   cảng/cửa khẩu, việc họ đã có văn phòng tại VN chưa, độ tin cậy và URL bằng chứng.
5. Chỉ dùng dữ liệu công khai, mọi thông tin phải có URL nguồn. **Không đoán email hoặc tên người.**
   Không đăng nhập, không vượt paywall.
6. **Không độn số.** Nếu không tìm đủ số agent mục tiêu đã xác minh, ghi rõ số còn thiếu trong báo cáo.

## Bước 0: Chuẩn bị

```bash
pip install -q -r requirements.txt
```

Chọn chế độ:
- **Chế độ A (API)**: có `ANTHROPIC_API_KEY`, và có thêm `GEMINI_API_KEY` thì càng tốt.
- **Chế độ B (Claude Code tự làm)**: không có API key. Claude Code dùng công cụ tìm kiếm của mình
  (Exa `web_search_exa` / `web_fetch_exa`, hoặc WebSearch) và connector Google Sheets.

## Chế độ A: chạy lệnh có sẵn

Nếu có service account Google (`GOOGLE_APPLICATION_CREDENTIALS`):

```bash
python -m forwarder_agent -v daily --provider both
```

Dùng `--provider claude` nếu không có `GEMINI_API_KEY`. Lệnh này tự làm hết: đọc các domain đã có trên Sheet để
loại trùng, chia hạn mức theo thị trường (vòng 2 bù phần thiếu cho thị trường còn nguồn), tìm agent, kiểm tra tuyến
VN (Gemini + Claude), chấm điểm, soạn email nháp, ghi `leads/<ngày>.csv` và `leads/<ngày>-report.md`, rồi đẩy lên Sheet.

Nếu **không có** service account nhưng có connector Google Sheets: chạy với `--no-sheets`, rồi làm Bước B5 để ghi
lên Sheet qua connector. Sau đó làm Bước B6 (commit) và B7 (báo cáo).

## Chế độ B: Claude Code tự nghiên cứu

### B1. Đọc bộ nhớ dài hạn (để không trùng)

- Dùng connector Google Sheets đọc `spreadsheet_id` trong `config/daily.toml`: lấy danh sách tab, rồi đọc cột A
  (Domain) của từng tab.
- Đọc thêm cột `Domain` trong tất cả file `leads/*.csv` của repo.
- Gộp lại thành tập **KNOWN** (tên miền viết thường, bỏ `www.`).

### B2. Tìm agent theo hạn mức

Với mỗi thị trường trong `[quota]` (đi theo thứ tự, mỗi thị trường tìm đến khi đủ hạn mức):

1. Tìm 3–6 lượt, cả tiếng Anh **và** tiếng địa phương. Ví dụ:
   - Trung Quốc: `越南专线 货代 官网`, `中越物流 海运 陆运`, `越南进口 物流公司`
   - Nhật: `ベトナム 輸入 フォワーダー`
   - Hàn: `베트남 포워딩`
   - Đức: `Spedition Vietnam Seefracht`
   - Đài Loan: `越南 海運 承攬`, `台灣 越南 物流 報關`
   - Singapore: `Singapore freight forwarder Vietnam LCL consolidation`
   - Thái Lan: `ขนส่งสินค้า เวียดนาม`, `Thailand Vietnam cross-border trucking`
   - Malaysia: `penghantaran Vietnam`, `Port Klang Vietnam freight forwarder`

   Dùng thêm danh sách thành viên hiệp hội và danh sách đơn vị tham gia hội chợ ghi trong `preferred_sources`.
2. Với mỗi ứng viên: lấy tên miền, bỏ qua nếu nằm trong KNOWN hoặc đã thấy hôm nay.
3. Mở website ứng viên (gom tối đa 8 URL mỗi lượt `web_fetch_exa`), xác nhận các tiêu chí 1–5, rồi ghi:
   tên, website, thành phố, loại hình, dịch vụ, phương thức, tuyến, bằng chứng VN, network, email/điện thoại công khai, URL nguồn.
4. Riêng chiều tuyến VN: tìm cả dấu hiệu chiều xuất từ VN, ví dụ "Vietnam to …", `越南进口`, `往返`, "round-trip",
   văn phòng tại VN đứng tên chiều xuất.
5. Nếu một thị trường cạn nguồn, chuyển phần thiếu sang thị trường khác (ưu tiên `china`, rồi theo thứ tự `[quota]`).

**Lưu dần sau mỗi thị trường** để không mất dữ liệu nếu phiên bị ngắt:
- `leads/<ngày>-candidates.csv`: định dạng của `import-csv`, các cột
  `company_name,website,country,city,company_type,services,transport_modes,trade_lanes,vietnam_evidence,networks,general_emails,phones,source_urls,notes`
  (giá trị danh sách ngăn cách bằng `;`).
- `leads/<ngày>-lanes.json`: `{domain: VietnamLaneCheck}`. Các trường lấy theo `forwarder_agent/models.py`:
  `direction` (`import_to_vn` | `export_from_vn` | `both` | `none` | `unknown`), `modes`, `vn_gateways`,
  `counterpart_gateways`, `commodities`, `frequency_hint`, `has_vietnam_office`, `vietnam_partners`,
  `evidence` [{claim, url, source}], `confidence` (`high` | `medium` | `low`), `sources_agree` (null),
  `summary_vi`.

### B3. Chấm điểm theo rubric (0–100)

Rubric đầy đủ trong `forwarder_agent/qualify.py`. Tóm tắt:

| Tiêu chí | Điểm |
|---|---|
| Tuyến VN: cả hai chiều | 30 |
| Tuyến VN: một chiều, tin cậy cao/trung bình | 25 |
| Tuyến VN: tin cậy thấp | 15 |
| Tuyến VN: không có | 0 (kèm red flag) |
| Năng lực sea/air phù hợp | 20 |
| Network/hiệp hội | 15 |
| Ngành hàng liên quan VN | 15 |
| Có email/liên hệ công khai | 10 |
| Quy mô | 10 |
| Đã có văn phòng tại VN | −15 (kèm red flag) |

Xếp hạng: **A ≥ 75**, **B 55–74**, **C 35–54**, **D < 35**.

Ghi `leads/<ngày>-quals.json`: `{domain: Qualification}` với các trường `score`, `tier`, `is_forwarder`,
`vietnam_fit`, `recommended_services`, `outreach_angle` (tiếng Anh), `red_flags`, `reasons`.

### B4. Nạp vào agent và tạo file ngày

```bash
D=$(TZ=Asia/Ho_Chi_Minh date +%F)
```
```bash
python -m forwarder_agent --db data/leads.db import-csv leads/$D-candidates.csv --campaign daily-$D
```
```bash
python -m forwarder_agent --db data/leads.db import-lanes leads/$D-lanes.json
```
```bash
python -m forwarder_agent --db data/leads.db import-quals leads/$D-quals.json
```
```bash
python -m forwarder_agent --db data/leads.db daily-report
```
```bash
python -m forwarder_agent --db data/leads.db sheet-rows --since $D --out leads/$D-sheet-rows.json
```

### B5. Ghi lên Google Sheet qua connector

File `leads/<ngày>-sheet-rows.json` có dạng `{"header": [...], "tabs": {"China": [[...], ...], ...}}`.
Số điện thoại đã được thêm dấu `'` đầu để Sheets không biến chúng thành công thức.

Với mỗi tab:
- **Tab chưa có**: tạo tab tên quốc gia (`addSheet`), ghi `header` vào hàng 1, cố định hàng 1, rồi ghi các dòng.
- **Tab đã có**: đọc hàng 1. Nếu **khác** `header` thì **dừng lại và báo người dùng** (Sheet đang dùng bố cục cột cũ),
  không được ghi lệch cột. Nếu khớp thì dùng `append_values` vào `<Tab>!A1`.
- Không bao giờ ghi vào các cột nằm bên phải cột "Cập nhật" (đó là ghi chú của sales).

### B6. Lưu lịch sử vào git

```bash
git add leads/ && git commit -m "Daily agent hunt $D: <N> new agents" && git push -u origin HEAD
```

`data/` không được commit. Lịch sử lâu dài nằm ở `leads/` và trên Google Sheet.

### B7. Báo cáo (trả lời người dùng bằng tiếng Việt)

- Số agent mới / mục tiêu (50), chia theo thị trường. Nếu thiếu, nói rõ thiếu bao nhiêu và vì sao.
- Phân bố hạng A/B/C/D và chiều tuyến VN (nhập / xuất / cả hai / chưa rõ).
- Top 10 agent theo điểm (tên, quốc gia, điểm, website).
- Link Google Sheet và đường dẫn `leads/<ngày>.csv`.
- Những gì chưa xác minh được, ví dụ thông tin chỉ có từ LinkedIn hoặc không có email công khai.

## Lưu ý

- Không gửi email cho agent. Email nháp (nếu có) chỉ để sales duyệt và gửi thủ công.
- Nếu phiên sắp hết thời gian hoặc context, ưu tiên làm B4–B6 với những gì đã có, rồi báo cáo phần còn thiếu.
- Domain có trạng thái `do_not_contact` trên Sheet: không thêm lại, không liên hệ.
