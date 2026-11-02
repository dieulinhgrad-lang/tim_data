# tim_data

Công cụ thu thập dữ liệu Google Maps và Amazon qua **BrowserAct workflow-template API**
(template lấy từ [browser-act/skills](https://github.com/browser-act/skills/tree/main/solutions)).

## Cài đặt

```bash
pip install -r requirements.txt
export BROWSERACT_API_KEY=...   # lấy tại https://www.browseract.com/reception/integrations
```

## Sử dụng

```bash
# Google Maps – danh sách doanh nghiệp (tên, danh mục, địa chỉ, SĐT, website, rating)
python -m tim_data maps --keywords "nha khoa quận 1" --language vi --country vn --out data/nha_khoa.csv

# Google Maps – tìm kiếm có giới hạn số lượng (giá, tiện ích, đoạn review)
python -m tim_data maps-search --keywords "cà phê Đà Lạt" --language vi --country vn --limit 50 --out data/cafe.csv

# Google Maps – review của một địa điểm
python -m tim_data maps-reviews --keywords "Bến Thành Market" --out data/reviews.json

# Amazon – tìm sản phẩm (giá, rating, số review, doanh số tháng, best seller)
python -m tim_data amazon-search --keywords "wireless earbuds" --brand Sony --limit 30 --out data/earbuds.csv

# Amazon – review theo ASIN
python -m tim_data amazon-reviews --asin B0XXXXXXXX --out data/asin_reviews.json
```

- `--out` nhận `.csv` (UTF-8 BOM, mở thẳng bằng Excel) hoặc `.json`; bỏ trống thì in JSON ra màn hình.
- `--timeout` (mặc định 600 giây). Mỗi tác vụ chạy trên cloud BrowserAct và thường mất vài phút.
- Khi lỗi không phải do API key thì tool tự chạy lại 1 lần, giống khuyến nghị của skill gốc.
- Mã thoát: `0` thành công, `1` tác vụ lỗi, `2` thiếu hoặc sai API key.

Dùng trong Python:

```python
from tim_data import BrowserActClient, TEMPLATES, to_records
rows = to_records(BrowserActClient().run(TEMPLATES["maps"].template_id,
                                         {"keywords": "spa", "language": "vi", "country": "vn"}))
```

## Khác biệt so với script gốc

Các script `google-maps-*-api-skill`, `amazon-product-search`, `amazon-reviews`... trong
browser-act/skills đặt dòng báo `timed out` + `return None` **ngay sau vòng lặp poll** mà không có
điều kiện chặn. Hậu quả: khi tác vụ `finished` thì `break` thoát vòng lặp nhưng vẫn rơi vào nhánh
timeout, nên **kết quả không bao giờ được lấy về**. Client ở đây trả kết quả ngay khi tác vụ xong,
và có thêm xử lý lỗi xác thực, timeout, xuất CSV/JSON.

## Test

```bash
python -m unittest discover -s tests -v
```

Test giả lập API, không cần key và không gọi mạng.

## Lưu ý

Dịch vụ tính credit theo mỗi lần chạy. Hãy tuân thủ điều khoản của Google và Amazon cũng như
luật bảo vệ dữ liệu cá nhân (ví dụ Nghị định 13/2023 nếu dùng số điện thoại để liên hệ bán hàng).
