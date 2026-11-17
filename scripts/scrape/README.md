# Cào agent không cần Exa

Nguồn: danh bạ công khai freightforwarderservices.com (tên, website, địa chỉ, điện thoại, email từng công ty).

1. `python3 -I scripts/scrape/ffs.py out/ffs_all.json` — cào các trang quốc gia/thành phố/cảng (danh sách trong biến PAGES).
2. Lọc trùng với Sheet/leads (domain đã biết) và bỏ domain xuất hiện ở ≥2 nước (tập đoàn đa quốc gia) → `ffs_cand.json`.
3. `python3 -I scripts/scrape/vncheck.py ffs_cand.json ffs_vn.json` — đọc trang chủ + link có chữ Vietnam, lấy đoạn bằng chứng, email, hiệp hội.
4. `python3 -I scripts/scrape/smap.py ffs_vn.json ffs_sm.json` — quét sitemap tìm trang Vietnam ẩn sâu.
5. Duyệt tay đoạn bằng chứng (loại trường hợp Việt Nam chỉ nằm trong ô chọn quốc gia), rồi chạy pipeline như skill daily-agent-hunt.
