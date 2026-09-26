# plugin 订单数据样例

> **订单号**：`585906224286303235`  
> **店铺**：`7494864868604150914`  
> **状态**：104（已完成）  
> **数据时间**：2026-09-21 查询

---

## plugin.orders — 订单头

| 字段 | 值 |
| --- | --- |
| id | 993 |
| shop_id | 7494864868604150914 |
| order_id | 585906224286303235 |
| main_order_status | 104 |
| sku_display_status | 140 |
| currency | VND |
| payment_amount | 576,207.0000 |
| total_amount | 576,207.0000 |
| fulfillment_type | 0 |
| pay_method | Cash on delivery |
| sale_region | VN |
| order_time | 2026-09-05 10:47:10+00:00 |
| update_time | 2026-09-16 05:12:30.341000+00:00 |
| latest_rts_time | 2026-09-07 10:47:12+00:00 |
| latest_tts_time | 2026-09-08 15:59:59+00:00 |
| buyer_nickname | k\*\*\*\*\*\*\*\*\*\*0 |
| created_at | 2026-09-13 15:32:52.380627+00:00 |
| updated_at | 2026-09-21 01:46:52.528273+00:00 |

---

## plugin.order_lines — 订单行（1 行）

| 字段 | 值 |
| --- | --- |
| id | 1022 |
| shop_id | 7494864868604150914 |
| order_id | 585906224286303235 |
| sku_id | 1737303421188539522 |
| product_id | 1737303446962537602 |
| product_name | Áo Polo Nam Ngắn Tay Mẫu Mới Nhất 2026 Chất Dệt Kim Trơn Mềm Mại, Cổ Bẻ Form Regular Thoải Mái Dễ Phối Đồ, Áo Có Cổ Nam Đẹp Phong Cách Tối Giản Thanh Lịch |
| variant_name | Nâu, XL 65-72.5 kg |
| image_url | <https://p16-oec-sg.ibyteimg.com/tos-alisg-i-aphluv4xwc-sg/89d89d8f2db1463984c0a379e060742f~tplv-aphluv4xwc-origin-jpeg.jpeg?dr=15568&t=555f072d&ps=933b5bde&shp=a3dd9c4f&shcp=569eacb7&idc=my2&from=2749209679> |
| quantity | 1.0000 |
| unit_price | 576,207.0000 |
| total_price | 576,207.0000 |
| currency | VND |
| main_order_status | 104 |
| sku_display_status | 140 |
| created_at | 2026-09-13 15:32:52.385070+00:00 |
| updated_at | 2026-09-21 01:46:52.534845+00:00 |

---

## plugin.shipments — 物流包裹（1 个）

| 字段 | 值 |
| --- | --- |
| id | 357 |
| shop_id | 7494864868604150914 |
| order_id | 585906224286303235 |
| package_id | 3335642352207102979 |
| tracking_number | WSWH3330269612 |
| carrier_name | J&T Express |
| status | 已送达商家 |
| shipped_at | 2026-09-05 10:47:10+00:00 |
| delivered_at | None |
| created_at | 2026-09-20 19:54:11.842084+00:00 |
| updated_at | 2026-09-21 00:36:47.102051+00:00 |

---

## plugin.tracking_events — 物流轨迹（49 条）

package_id = 3335642352207102979

| 时间 (UTC) | 事件描述 |
| --- | --- |
| 2026-09-05 10:47:10 | 已下单 |
| 2026-09-05 19:35:46 | 准备发货 |
| 2026-09-07 17:47:00 | 正在运送中 |
| 2026-09-07 22:38:22 | 正在运送中 |
| 2026-09-08 01:32:44 | 正在运送中 |
| 2026-09-08 02:34:40 | 准备发货 |
| 2026-09-09 03:10:00 | 正在运送中 |
| 2026-09-09 05:41:39 | 正在运送中 |
| 2026-09-09 07:46:00 | 正在运送中 |
| 2026-09-09 10:57:xx | 正在运送中 |
| 2026-09-10 xx:xx:xx | 正在运送中 |
| ... | （中间约 30 条"正在运送中"） |
| 2026-09-15 16:02:42 | 配送尝试失败并已退回给商家 |
| 2026-09-15 17:33:22 | 配送尝试失败并已退回给商家 |
| 2026-09-15 18:49:51 | 配送尝试失败并已退回给商家 |
| 2026-09-15 18:56:19 | 正在运送中 |
| 2026-09-15 22:51:28 | 配送尝试失败并已退回给商家 |
| 2026-09-16 03:45:25 | 配送尝试失败并已退回给商家 |
| 2026-09-16 05:11:25 | 已送达商家 |

---

## plugin.settlement_details — 结算明细

0 条。

---

## plugin.after_sales — 售后记录

0 条。
