# Описание источников

Анализ на 14.10.2026. Публичных API и готовых парсеров на GitHub для обеих бирж найти не удалось.

## ETS — https://ets.kz/documents/trading-results/

Структура сайта: `Итоги торгов` → год (`/2025-god/`, `/itogi-torgov-2026/`) → месяц (`.../itogi-torgov-2026-god-oktyabr/`) → файл за торговый день.
Каждый день — один XLSX (`<a href="/upload/iblock/…/….xlsx" download="07.10.2026 … ИТОГИ ТОРГОВ.xlsx">`). Дата торгов берётся из названия файла/заголовка листа (`ИТОГИ ТОРГОВ 07.10.2026`).
Страницы отдаются в UTF-8, JS для получения ссылок не нужен — достаточно requests + BeautifulSoup.

### Колонки XLSX (лист «07.10», шапка в 3-й строке, данные с 4-й, последняя строка «Итого» пропускается)

| Колонка | Поле в БД |
|---|---|
| B Наименование покупателя | buyer.name |
| C БИН покупателя | buyer.bin |
| D Брокер покупателя | buyer_broker |
| E Наименование продавца | seller.name |
| F БИН продавца | seller.bin |
| G Брокер продавца | seller_broker |
| H Наименование товара (каз/рус через `/`) | product.name |
| I Код ТН ВЭД ЕАЭС | product.tnved |
| J Код товара | product.code |
| K Количество заключённых сделок | deals_count |
| L Цена открытия | price_open |
| M Цена закрытия | price_close |
| N Максимальная цена | price_max |
| O Минимальная цена | price_min |
| P Средневзвешенная цена | price_avg |
| Q Общая сумма сделки (тг) | amount |

Нет в источнике: объём, единица измерения, базис/срок поставки (базис иногда зашит в название товара: `EXW`), время торгов, цена с/без НДС.
Строки — агрегат «покупатель + продавец + товар» за день.

## Каспий — https://ccx.kz/trading-and-analytics/trading-results

Страница рендерится сервером (Laravel, Blade), параметры в query string: `date_from`, `date_to`, `product_code`, `section_code` (CL уголь и т.д.), `instrument_regulated` (0 спот / 1 регулируемые), `tn_ved`, `broker_buyer_name`, `broker_seller_name`, `buyer_name`, `seller_name`, `delivery_term`, `page`, `per_page`.
У сайта есть внутренний JSON: `GET /api/internal/trading-results?section_code=…&instrument_regulated=true|false&max_results=N` → `{success, message, data:[{lot_code, lot_name, day_of_trade, basis, delivery_term, …}]}`.

**Статус проверки:** из тестовой среды (14.10.2026) и страница, и API возвращают пустой список («Нет данных для отображения») при любых параметрах. Пока не подтверждено, пустой ли источник на самом деле или данные закрыты по региону/сессии. Парсер Каспия пишется по известной структуре и доуточняется по реальному образцу данных.

## Единый формат (TradeRecord)

См. `docs/ARCHITECTURE.md`, раздел «Модель данных».
