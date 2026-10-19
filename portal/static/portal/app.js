/* Портал итогов торгов ETS и Каспий.
 * Страница «/»      — таблица итогов торгов (fetch к /api/trades/, без перезагрузки);
 * страница «/status/» — состояние данных и ручной запуск сбора;
 * страница «/analytics/» — сводка и графики Chart.js (fetch к /api/analytics/);
 * страница «/news/»   — лента новостей (серверная разметка, JS только применяет фильтр);
 * на всех страницах   — баннер уведомления о cookie (согласие в localStorage)
 *                       и переключатель светлой / тёмной темы (выбор в localStorage).
 * Данные в DOM выводятся только через textContent / createElement (без innerHTML).
 */
(function () {
  'use strict';

  /* ===================================================== общие помощники */

  /** Создаёт элемент: el('div', {class: 'x', text: 'текст', data: {a: 1}}, [дети]). */
  function el(tag, props, children) {
    const node = document.createElement(tag);
    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'data') Object.assign(node.dataset, value);
        else node.setAttribute(key, value === true ? '' : value);
      }
    }
    for (const child of children || []) {
      if (child === null || child === undefined) continue;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /* ===================================================== язык интерфейса */

  /* Словарь переводов текущего языка выводит сервер в base.html (<script id="portal-i18n"
   * type="application/json">, тег {% portal_i18n %}): {lang, locale, messages}.
   * Ключ — русский текст строки (как msgid в gettext), значение — перевод.
   * Нет словаря или строки в нём — показывается русский текст. Список строк — portal/js_messages.py. */
  const I18N = (function () {
    try {
      const node = document.getElementById('portal-i18n');
      const data = node ? JSON.parse(node.textContent) : null;
      return data && typeof data === 'object' ? data : {};
    } catch (e) {
      return {};
    }
  })();
  const LANG = I18N.lang || document.documentElement.getAttribute('lang') || 'ru';
  const MESSAGES = I18N.messages || {};

  /** Локаль для Intl: kk-KZ поддерживают не все браузеры — тогда запасная ru-RU (те же форматы).
   * supportedLocalesOf недостаточно: встроенные браузеры с урезанными данными ICU «поддерживают»
   * kk-KZ, но форматируют по корневой локали (2026-10-17, 6,149). Поэтому проверяем результат. */
  const LOCALE = (function () {
    const SAMPLE_DATE = { 'kk-KZ': '17.10.2026', 'ru-RU': '17.10.2026', 'en-GB': '17/10/2026' };
    const candidates = [I18N.locale, 'ru-RU'].filter(Boolean);
    for (const candidate of candidates) {
      try {
        if (!Intl.NumberFormat.supportedLocalesOf([candidate]).length) continue;
        const expected = SAMPLE_DATE[candidate];
        const date = new Intl.DateTimeFormat(candidate, { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' })
          .format(new Date(Date.UTC(2026, 9, 17)));
        const number = new Intl.NumberFormat(candidate, { maximumFractionDigits: 1 }).format(1234.5);
        const decimalComma = candidate !== 'en-GB';
        if (expected && date !== expected) continue;
        if (decimalComma ? !/,5$/.test(number) : !/\.5$/.test(number)) continue;
        return candidate;
      } catch (e) { /* некорректный тег локали */ }
    }
    return 'ru-RU';
  })();

  /** Перевод строки интерфейса: _(русский текст, { имя: значение }) — {имя} в тексте заменяется значением. */
  function _(text, params) {
    let value = Object.prototype.hasOwnProperty.call(MESSAGES, text) && MESSAGES[text] ? MESSAGES[text] : text;
    if (params) {
      value = value.replace(/\{(\w+)\}/g, (match, key) => (
        Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : match));
    }
    return value;
  }

  const nfInt = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
  const nfMoney = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nfQty = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 0, maximumFractionDigits: 3 });
  // даты из API — календарные дни без часового пояса: форматируем как полночь UTC
  const dfDate = new Intl.DateTimeFormat(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
  const dfDayMonth = new Intl.DateTimeFormat(LOCALE, { day: '2-digit', month: '2-digit', timeZone: 'UTC' });
  const dfDayMonthYear = new Intl.DateTimeFormat(LOCALE, { day: '2-digit', month: '2-digit', year: '2-digit', timeZone: 'UTC' });

  function isoDay(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    return m ? new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))) : null;
  }

  /** 'ГГГГ-ММ-ДД' -> дата по правилам языка: 17.10.2026 (ru, kk), 17/10/2026 (en). */
  function formatDate(iso) {
    if (!iso) return '';
    const day = isoDay(iso);
    return day ? dfDate.format(day) : iso;
  }

  /** ISO-время сервера (уже в местном поясе) -> «дата чч:мм» без пересчёта поясов. */
  function formatDateTime(iso) {
    if (!iso) return '';
    const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso);
    return m ? formatDate(m[1]) + ' ' + m[2] : formatDate(iso);
  }

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /** Форма слова при числе: plural(5, 'запись|записи|записей').
   * Формы — через «|» в переводе: в русском три, в английском две, в казахском одна. */
  function plural(n, forms) {
    const list = _(forms).split('|');
    if (list.length === 1) return list[0];
    if (list.length >= 3) {
      const a = Math.abs(n) % 100;
      const b = a % 10;
      if (a > 10 && a < 20) return list[2];
      if (b > 1 && b < 5) return list[1];
      if (b === 1) return list[0];
      return list[2];
    }
    return Math.abs(n) === 1 ? list[0] : list[1];
  }

  /** Название биржи по коду (из API приходит уже переведённое; это — запасной вариант). */
  function exchangeName(code) {
    return code === 'caspy' ? _('Каспий') : 'ETS';
  }

  function dash() {
    return el('span', { class: 'dash', text: '—' });
  }

  function exchangeBadge(code, name) {
    return el('span', { class: 'badge badge-' + (code === 'caspy' ? 'caspy' : 'ets'), text: name || code });
  }

  async function readError(response) {
    try {
      const data = await response.json();
      if (data && data.error) return data.error;
    } catch (e) { /* тело не JSON */ }
    if (response.status === 403) return _('Сессия устарела или нет токена CSRF: обновите страницу и повторите');
    return _('Ошибка сервера ({status})', { status: response.status });
  }

  function debounce(fn, delay) {
    let timer = null;
    const wrapped = function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(null, args), delay);
    };
    wrapped.cancel = () => clearTimeout(timer);
    return wrapped;
  }

  function getCsrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  /** Число заполненных фильтров формы — в кнопке «Фильтры (N)» на телефоне. */
  function updateFiltersBadge(form) {
    const panel = form && form.closest('.filters');
    if (!panel) return;
    const count = form.querySelectorAll('.has-value').length;
    const counter = panel.querySelector('.filters-count');
    if (counter) counter.textContent = count ? '(' + count + ')' : '';
    const reset = panel.querySelector('.filters-bar-reset');
    if (reset) reset.hidden = count === 0;
  }

  /** На узком экране панель фильтров сворачивается в кнопку (раскладка — в CSS). */
  function initCollapsibleFilters() {
    document.querySelectorAll('.filters[data-collapsible]').forEach((panel) => {
      const toggle = panel.querySelector('.filters-toggle');
      if (!toggle) return;
      panel.classList.add('filters-collapsible');
      toggle.addEventListener('click', () => {
        const open = !panel.classList.contains('is-open');
        panel.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      const reset = panel.querySelector('.filters-bar-reset');
      if (reset) {
        reset.addEventListener('click', () => {
          const target = document.getElementById(reset.dataset.reset);
          if (target) target.click();
        });
      }
    });
  }

  /** Совпадает с точкой перелома в CSS, где таблица торгов становится списком карточек. */
  const narrowScreen = window.matchMedia ? window.matchMedia('(max-width: 700px)') : { matches: false };

  /* ===================================================== тема оформления */

  /* Без явного выбора — системная настройка (prefers-color-scheme). Нажатие на кнопку —
   * явный выбор: атрибут data-theme на <html> и запись в localStorage (её же читает
   * маленький скрипт в <head> до отрисовки). Смена темы — событие 'portal:themechange'. */
  const THEME_KEY = 'portal_theme';
  const darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function storedTheme() {
    try {
      const value = window.localStorage.getItem(THEME_KEY);
      return value === 'dark' || value === 'light' ? value : null;
    } catch (e) {
      return null;
    }
  }

  function currentTheme() {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr === 'dark' || attr === 'light') return attr;
    return darkQuery && darkQuery.matches ? 'dark' : 'light';
  }

  function initThemeToggle() {
    const root = document.documentElement;
    const button = document.getElementById('theme-toggle');

    function updateButton() {
      if (!button) return;
      const label = currentTheme() === 'dark' ? _('Светлая тема') : _('Тёмная тема');
      button.setAttribute('aria-label', label);
      button.title = label;
    }

    function changed() {
      updateButton();
      document.dispatchEvent(new CustomEvent('portal:themechange', { detail: { theme: currentTheme() } }));
    }

    updateButton();
    if (button) {
      button.addEventListener('click', () => {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        try { window.localStorage.setItem(THEME_KEY, next); } catch (e) { /* хранилище недоступно: выбор до перезагрузки */ }
        changed();
      });
    }
    if (darkQuery) {
      const onSystemChange = () => { if (!root.hasAttribute('data-theme')) changed(); };
      if (darkQuery.addEventListener) darkQuery.addEventListener('change', onSystemChange);
      else if (darkQuery.addListener) darkQuery.addListener(onSystemChange);
    }
    // тему сменили в другой вкладке
    window.addEventListener('storage', (event) => {
      if (event.key !== THEME_KEY) return;
      const value = storedTheme();
      if (value) root.setAttribute('data-theme', value);
      else root.removeAttribute('data-theme');
      changed();
    });
  }

  /* ===================================================== переключатель языка */

  /* Форма в шапке отправляет POST на стандартный /i18n/setlang/ (с CSRF). Фильтры страниц
   * хранятся в адресной строке и меняются без перезагрузки (history.replaceState), поэтому
   * перед отправкой в поле next подставляется текущий адрес — со всеми фильтрами. */
  function initLanguageSwitch() {
    document.querySelectorAll('form.lang-switch').forEach((form) => {
      form.addEventListener('submit', () => {
        const next = form.querySelector('input[name="next"]');
        if (next) next.value = window.location.pathname + window.location.search;
      });
    });
  }

  /* ===================================================== страница «Итоги торгов» */

  function initTrades() {
    const DEFAULT_ORDERING = '-trade_date';
    const DEFAULT_PAGE_SIZE = 50;
    const FILTER_KEYS = ['exchange', 'date_from', 'date_to', 'q', 'party', 'broker', 'amount_min', 'amount_max', 'delivery'];
    const TEXT_KEYS = ['q', 'party', 'broker', 'amount_min', 'amount_max', 'delivery'];
    const SUGGEST_KEYS = { q: 'product', party: 'party', broker: 'broker', delivery: 'delivery' };

    // key — поле сортировки в API; firstDir — направление при первом клике
    const COLUMNS = [
      { key: 'trade_date', label: _('Дата'), firstDir: '-' },
      { key: 'exchange', label: _('Биржа'), firstDir: '' },
      { key: 'product', label: _('Товар'), firstDir: '' },
      { key: 'seller', label: _('Продавец'), firstDir: '' },
      { key: 'buyer', label: _('Покупатель'), firstDir: '' },
      { key: 'seller_broker', label: _('Брокер продавца'), firstDir: '' },
      { key: 'buyer_broker', label: _('Брокер покупателя'), firstDir: '' },
      { key: 'quantity', label: _('Объём'), num: true, firstDir: '-' },
      { key: 'price_avg', label: _('Цена ср., тг'), num: true, firstDir: '-' },
      { key: 'amount', label: _('Сумма, тг'), num: true, firstDir: '-' },
      { key: 'delivery_place', label: _('Место / базис поставки'), firstDir: '' },
    ];
    const LABELS = {};
    COLUMNS.forEach((col) => { LABELS[col.key] = col.label; });
    const SORT_KEYS = COLUMNS.map((c) => c.key);

    const inputs = {};
    FILTER_KEYS.forEach((key) => { inputs[key] = document.getElementById('f-' + key); });
    const pageSizeSelect = document.getElementById('page-size');
    const headRow = document.getElementById('head-row');
    const body = document.getElementById('trades-body');
    const table = document.getElementById('trades-table');
    const tableWrap = document.getElementById('table-wrap');
    const loader = document.getElementById('loader');
    const emptyState = document.getElementById('empty-state');
    const errorState = document.getElementById('error-state');
    const errorText = document.getElementById('error-text');
    const foundCount = document.getElementById('found-count');
    const foundSum = document.getElementById('found-sum');
    const pagination = document.getElementById('pagination');
    const exportLink = document.getElementById('btn-export');
    const sortSelect = document.getElementById('sort-select');
    const filtersForm = document.getElementById('filters-form');
    const resultsPanel = document.querySelector('.panel.results');

    const state = { ordering: DEFAULT_ORDERING, page: 1, pageSize: DEFAULT_PAGE_SIZE };
    let requestSeq = 0;
    let abortController = null;

    /* ---- состояние <-> адресная строка ---- */

    function applyPageSize(value) {
      const size = Math.max(10, Math.min(200, parseInt(value, 10) || DEFAULT_PAGE_SIZE));
      state.pageSize = size;
      if (![...pageSizeSelect.options].some((o) => parseInt(o.value, 10) === size)) {
        const opt = el('option', { value: String(size), text: String(size) });
        const next = [...pageSizeSelect.options].find((o) => parseInt(o.value, 10) > size);
        pageSizeSelect.insertBefore(opt, next || null);
      }
      pageSizeSelect.value = String(size);
    }

    function readUrl() {
      const params = new URLSearchParams(window.location.search);
      FILTER_KEYS.forEach((key) => {
        const input = inputs[key];
        if (!input) return;
        const value = params.get(key) || '';
        // значение select принимаем только из списка допустимых
        if (input.tagName === 'SELECT' && ![...input.options].some((o) => o.value === value)) return;
        input.value = value;
      });
      const ordering = params.get('ordering') || DEFAULT_ORDERING;
      state.ordering = SORT_KEYS.includes(ordering.replace(/^-/, '')) ? ordering : DEFAULT_ORDERING;
      state.page = Math.max(1, parseInt(params.get('page'), 10) || 1);
      applyPageSize(params.get('page_size'));
    }

    function filterParams() {
      const params = new URLSearchParams();
      FILTER_KEYS.forEach((key) => {
        const value = (inputs[key].value || '').trim();
        if (value) params.set(key, value);
      });
      return params;
    }

    function exportParams() {
      const params = filterParams();
      params.set('ordering', state.ordering);
      return params;
    }

    function apiParams() {
      const params = filterParams();
      params.set('ordering', state.ordering);
      params.set('page', String(state.page));
      params.set('page_size', String(state.pageSize));
      return params;
    }

    function syncUrl() {
      const params = filterParams();
      if (state.ordering !== DEFAULT_ORDERING) params.set('ordering', state.ordering);
      if (state.page > 1) params.set('page', String(state.page));
      if (state.pageSize !== DEFAULT_PAGE_SIZE) params.set('page_size', String(state.pageSize));
      const query = params.toString();
      history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
      exportLink.href = '/api/trades/export.xlsx?' + exportParams().toString();
      FILTER_KEYS.forEach((key) => inputs[key].classList.toggle('has-value', !!(inputs[key].value || '').trim()));
      updateFiltersBadge(filtersForm);
    }

    /* ---- шапка таблицы и сортировка ---- */

    function renderHead() {
      clear(headRow);
      const currentKey = state.ordering.replace(/^-/, '');
      const desc = state.ordering.startsWith('-');
      COLUMNS.forEach((col) => {
        const active = col.key === currentKey;
        const arrow = el('span', { class: 'th-arrow', 'aria-hidden': 'true', text: active ? (desc ? '▼' : '▲') : '↕' });
        const button = el('button', { type: 'button', class: 'th-sort', title: _('Сортировать') }, [col.label, arrow]);
        button.addEventListener('click', () => onSort(col));
        const th = el('th', { scope: 'col', class: col.num ? 'num' : '' }, [button]);
        th.setAttribute('aria-sort', active ? (desc ? 'descending' : 'ascending') : 'none');
        headRow.appendChild(th);
      });
      if (sortSelect) sortSelect.value = state.ordering;
    }

    /** Сортировка на телефоне, где шапки таблицы нет: те же поля и направления. */
    function initSortSelect() {
      if (!sortSelect) return;
      COLUMNS.forEach((col) => {
        const order = col.firstDir === '-' ? ['-', ''] : ['', '-'];
        order.forEach((dir) => {
          const isDate = col.key === 'trade_date';
          const text = dir === '-'
            ? (isDate ? _('{column}: сначала новые', { column: col.label }) : _('{column}: по убыванию', { column: col.label }))
            : (isDate ? _('{column}: сначала старые', { column: col.label }) : _('{column}: по возрастанию', { column: col.label }));
          sortSelect.appendChild(el('option', { value: dir + col.key, text }));
        });
      });
      sortSelect.addEventListener('change', () => {
        state.ordering = sortSelect.value;
        state.page = 1;
        renderHead();
        load();
      });
    }

    function onSort(col) {
      const currentKey = state.ordering.replace(/^-/, '');
      if (currentKey === col.key) {
        state.ordering = state.ordering.startsWith('-') ? col.key : '-' + col.key;
      } else {
        state.ordering = col.firstDir + col.key;
      }
      state.page = 1;
      renderHead();
      load();
    }

    /* ---- отрисовка строк ---- */

    function textCell(value, className, label) {
      const td = el('td', { class: className || '', 'data-label': label });
      if (value) td.appendChild(el('div', { class: 'cell-main', text: value }));
      else td.appendChild(dash());
      return td;
    }

    function partyCell(name, bin, className, label) {
      const td = el('td', { class: 'cell-party ' + className, 'data-label': label });
      if (!name) { td.appendChild(dash()); return td; }
      td.appendChild(el('div', { class: 'cell-main', text: name }));
      if (bin) td.appendChild(el('div', { class: 'cell-sub', text: _('БИН {bin}', { bin }) }));
      return td;
    }

    /** Место поставки; базис (FCA, EXW…) — второй строкой. У ETS места нет — показываем базис. */
    function placeCell(place, basis, label) {
      const td = el('td', { class: 'cell-place', 'data-label': label });
      if (!place && !basis) { td.appendChild(dash()); return td; }
      td.appendChild(el('div', { class: 'cell-main', text: place || basis }));
      if (place && basis) td.appendChild(el('div', { class: 'cell-sub', text: _('базис {basis}', { basis }) }));
      return td;
    }

    function numCell(value, formatter, className, sub, label) {
      const td = el('td', { class: 'num ' + (className || ''), 'data-label': label });
      if (isNumber(value)) {
        td.appendChild(el('div', { class: 'cell-main', text: formatter.format(value) }));
        if (sub) td.appendChild(el('div', { class: 'cell-sub', text: sub }));
      } else {
        td.appendChild(dash());
      }
      return td;
    }

    /** Подпись валюты после суммы в карточке на телефоне (CSS: content: attr(data-currency)). */
    function currencyCell(td) {
      const main = td.querySelector('.cell-main');
      if (main) main.dataset.currency = _('тг');
      return td;
    }

    function detailItem(label, value) {
      const dd = el('dd');
      if (value instanceof Node) dd.appendChild(value);
      else dd.textContent = value === '' || value === null || value === undefined ? '—' : String(value);
      return el('div', {}, [el('dt', { text: label }), dd]);
    }

    function safeLink(url) {
      // разрешаем только http/https, иначе показываем простым текстом
      if (/^https?:\/\//i.test(url)) {
        return el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text: url });
      }
      return document.createTextNode(url);
    }

    function money(value) { return isNumber(value) ? _('{amount} тг', { amount: nfMoney.format(value) }) : '—'; }

    function buildDetails(row) {
      const items = [
        detailItem(_('Время торгов'), row.trade_time ? row.trade_time.slice(0, 5) : ''),
        detailItem(_('Число сделок'), isNumber(row.deals_count) ? nfInt.format(row.deals_count) : ''),
        detailItem(_('Код товара'), row.product_code),
        detailItem(_('Код ТН ВЭД'), row.tnved),
        detailItem(_('Цена открытия'), money(row.price_open)),
        detailItem(_('Цена закрытия'), money(row.price_close)),
        detailItem(_('Мин. цена'), money(row.price_min)),
        detailItem(_('Макс. цена'), money(row.price_max)),
        detailItem(_('Цена с НДС'), money(row.price_with_vat)),
        detailItem(_('Цена без НДС'), money(row.price_without_vat)),
        detailItem(_('Базис поставки'), row.delivery_basis),
        detailItem(_('Срок поставки'), row.delivery_term),
      ];
      if (row.source_url) items.push(detailItem(_('Источник'), safeLink(row.source_url)));
      const td = el('td', { colspan: String(COLUMNS.length) }, [
        el('div', { class: 'details-inner' }, [el('dl', { class: 'details-grid' }, items)]),
      ]);
      return el('tr', { class: 'details-row' }, [td]);
    }

    function toggleDetails(tr, row) {
      const next = tr.nextElementSibling;
      if (next && next.classList.contains('details-row')) {
        next.remove();
        tr.classList.remove('open');
        tr.setAttribute('aria-expanded', 'false');
      } else {
        tr.after(buildDetails(row));
        tr.classList.add('open');
        tr.setAttribute('aria-expanded', 'true');
      }
    }

    function renderRow(row) {
      const tr = el('tr', { class: 'trade-row', tabindex: '0', 'aria-expanded': 'false', title: _('Показать подробности') }, [
        el('td', { class: 'cell-date', text: formatDate(row.trade_date) }),
        el('td', { class: 'cell-exchange' }, [exchangeBadge(row.exchange, row.exchange_name)]),
        textCell(row.product, 'cell-product'),
        partyCell(row.seller, row.seller_bin, 'cell-seller', LABELS.seller),
        partyCell(row.buyer, row.buyer_bin, 'cell-buyer', LABELS.buyer),
        textCell(row.seller_broker, 'cell-broker cell-sbroker', LABELS.seller_broker),
        textCell(row.buyer_broker, 'cell-broker cell-bbroker', LABELS.buyer_broker),
        numCell(row.quantity, nfQty, 'cell-qty', row.unit, LABELS.quantity),
        numCell(row.price_avg, nfMoney, 'cell-price', null, LABELS.price_avg),
        currencyCell(numCell(row.amount, nfMoney, 'amount')),
        placeCell(row.delivery_place, row.delivery_basis, LABELS.delivery_place),
      ]);
      tr.addEventListener('click', (event) => {
        if (event.target.closest('a')) return;
        toggleDetails(tr, row);
      });
      tr.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggleDetails(tr, row);
        }
      });
      return tr;
    }

    function renderRows(rows) {
      clear(body);
      const fragment = document.createDocumentFragment();
      rows.forEach((row) => fragment.appendChild(renderRow(row)));
      body.appendChild(fragment);
    }

    /* ---- пагинация ---- */

    function pageButton(label, page, options) {
      const opts = options || {};
      const button = el('button', { type: 'button', class: 'page-btn', text: label });
      if (opts.current) button.setAttribute('aria-current', 'page');
      if (opts.disabled) button.disabled = true;
      if (opts.title) button.title = opts.title;
      if (!opts.disabled && !opts.current) {
        button.addEventListener('click', () => {
          state.page = page;
          load();
          tableWrap.scrollTop = 0;
          if (narrowScreen.matches && resultsPanel) resultsPanel.scrollIntoView({ block: 'start' });
        });
      }
      return button;
    }

    function renderPagination(page, pages) {
      clear(pagination);
      if (pages <= 1) return;
      pagination.appendChild(pageButton(_('‹ Назад'), page - 1, { disabled: page <= 1 }));
      const visible = new Set([1, pages]);
      for (let p = page - 2; p <= page + 2; p++) if (p >= 1 && p <= pages) visible.add(p);
      let previous = 0;
      [...visible].sort((a, b) => a - b).forEach((p) => {
        if (p - previous > 1) pagination.appendChild(el('span', { class: 'page-gap', text: '…' }));
        pagination.appendChild(pageButton(nfInt.format(p), p, { current: p === page }));
        previous = p;
      });
      pagination.appendChild(pageButton(_('Вперёд ›'), page + 1, { disabled: page >= pages }));
    }

    /* ---- загрузка данных ---- */

    function setLoading(on) {
      loader.hidden = !on;
      tableWrap.classList.toggle('loading', on);
      tableWrap.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    function showError(message) {
      table.hidden = true;
      emptyState.hidden = true;
      errorState.hidden = false;
      errorText.textContent = message;
      foundCount.textContent = _('Ошибка загрузки');
      foundSum.textContent = '';
      clear(pagination);
    }

    function renderResult(data) {
      errorState.hidden = true;
      const count = data.count;
      if (count === 0) {
        table.hidden = true;
        emptyState.hidden = false;
        clear(body);
        foundCount.textContent = _('Найдено: {count} {records}', { count: nfInt.format(0), records: plural(0, 'запись|записи|записей') });
        foundSum.textContent = '';
        clear(pagination);
        return;
      }
      table.hidden = false;
      emptyState.hidden = true;
      renderRows(data.results);
      const from = (data.page - 1) * data.page_size + 1;
      const to = from + data.results.length - 1;
      foundCount.textContent = _('Найдено: {count} {records}', { count: nfInt.format(count), records: plural(count, 'запись|записи|записей') }) +
        ' · ' + _('показаны {from}–{to}', { from: nfInt.format(from), to: nfInt.format(to) });
      foundSum.textContent = isNumber(data.amount_sum)
        ? _('Сумма по выборке: {amount} тг', { amount: nfMoney.format(data.amount_sum) }) : '';
      renderPagination(data.page, data.pages);
    }

    async function load() {
      const seq = ++requestSeq;
      if (abortController) abortController.abort();
      abortController = new AbortController();
      syncUrl();
      setLoading(true);
      try {
        const response = await fetch('/api/trades/?' + apiParams().toString(), {
          signal: abortController.signal,
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(await readError(response));
        const data = await response.json();
        if (seq !== requestSeq) return;
        // сервер мог поправить номер страницы (например, она вышла за предел)
        state.page = data.page;
        syncUrl();
        renderResult(data);
        setLoading(false);
      } catch (error) {
        if (error.name === 'AbortError' || seq !== requestSeq) return;
        showError(error.message || _('Не удалось связаться с сервером'));
        setLoading(false);
      }
    }

    /* ---- подсказки для текстовых фильтров ---- */

    function fillDatalist(id, values) {
      const list = document.getElementById(id);
      clear(list);
      values.forEach((value) => list.appendChild(el('option', { value })));
    }

    const suggestSeq = {};
    async function loadSuggestions(key) {
      const field = SUGGEST_KEYS[key];
      const term = (inputs[key].value || '').trim();
      const listId = 'dl-' + field;
      if (term.length < 2) {
        if (key === 'delivery') fillDatalist(listId, topPlaces);
        else fillDatalist(listId, []);
        return;
      }
      const seq = (suggestSeq[key] = (suggestSeq[key] || 0) + 1);
      try {
        const response = await fetch('/api/filters/?field=' + field + '&term=' + encodeURIComponent(term));
        if (!response.ok) return;
        const data = await response.json();
        if (seq === suggestSeq[key]) fillDatalist(listId, data.suggestions || []);
      } catch (e) { /* подсказки необязательны */ }
    }

    let topPlaces = [];
    async function loadFilterLists() {
      try {
        const response = await fetch('/api/filters/');
        if (!response.ok) return;
        const data = await response.json();
        if (Array.isArray(data.exchanges) && data.exchanges.length) {
          const select = inputs.exchange;
          const current = select.value;
          clear(select);
          select.appendChild(el('option', { value: '', text: _('Обе биржи') }));
          data.exchanges.forEach((e) => select.appendChild(el('option', { value: e.code, text: e.name })));
          select.value = [...select.options].some((o) => o.value === current) ? current : '';
        }
        topPlaces = (data.delivery_places || []).map((p) => p.name);
        fillDatalist('dl-delivery', topPlaces);
      } catch (e) { /* без списков страница работает */ }
    }

    /* ---- события ---- */

    const loadDebounced = debounce(() => { state.page = 1; load(); }, 350);
    const suggestDebounced = {};
    TEXT_KEYS.forEach((key) => {
      const input = inputs[key];
      input.addEventListener('input', () => {
        loadDebounced();
        if (SUGGEST_KEYS[key]) {
          suggestDebounced[key] = suggestDebounced[key] || debounce(() => loadSuggestions(key), 250);
          suggestDebounced[key]();
        }
      });
    });
    ['exchange', 'date_from', 'date_to'].forEach((key) => {
      inputs[key].addEventListener('change', () => { loadDebounced.cancel(); state.page = 1; load(); });
    });
    document.getElementById('filters-form').addEventListener('submit', (event) => {
      event.preventDefault();
      loadDebounced.cancel();
      state.page = 1;
      load();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      FILTER_KEYS.forEach((key) => { inputs[key].value = ''; });
      state.ordering = DEFAULT_ORDERING;
      state.page = 1;
      loadDebounced.cancel();
      fillDatalist('dl-product', []);
      fillDatalist('dl-party', []);
      fillDatalist('dl-broker', []);
      fillDatalist('dl-delivery', topPlaces);
      renderHead();
      load();
    });
    pageSizeSelect.addEventListener('change', () => {
      applyPageSize(pageSizeSelect.value);
      state.page = 1;
      load();
    });
    document.getElementById('btn-retry').addEventListener('click', load);

    /* ---- старт ---- */
    readUrl();
    initSortSelect();
    renderHead();
    syncUrl();
    load();
    loadFilterLists().then(() => {
      // список бирж мог перестроиться — восстанавливаем значение из адреса
      const params = new URLSearchParams(window.location.search);
      const value = params.get('exchange') || '';
      if ([...inputs.exchange.options].some((o) => o.value === value)) inputs.exchange.value = value;
      syncUrl();
    });
  }

  /* ===================================================== страница «Состояние данных» */

  function initStatus() {
    const cardsBox = document.getElementById('exchange-cards');
    const runsBody = document.getElementById('runs-body');
    const runsEmpty = document.getElementById('runs-empty');
    const collectForm = document.getElementById('collect-form');
    const collectButton = document.getElementById('btn-collect');
    const collectBox = document.getElementById('collect-status');
    const totalLine = document.getElementById('total-line');
    const daysInput = document.getElementById('c-days');
    const POLL_FAST = 2000;
    const POLL_SLOW = 20000;
    let pollTimer = null;
    let lastMessage = null; // сообщение пользователю после нажатия кнопки (ответ сервера)

    const STATUS_LABELS = { success: _('Успешно'), partial: _('С ошибками'), failed: _('Ошибка'), running: _('Выполняется') };

    function statusBadge(status, label) {
      if (!status) return el('span', { class: 'badge badge-none', text: _('Нет запусков') });
      return el('span', { class: 'badge badge-' + status, text: label || STATUS_LABELS[status] || status });
    }

    function stat(label, valueNode, big) {
      const dd = el('dd', { class: big ? 'big' : '' });
      if (valueNode instanceof Node) dd.appendChild(valueNode);
      else dd.textContent = valueNode;
      return el('div', { class: 'stat' }, [el('dt', { text: label }), dd]);
    }

    function periodText(run) {
      if (!run.date_from && !run.date_to) return '—';
      return formatDate(run.date_from) + ' – ' + formatDate(run.date_to);
    }

    function renderCard(exchange) {
      const run = exchange.last_run;
      const stats = [
        stat(_('Последняя торговая запись'), exchange.last_trade_date ? formatDate(exchange.last_trade_date) : '—', true),
        stat(_('Записей в базе'), nfInt.format(exchange.trades_count), true),
      ];
      if (run) {
        stats.push(stat(_('Последний запуск'), formatDateTime(run.started_at) + ' (' + run.trigger_display.toLowerCase() + ')'));
        stats.push(stat(_('Период запуска'), periodText(run)));
        const result = el('span', {}, [
          _('+{added} добавлено, {skipped} пропущено', {
            added: nfInt.format(run.rows_added), skipped: nfInt.format(run.rows_skipped),
          }),
        ]);
        stats.push(stat(_('Результат'), result));
        stats.push(stat(_('Окончание'), formatDateTime(run.finished_at) || (run.status === 'running' ? _('идёт…') : '—')));
      } else {
        stats.push(stat(_('Последний запуск'), _('ещё не запускался')));
      }
      const children = [
        el('div', { class: 'card-head' }, [
          el('h2', { class: 'card-title', text: exchange.name }),
          statusBadge(run && run.status, run && run.status_display),
        ]),
        el('dl', { class: 'stat-grid' }, stats),
      ];
      if (run && run.error) children.push(el('div', { class: 'card-error', text: run.error }));
      return el('div', { class: 'panel' }, children);
    }

    function renderRuns(runs) {
      clear(runsBody);
      runsEmpty.hidden = runs.length > 0;
      runs.forEach((run) => {
        runsBody.appendChild(el('tr', {}, [
          el('td', {}, [exchangeBadge(run.exchange, run.exchange_name)]),
          el('td', { text: formatDateTime(run.started_at), class: 'cell-date' }),
          el('td', { text: formatDateTime(run.finished_at) || '—', class: 'cell-date' }),
          el('td', { text: run.trigger_display }),
          el('td', { text: periodText(run), class: 'cell-date' }),
          el('td', {}, [statusBadge(run.status, run.status_display)]),
          el('td', { class: 'num', text: nfInt.format(run.rows_added) }),
          el('td', { class: 'num', text: nfInt.format(run.rows_skipped) }),
          el('td', { class: 'cell-error', text: run.error || '' }),
        ]));
      });
    }

    function setCollectBox(kind, nodes) {
      collectBox.hidden = false;
      collectBox.className = 'collect-status ' + kind;
      clear(collectBox);
      nodes.forEach((n) => collectBox.appendChild(typeof n === 'string' ? document.createTextNode(n) : n));
    }

    function renderCollect(collect) {
      if (!collectForm) return; // демо-режим «только чтение»: формы запуска нет
      const busy = collect.running || collect.running_elsewhere;
      collectButton.disabled = busy;
      if (collect.running) {
        const names = (collect.exchanges || []).map((c) => (c === 'caspy' ? exchangeName(c) : c.toUpperCase())).join(', ');
        setCollectBox('running', [
          el('span', { class: 'spinner' }),
          _('Идёт сбор: {exchanges}, период {from} – {to} (начат {started})', {
            exchanges: names, from: formatDate(collect.date_from), to: formatDate(collect.date_to),
            started: formatDateTime(collect.started_at),
          }),
        ]);
      } else if (collect.running_elsewhere) {
        setCollectBox('running', [
          el('span', { class: 'spinner' }),
          _('Сбор уже выполняется (запущен по расписанию или из командной строки). Дождитесь завершения.'),
        ]);
      } else if (lastMessage) {
        setCollectBox(lastMessage.kind, [lastMessage.text]);
      } else if (collect.finished_at && collect.error) {
        setCollectBox('fail', [_('Ручной запуск завершился ошибкой ({finished}): {error}', {
          finished: formatDateTime(collect.finished_at), error: collect.error,
        })]);
      } else if (collect.finished_at) {
        setCollectBox('ok', [_('Ручной запуск завершён в {finished}. Результат — в таблице ниже.', {
          finished: formatDateTime(collect.finished_at),
        })]);
      } else {
        collectBox.hidden = true;
      }
    }

    async function refresh() {
      clearTimeout(pollTimer);
      let delay = POLL_SLOW;
      try {
        const response = await fetch('/api/status/', { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(await readError(response));
        const data = await response.json();
        clear(cardsBox);
        data.exchanges.forEach((exchange) => cardsBox.appendChild(renderCard(exchange)));
        totalLine.textContent = _('Всего записей в базе: {count}', { count: nfInt.format(data.total_trades) });
        renderRuns(data.runs);
        renderCollect(data.collect);
        const anyRunning = data.collect.running || data.collect.running_elsewhere ||
          data.exchanges.some((e) => e.last_run && e.last_run.status === 'running');
        if (anyRunning) delay = POLL_FAST;
      } catch (error) {
        clear(cardsBox);
        cardsBox.appendChild(el('div', { class: 'panel card-error', text: _('Не удалось загрузить состояние: {error}', { error: error.message }) }));
      }
      pollTimer = setTimeout(refresh, delay);
    }

    function initCollectForm() {
      collectForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const days = parseInt(daysInput.value, 10);
        const min = parseInt(daysInput.min, 10);
        const max = parseInt(daysInput.max, 10);
        if (!(days >= min && days <= max)) {
          lastMessage = { kind: 'fail', text: _('Глубина должна быть от {min} до {max} дней.', { min, max }) };
          setCollectBox('fail', [lastMessage.text]);
          return;
        }
        collectButton.disabled = true;
        lastMessage = null;
        try {
          const response = await fetch('/api/collect/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken(), Accept: 'application/json' },
            body: JSON.stringify({ exchange: document.getElementById('c-exchange').value, days }),
          });
          if (response.status !== 202) {
            lastMessage = { kind: 'fail', text: await readError(response) };
          }
        } catch (error) {
          lastMessage = { kind: 'fail', text: _('Не удалось связаться с сервером') };
        }
        await refresh();
      });

      collectForm.querySelectorAll('.chip').forEach((chip) => {
        chip.addEventListener('click', () => { daysInput.value = chip.dataset.days; });
      });
    }

    if (collectForm) initCollectForm();
    refresh();
  }

  /* ===================================================== страница «Аналитика» */

  function initAnalytics() {
    const PERIOD_KEYS = ['exchange', 'date_from', 'date_to'];
    const MAX_SELECTED = 10;
    const LIST_RENDER_LIMIT = 400;
    const LIMIT_MESSAGE = _('Можно выбрать не больше {max} товаров для сравнения. Снимите отметку с одного из выбранных, чтобы добавить другой.', { max: MAX_SELECTED });
    /* Цвета графиков берутся из CSS-переменных (--chart-*, --series-*) текущей темы
     * и перечитываются при её смене; здесь — запасные значения светлой темы. */
    const COLOR_VARS = {
      accentBar: ['--chart-bar', 'rgba(14, 90, 97, 0.78)'],
      accentBarSoft: ['--chart-bar-soft', 'rgba(14, 90, 97, 0.3)'],
      caspy: ['--chart-line', '#b0681e'],
      caspyBar: ['--chart-bar-alt', 'rgba(176, 104, 30, 0.8)'],
      text: ['--chart-text', '#5b6068'],
      grid: ['--chart-grid', '#ebe9e3'],
      tooltipBg: ['--chart-tooltip-bg', 'rgba(30, 34, 39, 0.92)'],
      tooltipText: ['--chart-tooltip-text', '#ffffff'],
      tooltipBorder: ['--chart-tooltip-border', 'rgba(30, 34, 39, 0)'],
    };
    const COLORS = {};
    /* Цвета линий сравнения: 8 приглушённых оттенков, порядок проверен на различимость
     * (в том числе при нарушениях цветового зрения). 9-й и 10-й товар — те же оттенки пунктиром.
     * Цвет закреплён за товаром, а не за позицией: снятие отметки не перекрашивает остальные.
     * В тёмной теме — те же оттенки, осветлённые для контраста с тёмным фоном. */
    const DEFAULT_SERIES = ['#2a6fb5', '#d0652e', '#1f9472', '#c99a06', '#c75a8a', '#4e8a2a', '#6a58c4', '#c8433f'];
    let SERIES_COLORS = DEFAULT_SERIES.slice();

    function readColors() {
      const style = window.getComputedStyle(document.documentElement);
      const read = (name, fallback) => (style.getPropertyValue(name) || '').trim() || fallback;
      Object.keys(COLOR_VARS).forEach((key) => { COLORS[key] = read(COLOR_VARS[key][0], COLOR_VARS[key][1]); });
      SERIES_COLORS = DEFAULT_SERIES.map((fallback, i) => read('--series-' + (i + 1), fallback));
    }
    readColors();

    /** Общие цвета Chart.js (подписи, сетка, подсказки) — по текущей теме. */
    function applyChartTheme() {
      if (typeof Chart === 'undefined') return;
      Chart.defaults.color = COLORS.text;
      Chart.defaults.borderColor = COLORS.grid;
      const tooltip = Chart.defaults.plugins.tooltip;
      tooltip.backgroundColor = COLORS.tooltipBg;
      tooltip.titleColor = COLORS.tooltipText;
      tooltip.bodyColor = COLORS.tooltipText;
      tooltip.footerColor = COLORS.tooltipText;
      tooltip.borderColor = COLORS.tooltipBorder;
      tooltip.borderWidth = 1;
    }

    const inputs = {};
    PERIOD_KEYS.forEach((key) => { inputs[key] = document.getElementById('a-' + key); });
    const form = document.getElementById('an-filters-form');
    const loader = document.getElementById('an-loader');
    const errorBox = document.getElementById('an-error');
    const errorText = document.getElementById('an-error-text');
    const noteBox = document.getElementById('an-note');
    const summaryBox = document.getElementById('an-summary');
    const chartsBox = document.querySelector('.charts-grid');
    const roleButtons = [...document.querySelectorAll('.chart-switch [data-role]')];
    const metricButtons = [...document.querySelectorAll('.chart-switch [data-metric]')];

    const state = {
      role: 'buyers',
      metric: 'price',
      data: null,
      selected: [],          // id выбранных товаров в порядке выбора
      legacyQ: '',           // старый параметр q из адреса (ключевое слово)
      products: [],          // товары текущей выборки (биржа и период) для списка
      productsLoaded: false,
      productsError: '',
      inList: new Set(),     // id товаров, которые есть в текущей выборке
      known: new Map(),      // id -> {id, name, code, tnved, ...}: названия для чипов
      slots: new Map(),      // id -> номер цвета
      hidden: new Set(),     // товары, скрытые на графике щелчком по легенде
      sort: { key: null, dir: 'desc' },
    };
    const charts = {};
    let requestSeq = 0;
    let abortController = null;
    let productsController = null;

    const nfOne = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });
    const nfTwo = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 });
    const nfPct = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 2 });

    /* ---- форматирование ---- */

    /** Короткая запись денег для осей: 1,2 млрд, 35 млн, 400 тыс. */
    function compactMoney(value) {
      const abs = Math.abs(value);
      if (abs >= 1e9) return _('{n} млрд', { n: nfTwo.format(value / 1e9) });
      if (abs >= 1e6) return _('{n} млн', { n: nfTwo.format(value / 1e6) });
      if (abs >= 1e3) return _('{n} тыс.', { n: nfOne.format(value / 1e3) });
      return nfInt.format(value);
    }

    function fullMoney(value) { return _('{amount} тг', { amount: nfMoney.format(value) }); }

    /** 'ГГГГ-ММ-ДД' -> день и месяц по правилам языка (17.10 / 17/10); withYear — с годом (17.10.26). */
    function shortDate(iso, withYear) {
      const day = isoDay(iso);
      if (!day) return iso;
      return (withYear ? dfDayMonthYear : dfDayMonth).format(day);
    }

    /** Подпись для оси: длинное название обрезается, полное — в подсказке. */
    function truncate(text, max) {
      const value = String(text || '');
      return value.length > max ? value.slice(0, max - 1).trimEnd() + '…' : value;
    }

    function percent(share) {
      const value = share * 100;
      return (value > 0 && value < 0.1 ? '<' + nfOne.format(0.1) : nfOne.format(value)) + '%';
    }

    /** Изменение в процентах со знаком: +12,3 % / −4,5 % (минус — типографский). */
    function signedPercent(value, format) {
      if (!isNumber(value)) return '';
      const text = (format || nfPct).format(Math.abs(value)) + ' %';
      if (value > 0) return '+' + text;
      if (value < 0) return '−' + text;
      return '0 %';
    }

    const RECORDS = 'запись|записи|записей';
    const RECORDS_GEN = 'записи|записей|записей';
    const PRODUCTS = 'товар|товара|товаров';
    const DEALS = 'сделка|сделки|сделок';

    /** Строка для поиска: нижний регистр, ё = е. */
    function normalize(text) {
      return String(text || '').toLowerCase().replace(/ё/g, 'е');
    }

    function productName(id) {
      const p = state.known.get(id);
      return p ? p.name : _('Товар №{id}', { id });
    }

    function rememberProducts(list) {
      (list || []).forEach((p) => {
        const old = state.known.get(p.id) || {};
        const merged = Object.assign({}, old, p);
        merged._hay = normalize([p.name, p.code, p.tnved].join(' '));
        state.known.set(p.id, merged);
      });
    }

    /* ---- цвета товаров ---- */

    /** Каждому выбранному товару — свободный номер цвета; снятым — освобождаем. */
    function assignSlots() {
      for (const id of [...state.slots.keys()]) {
        if (!state.selected.includes(id)) state.slots.delete(id);
      }
      const used = new Set(state.slots.values());
      state.selected.forEach((id) => {
        if (state.slots.has(id)) return;
        let slot = 0;
        while (used.has(slot)) slot += 1;
        state.slots.set(id, slot);
        used.add(slot);
      });
    }

    function styleForSlot(slot) {
      return { color: SERIES_COLORS[slot % SERIES_COLORS.length], dashed: slot >= SERIES_COLORS.length };
    }

    /** Стиль линии товара: у выбранных — закреплённый цвет, у «топ-5» — по порядку. */
    function seriesStyle(id, index) {
      if (state.slots.has(id)) return styleForSlot(state.slots.get(id));
      return styleForSlot(index);
    }

    function swatch(style) {
      return el('span', {
        class: 'swatch' + (style.dashed ? ' swatch-dashed' : ''),
        style: '--swatch:' + style.color,
        'aria-hidden': 'true',
      });
    }

    /* ---- параметры и адресная строка ---- */

    function readUrl() {
      const params = new URLSearchParams(window.location.search);
      PERIOD_KEYS.forEach((key) => {
        const value = params.get(key) || '';
        if (inputs[key].tagName === 'SELECT' && ![...inputs[key].options].some((o) => o.value === value)) return;
        inputs[key].value = value;
      });
      const ids = [];
      const add = (raw) => {
        const value = String(raw || '').trim();
        if (!/^\d+$/.test(value)) return;
        const id = Number(value);
        if (id > 0 && !ids.includes(id)) ids.push(id);
      };
      params.getAll('product').forEach(add);
      params.getAll('products').forEach((chunk) => chunk.split(',').forEach(add));
      // старые ссылки: product_id — один товар для графика цены
      if (!ids.length) add(params.get('product_id'));
      state.selected = ids.slice(0, MAX_SELECTED);
      state.legacyQ = (params.get('q') || '').trim();
      return ids.length > MAX_SELECTED;
    }

    function periodParams() {
      const params = new URLSearchParams();
      PERIOD_KEYS.forEach((key) => {
        const value = (inputs[key].value || '').trim();
        if (value) params.set(key, value);
      });
      return params;
    }

    function filterParams() {
      const params = periodParams();
      if (state.legacyQ) params.set('q', state.legacyQ);
      state.selected.forEach((id) => params.append('product', String(id)));
      return params;
    }

    function syncUrl() {
      const query = filterParams().toString();
      history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
      PERIOD_KEYS.forEach((key) => inputs[key].classList.toggle('has-value', !!(inputs[key].value || '').trim()));
      picker.control.classList.toggle('has-value', state.selected.length > 0 || !!state.legacyQ);
      updateFiltersBadge(form);
    }

    /* ================================================ выбор товаров (как в 1С) */

    const picker = {
      root: document.getElementById('a-products'),
      control: document.querySelector('#a-products .combo-control'),
      chips: document.getElementById('a-products-chips'),
      input: document.getElementById('a-products-input'),
      clearBtn: document.getElementById('a-products-clear'),
      arrowBtn: document.getElementById('a-products-arrow'),
      popup: document.getElementById('a-products-popup'),
      allBtn: document.getElementById('a-products-all'),
      foundBtn: document.getElementById('a-products-found'),
      count: document.getElementById('a-products-count'),
      msg: document.getElementById('a-products-msg'),
      list: document.getElementById('a-products-list'),
      note: document.getElementById('a-products-note'),
      flash: document.getElementById('a-products-flash'),
      open: false,
      options: [],        // товары в порядке строк списка (для клавиатуры)
      found: [],          // найденные по тексту товары выборки
      active: -1,
      keyboardNav: false, // пробел отмечает строку только после навигации стрелками
      chipsExpanded: false,
      msgTimer: null,
      flashTimer: null,
      silentFocus: false,
    };

    function isMissing(id) {
      return state.productsLoaded && !state.inList.has(id);
    }

    function showPickerMessage(text) {
      clearTimeout(picker.msgTimer);
      picker.msg.textContent = text;
      picker.msg.hidden = !text;
      if (text) picker.msgTimer = setTimeout(() => { picker.msg.hidden = true; }, 6000);
    }

    /** Сообщение о выборе: в открытом списке — над строками, иначе — под полем. */
    function notify(text) {
      if (picker.open) { showPickerMessage(text); return; }
      clearTimeout(picker.flashTimer);
      picker.flash.textContent = text;
      picker.flash.hidden = !text;
      if (text) picker.flashTimer = setTimeout(() => { picker.flash.hidden = true; }, 6000);
    }

    function queryWords() {
      return normalize(picker.input.value).split(/\s+/).filter(Boolean);
    }

    function matches(product, words) {
      if (!words.length) return true;
      const hay = product._hay || normalize([product.name, product.code, product.tnved].join(' '));
      return words.every((w) => hay.includes(w));
    }

    function optionMeta(p) {
      const meta = el('span', { class: 'combo-meta' });
      (p.exchange_codes || []).forEach((code) => meta.appendChild(exchangeBadge(code, exchangeName(code))));
      const codes = [];
      if (p.code) codes.push(_('код {code}', { code: p.code }));
      if (p.tnved) codes.push(_('ТН ВЭД {code}', { code: p.tnved }));
      if (codes.length) meta.appendChild(el('span', { class: 'combo-codes', text: codes.join(' · ') }));
      return meta;
    }

    function renderOption(p, index, missing) {
      const selected = state.selected.includes(p.id);
      const deals = missing
        ? el('span', { class: 'combo-deals is-missing', text: _('нет сделок') })
        : el('span', { class: 'combo-deals', text: nfInt.format(p.deals) + ' ' + plural(p.deals, DEALS) });
      return el('li', {
        id: 'a-opt-' + p.id,
        role: 'option',
        class: 'combo-option' + (selected ? ' is-selected' : '') + (missing ? ' is-missing' : ''),
        'aria-selected': selected ? 'true' : 'false',
        data: { id: String(p.id), index: String(index) },
      }, [
        el('span', { class: 'combo-check', 'aria-hidden': 'true' }),
        el('span', { class: 'combo-text' }, [
          el('span', { class: 'combo-name', title: p.name, text: p.name }),
          optionMeta(p),
        ]),
        deals,
      ]);
    }

    function renderList() {
      const words = queryWords();
      clear(picker.list);
      picker.options = [];
      const missing = state.productsLoaded
        ? state.selected.filter((id) => !state.inList.has(id))
            .map((id) => state.known.get(id) || { id, name: productName(id) })
            .filter((p) => matches(p, words))
        : [];
      picker.found = state.products.filter((p) => matches(p, words));

      if (missing.length) {
        picker.list.appendChild(el('li', { role: 'presentation', class: 'combo-group', text: _('Выбраны, но нет сделок в выбранной бирже и периоде') }));
        missing.forEach((p) => {
          picker.list.appendChild(renderOption(p, picker.options.length, true));
          picker.options.push(p);
        });
        if (picker.found.length) {
          picker.list.appendChild(el('li', { role: 'presentation', class: 'combo-group', text: _('Товары выборки') }));
        }
      }
      picker.found.slice(0, LIST_RENDER_LIMIT).forEach((p) => {
        picker.list.appendChild(renderOption(p, picker.options.length, false));
        picker.options.push(p);
      });
      if (picker.found.length > LIST_RENDER_LIMIT) {
        picker.list.appendChild(el('li', {
          role: 'presentation', class: 'combo-empty',
          text: _('Показаны первые {shown} из {total} — уточните поиск', {
            shown: nfInt.format(LIST_RENDER_LIMIT), total: nfInt.format(picker.found.length),
          }),
        }));
      }
      if (!picker.options.length) {
        let text = _('Ничего не найдено — попробуйте часть слова или код ТН ВЭД');
        if (state.productsError) text = state.productsError;
        else if (!state.productsLoaded) text = _('Загрузка списка товаров…');
        else if (!words.length) text = _('В выбранной бирже и периоде нет сделок');
        picker.list.appendChild(el('li', { role: 'presentation', class: 'combo-empty', text }));
      }
      picker.active = Math.min(picker.active, picker.options.length - 1);
      updatePickerActions();
      setActive(picker.active, false);
    }

    function updatePickerActions() {
      const words = queryWords();
      const total = state.products.length;
      const n = state.selected.length;
      let text = '';
      if (state.productsLoaded) {
        text = words.length
          ? _('Найдено: {found} из {total}', { found: nfInt.format(picker.found.length), total: nfInt.format(total) })
          : _('{count} {products} в выборке', { count: nfInt.format(total), products: plural(total, PRODUCTS) });
      }
      if (n) text += (text ? ' · ' : '') + _('выбрано {n} из {max}', { n, max: MAX_SELECTED });
      picker.count.textContent = text;
      picker.allBtn.setAttribute('aria-pressed', n === 0 && !state.legacyQ ? 'true' : 'false');
      picker.allBtn.classList.toggle('is-active', n === 0 && !state.legacyQ);
      const addable = picker.found.filter((p) => !state.selected.includes(p.id)).length;
      picker.foundBtn.hidden = !words.length;
      picker.foundBtn.disabled = addable === 0;
      picker.foundBtn.textContent = _('Выбрать найденные') + (addable ? ' (' + nfInt.format(addable) + ')' : '');
    }

    /** Отметки в уже нарисованном списке (без перерисовки — не сбивает прокрутку). */
    function updateOptionStates() {
      picker.list.querySelectorAll('[role="option"]').forEach((li) => {
        const on = state.selected.includes(Number(li.dataset.id));
        li.classList.toggle('is-selected', on);
        li.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      updatePickerActions();
    }

    function setActive(index, scroll) {
      picker.list.querySelectorAll('.combo-option.is-active').forEach((li) => li.classList.remove('is-active'));
      picker.active = index;
      if (index < 0 || !picker.options[index]) {
        picker.input.removeAttribute('aria-activedescendant');
        return;
      }
      const li = document.getElementById('a-opt-' + picker.options[index].id);
      if (!li) return;
      li.classList.add('is-active');
      picker.input.setAttribute('aria-activedescendant', li.id);
      if (scroll !== false) li.scrollIntoView({ block: 'nearest' });
    }

    function moveActive(delta) {
      const n = picker.options.length;
      if (!n) return;
      let next = picker.active < 0 ? (delta > 0 ? 0 : n - 1) : picker.active + delta;
      next = Math.max(0, Math.min(n - 1, next));
      setActive(next);
    }

    function openPicker() {
      if (picker.open) return;
      picker.open = true;
      picker.popup.hidden = false;
      picker.root.classList.add('is-open');
      picker.input.setAttribute('aria-expanded', 'true');
      picker.arrowBtn.setAttribute('aria-label', _('Закрыть список товаров'));
      picker.active = -1;
      renderList();
      picker.list.scrollTop = 0;
    }

    function closePicker() {
      if (!picker.open) return;
      picker.open = false;
      picker.popup.hidden = true;
      picker.root.classList.remove('is-open');
      picker.input.setAttribute('aria-expanded', 'false');
      picker.input.removeAttribute('aria-activedescendant');
      picker.arrowBtn.setAttribute('aria-label', _('Открыть список товаров'));
      picker.input.value = '';
      picker.active = -1;
      picker.keyboardNav = false;
      showPickerMessage('');
    }

    function focusInput() {
      picker.silentFocus = true;
      picker.input.focus();
      picker.silentFocus = false;
    }

    function renderChips() {
      clear(picker.chips);
      const ids = state.selected;
      const collapseAt = narrowScreen.matches ? 2 : 3;
      const collapsed = ids.length > collapseAt && !picker.chipsExpanded;
      if (state.legacyQ) {
        picker.chips.appendChild(el('span', { class: 'combo-chip combo-chip-word', role: 'listitem', title: _('Старый фильтр по ключевому слову в названии товара') }, [
          el('span', { class: 'combo-chip-text', text: _('Слово: «{word}»', { word: state.legacyQ }) }),
          el('button', { type: 'button', class: 'combo-chip-remove', 'aria-label': _('Убрать фильтр по слову {word}', { word: state.legacyQ }), data: { remove: 'q' } }, ['×']),
        ]));
      }
      if (collapsed) {
        const missing = ids.filter(isMissing).length;
        picker.chips.appendChild(el('button', {
          type: 'button', class: 'combo-chip combo-chip-toggle', 'aria-expanded': 'false', data: { toggle: '1' },
          title: ids.map(productName).join('\n'),
        }, [
          _('Выбрано: {n}', { n: ids.length }) + (missing ? ' ' + _('(без сделок: {n})', { n: missing }) : ''),
          el('span', { class: 'combo-chip-chevron', 'aria-hidden': 'true' }),
        ]));
      } else {
        ids.forEach((id) => {
          const missing = isMissing(id);
          const name = productName(id);
          picker.chips.appendChild(el('span', {
            class: 'combo-chip' + (missing ? ' is-missing' : ''), role: 'listitem',
            title: name + (missing ? '\n' + _('Нет сделок в выбранной бирже и периоде') : ''),
          }, [
            swatch(styleForSlot(state.slots.get(id) || 0)),
            el('span', { class: 'combo-chip-text', text: name }),
            el('button', { type: 'button', class: 'combo-chip-remove', 'aria-label': _('Убрать: {name}', { name }), data: { remove: String(id) } }, ['×']),
          ]));
        });
        if (ids.length > collapseAt) {
          picker.chips.appendChild(el('button', {
            type: 'button', class: 'combo-chip combo-chip-toggle', 'aria-expanded': 'true', data: { toggle: '1' },
          }, [_('Свернуть'), el('span', { class: 'combo-chip-chevron is-up', 'aria-hidden': 'true' })]));
        }
      }
      picker.chips.hidden = !picker.chips.firstChild;
      const any = ids.length > 0 || !!state.legacyQ;
      picker.input.placeholder = any
        ? (ids.length >= MAX_SELECTED ? _('Выбрано {n} — максимум', { n: MAX_SELECTED }) : _('Добавить товар…'))
        : _('Все товары');
      picker.clearBtn.hidden = !any;
      renderMissingNote();
    }

    function renderMissingNote() {
      const missing = state.selected.filter(isMissing);
      clear(picker.note);
      picker.note.hidden = !missing.length;
      if (!missing.length) return;
      const n = missing.length;
      let text;
      if (n === state.selected.length && n > 1) text = _('У всех выбранных товаров нет сделок в выбранной бирже и периоде — они отмечены серым.');
      else if (n === 1) text = _('У 1 выбранного товара нет сделок в выбранной бирже и периоде — он отмечен серым.');
      else text = _('У {n} выбранных товаров нет сделок в выбранной бирже и периоде — они отмечены серым.', { n });
      picker.note.appendChild(el('span', { text }));
      picker.note.appendChild(el('button', { type: 'button', class: 'link-btn', data: { dropMissing: '1' } }, [n === 1 ? _('Убрать его') : _('Убрать их')]));
    }

    const loadDebounced = debounce(load, 300);

    function selectionChanged() {
      assignSlots();
      renderChips();
      updateOptionStates();
      syncUrl();
      loadDebounced();
    }

    function toggleProduct(id) {
      const index = state.selected.indexOf(id);
      if (index >= 0) {
        state.selected.splice(index, 1);
        showPickerMessage('');
      } else {
        if (state.selected.length >= MAX_SELECTED) {
          showPickerMessage(LIMIT_MESSAGE);
          return;
        }
        state.selected.push(id);
      }
      selectionChanged();
    }

    function removeProduct(id) {
      state.selected = state.selected.filter((x) => x !== id);
      state.hidden.delete(id);
      if (state.selected.length <= (narrowScreen.matches ? 2 : 3)) picker.chipsExpanded = false;
      selectionChanged();
    }

    function selectAll() {
      // «Все товары»: сбросить выбор (и старое ключевое слово)
      const hadQ = !!state.legacyQ;
      state.selected = [];
      state.legacyQ = '';
      state.hidden.clear();
      picker.chipsExpanded = false;
      showPickerMessage('');
      if (hadQ) loadProducts();
      selectionChanged();
    }

    function removeLegacyQ() {
      state.legacyQ = '';
      loadProducts();
      selectionChanged();
    }

    function selectFound() {
      const toAdd = picker.found.filter((p) => !state.selected.includes(p.id));
      const room = MAX_SELECTED - state.selected.length;
      if (room <= 0) {
        showPickerMessage(LIMIT_MESSAGE);
        return;
      }
      toAdd.slice(0, room).forEach((p) => state.selected.push(p.id));
      if (toAdd.length > room) {
        showPickerMessage(_('Добавлено {added} из {found} найденных: для сравнения можно выбрать не больше {max} товаров (взяты самые торгуемые).', {
          added: room, found: toAdd.length, max: MAX_SELECTED,
        }));
      } else {
        showPickerMessage('');
      }
      selectionChanged();
    }

    /* события выбора товаров */
    picker.input.addEventListener('focus', () => { if (!picker.silentFocus) openPicker(); });
    picker.input.addEventListener('input', () => {
      picker.keyboardNav = false;
      openPicker();
      picker.active = queryWords().length ? 0 : -1;
      renderList();
      picker.list.scrollTop = 0;
    });
    picker.input.addEventListener('keydown', (event) => {
      const key = event.key;
      if (key === 'ArrowDown' || key === 'ArrowUp') {
        event.preventDefault();
        if (event.altKey && key === 'ArrowUp') { closePicker(); return; }
        if (!picker.open) { openPicker(); if (key === 'ArrowDown') moveActive(1); }
        else moveActive(key === 'ArrowDown' ? 1 : -1);
        picker.keyboardNav = true;
      } else if ((key === 'PageDown' || key === 'PageUp') && picker.open) {
        event.preventDefault();
        moveActive(key === 'PageDown' ? 10 : -10);
        picker.keyboardNav = true;
      } else if (key === 'Enter') {
        event.preventDefault();
        if (!picker.open) { openPicker(); return; }
        if (picker.active < 0 && picker.options.length) setActive(0);
        if (picker.active >= 0) toggleProduct(picker.options[picker.active].id);
      } else if (key === ' ' && picker.open && picker.keyboardNav && picker.active >= 0) {
        event.preventDefault();
        toggleProduct(picker.options[picker.active].id);
      } else if (key === 'Escape') {
        if (picker.open) { event.preventDefault(); closePicker(); }
        else if (picker.input.value) { event.preventDefault(); picker.input.value = ''; }
      } else if (key === 'Backspace' && !picker.input.value) {
        if (state.selected.length) {
          event.preventDefault();
          const last = state.selected[state.selected.length - 1];
          notify(_('Убрано: {name}', { name: truncate(productName(last), 80) }));
          removeProduct(last);
        } else if (state.legacyQ) {
          event.preventDefault();
          removeLegacyQ();
        }
      } else if (key === 'Tab') {
        closePicker();
      }
    });
    picker.control.addEventListener('mousedown', (event) => {
      // щелчок по пустому месту поля — фокус в поле ввода и открытие списка
      if (event.target.closest('button') || event.target === picker.input) return;
      event.preventDefault();
      focusInput();
      openPicker();
    });
    picker.arrowBtn.addEventListener('mousedown', (event) => event.preventDefault());
    picker.arrowBtn.addEventListener('click', () => {
      if (picker.open) closePicker();
      else { focusInput(); openPicker(); }
    });
    picker.clearBtn.addEventListener('click', () => { selectAll(); focusInput(); });
    picker.chips.addEventListener('click', (event) => {
      const remove = event.target.closest('[data-remove]');
      if (remove) {
        if (remove.dataset.remove === 'q') removeLegacyQ();
        else removeProduct(Number(remove.dataset.remove));
        focusInput();
        return;
      }
      const toggle = event.target.closest('[data-toggle]');
      if (toggle) {
        picker.chipsExpanded = !picker.chipsExpanded;
        renderChips();
        const again = picker.chips.querySelector('[data-toggle]');
        if (again) again.focus();
      }
    });
    picker.note.addEventListener('click', (event) => {
      if (!event.target.closest('[data-drop-missing]')) return;
      state.selected = state.selected.filter((id) => !isMissing(id));
      selectionChanged();
    });
    picker.list.addEventListener('mousedown', (event) => event.preventDefault());
    picker.list.addEventListener('click', (event) => {
      const li = event.target.closest('[role="option"]');
      if (!li) return;
      picker.keyboardNav = false;
      setActive(Number(li.dataset.index), false);
      toggleProduct(Number(li.dataset.id));
    });
    picker.allBtn.addEventListener('click', () => { selectAll(); focusInput(); });
    picker.foundBtn.addEventListener('click', () => { selectFound(); focusInput(); });
    document.addEventListener('pointerdown', (event) => {
      if (picker.open && !picker.root.contains(event.target)) closePicker();
    });
    picker.root.addEventListener('focusout', (event) => {
      if (picker.open && event.relatedTarget && !picker.root.contains(event.relatedTarget)) closePicker();
    });

    /* ---- список товаров выборки ---- */

    async function loadProducts() {
      if (productsController) productsController.abort();
      productsController = new AbortController();
      const params = periodParams();
      if (state.legacyQ) params.set('q', state.legacyQ);
      state.productsLoaded = false;
      state.productsError = '';
      if (picker.open) renderList();
      try {
        const response = await fetch('/api/analytics/products/?' + params.toString(), {
          signal: productsController.signal, headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(await readError(response));
        const data = await response.json();
        rememberProducts(data.results);
        state.products = data.results.map((p) => state.known.get(p.id));
        state.inList = new Set(data.results.map((p) => p.id));
        state.productsLoaded = true;
      } catch (error) {
        if (error.name === 'AbortError') return;
        state.products = [];
        state.inList = new Set();
        state.productsError = _('Не удалось загрузить список товаров: {error}', { error: error.message || _('нет связи с сервером') });
      }
      renderChips();
      if (picker.open) renderList();
    }

    /* ---- графики ---- */

    function baseOptions() {
      return {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        interaction: { mode: 'index', intersect: false },
      };
    }

    function ensureChart(name, config) {
      if (!charts[name]) charts[name] = new Chart(document.getElementById('chart-' + name), config);
      return charts[name];
    }

    function resetChart(name) {
      const chart = charts[name];
      if (!chart) return;
      chart.data.labels = [];
      chart.data.datasets.forEach((d) => { d.data = []; });
      chart.update();
    }

    function setEmpty(name, message) {
      const box = document.getElementById('empty-' + name);
      box.hidden = !message;
      box.textContent = message || '';
      document.getElementById('chart-' + name).style.visibility = message ? 'hidden' : 'visible';
    }

    function valueAxis(extra) {
      return Object.assign({
        beginAtZero: true,
        grid: { color: COLORS.grid },
        ticks: { callback: (v) => compactMoney(v), maxRotation: 0, maxTicksLimit: 7 },
      }, extra || {});
    }

    /** Короткие подписи товаров для легенды и подсказок; одинаковые — с кодом или номером. */
    function shortNames(series) {
      const limit = narrowScreen.matches ? 30 : 42;
      const names = series.map((s) => truncate(s.name, limit));
      const counts = {};
      names.forEach((n) => { counts[n] = (counts[n] || 0) + 1; });
      return names.map((n, i) => (counts[n] > 1 ? truncate(series[i].name, limit - 8) + ' (' + (series[i].code || '№' + series[i].id) + ')' : n));
    }

    const METRIC_TEXT = {
      price: _('средневзвешенная цена по дням, тг за единицу товара'),
      change: _('изменение средневзвешенной цены к первому дню с ценой, %'),
      amount: _('сумма сделок по дням, тг'),
    };

    function seriesValues(s, labels) {
      const byDate = new Map(s.points.map((p) => [p.date, p]));
      if (state.metric === 'amount') {
        return labels.map((d) => { const p = byDate.get(d); return p && isNumber(p.amount) ? p.amount : null; });
      }
      const prices = labels.map((d) => { const p = byDate.get(d); return p && isNumber(p.avg_price) ? p.avg_price : null; });
      if (state.metric === 'price') return prices;
      const base = prices.find((v) => v !== null && v > 0);
      return prices.map((v) => (v === null || !base ? null : Math.round((v / base - 1) * 10000) / 100));
    }

    function formatMetric(value) {
      if (state.metric === 'amount') return fullMoney(value);
      if (state.metric === 'change') return signedPercent(value);
      return fullMoney(value);
    }

    function renderCompareChart(data) {
      const series = data.series;
      const top = data.compare_mode === 'top';
      const sub = document.getElementById('price-sub');
      const hint = document.getElementById('price-hint');
      if (top) {
        sub.textContent = series.length
          ? _('Показаны {n} {products} — выберите товары для сравнения', {
            n: series.length,
            products: plural(series.length, 'самый торгуемый товар|самых торгуемых товара|самых торгуемых товаров'),
          })
          : _('Нет сделок в выбранной бирже и периоде');
      } else {
        sub.textContent = _('Выбрано: {n} {products}', { n: series.length, products: plural(series.length, PRODUCTS) }) +
          ' · ' + METRIC_TEXT[state.metric];
      }
      if (top && series.length) sub.textContent += ' · ' + METRIC_TEXT[state.metric];
      hint.textContent = state.metric === 'price' && series.length > 1
        ? _('Цены разных товаров могут быть в разных единицах (тонна, кубометр, литр) — для сопоставления динамики включите «Изменение, %».')
        : (state.metric === 'amount' && series.length > 1 ? _('Столбцы товаров за один день сложены.') : '');
      hint.hidden = !hint.textContent;

      const labelSet = new Set();
      series.forEach((s) => s.points.forEach((p) => labelSet.add(p.date)));
      const labels = [...labelSet].sort();
      const names = shortNames(series);
      const isBar = state.metric === 'amount';
      // период длиннее года: в подписях оси нужен и год (дд.мм.гг)
      const manyYears = labels.length > 1 && labels[0].slice(0, 4) !== labels[labels.length - 1].slice(0, 4);
      const dense = labels.length > 60;
      const datasets = series.map((s, i) => {
        const style = seriesStyle(s.id, i);
        const values = seriesValues(s, labels);
        const base = {
          label: names[i], data: values, $id: s.id, $name: s.name,
          borderColor: style.color, backgroundColor: style.color,
          hidden: state.hidden.has(s.id),
        };
        if (isBar) return Object.assign(base, { type: 'bar', stack: 'sum', borderWidth: 0, borderRadius: 2, maxBarThickness: 28 });
        return Object.assign(base, {
          type: 'line', borderWidth: 2, borderDash: style.dashed ? [6, 4] : [], tension: 0.2, spanGaps: true,
          pointRadius: dense ? 0 : 2.5, pointHoverRadius: 4, pointHitRadius: 8, pointBackgroundColor: style.color,
          backgroundColor: style.color, fill: false,
        });
      });
      renderLegend(series, names);

      const hasValues = datasets.some((d) => d.data.some((v) => v !== null));
      if (!series.length || !hasValues) {
        let message;
        if (!series.length) message = _('Нет сделок в выбранной бирже и периоде');
        else if (state.metric === 'amount') message = _('У выбранных товаров нет сумм сделок за выбранный период');
        else message = series.length === 1 ? _('У этого товара нет цен за выбранный период') : _('У выбранных товаров нет цен за выбранный период');
        setEmpty('price', message);
        if (charts.price) { charts.price.destroy(); delete charts.price; }
        return;
      }
      setEmpty('price', '');
      if (charts.price) { charts.price.destroy(); delete charts.price; }
      const yTicks = {
        callback: (v) => (state.metric === 'change' ? signedPercent(v, nfInt) : compactMoney(v)),
        maxTicksLimit: 7,
      };
      const chart = ensureChart('price', {
        type: isBar ? 'bar' : 'line',
        data: { labels: labels.map((d) => shortDate(d, manyYears)), datasets },
        options: Object.assign(baseOptions(), {
          scales: {
            x: {
              stacked: isBar, grid: { display: false },
              ticks: { maxRotation: 0, autoSkip: true, autoSkipPadding: 14, maxTicksLimit: narrowScreen.matches ? 5 : 14 },
            },
            y: { stacked: isBar, beginAtZero: state.metric !== 'price', grid: { color: COLORS.grid }, ticks: yTicks },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              padding: 10,
              filter: (item) => item.parsed.y !== null && item.parsed.y !== undefined,
              itemSort: (a, b) => b.parsed.y - a.parsed.y,
              callbacks: {
                title: (items) => formatDate(items[0].chart.$dates[items[0].dataIndex]),
                label: (item) => truncate(item.dataset.label, 34) + ': ' + formatMetric(item.parsed.y),
              },
            },
          },
        }),
      });
      chart.$dates = labels;
    }

    function renderLegend(series, names) {
      const legend = document.getElementById('price-legend');
      clear(legend);
      legend.hidden = !series.length;
      series.forEach((s, i) => {
        const visible = !state.hidden.has(s.id);
        const noData = !s.points.some((p) => isNumber(state.metric === 'amount' ? p.amount : p.avg_price));
        legend.appendChild(el('li', {}, [
          el('button', {
            type: 'button', class: 'legend-item' + (visible ? '' : ' is-off'), 'aria-pressed': visible ? 'true' : 'false',
            title: s.name + (noData ? '\n' + _('Нет данных за выбранный период') : '') + '\n' + _('Щелчок — скрыть или показать на графике'),
            data: { id: String(s.id) },
          }, [
            swatch(seriesStyle(s.id, i)),
            el('span', { class: 'legend-text', text: names[i] }),
            noData ? el('span', { class: 'legend-note', text: _('нет данных') }) : null,
          ]),
        ]));
      });
    }

    document.getElementById('price-legend').addEventListener('click', (event) => {
      const button = event.target.closest('.legend-item');
      if (!button) return;
      const id = Number(button.dataset.id);
      if (state.hidden.has(id)) state.hidden.delete(id);
      else state.hidden.add(id);
      const visible = !state.hidden.has(id);
      button.classList.toggle('is-off', !visible);
      button.setAttribute('aria-pressed', visible ? 'true' : 'false');
      const chart = charts.price;
      if (chart) {
        const index = chart.data.datasets.findIndex((d) => d.$id === id);
        if (index >= 0) { chart.setDatasetVisibility(index, visible); chart.update(); }
      }
    });

    /* ---- таблица сравнения ---- */

    const COMPARE_COLUMNS = [
      { key: 'name', label: _('Товар'), text: true },
      { key: 'exchange', label: _('Биржа'), text: true },
      { key: 'deals', label: _('Сделок') },
      { key: 'volume', label: _('Объём'), optional: true },
      { key: 'amount', label: _('Сумма, тг') },
      { key: 'avg_price', label: _('Ср. взв. цена') },
      { key: 'min_price', label: _('Мин. цена') },
      { key: 'max_price', label: _('Макс. цена') },
      { key: 'first_price', label: _('Первая') },
      { key: 'last_price', label: _('Последняя') },
      { key: 'change_pct', label: _('Изменение') },
      { key: 'last_date', label: _('Посл. сделка'), text: true },
    ];

    function sortValue(row, key) {
      if (key === 'exchange') return (row.exchange_codes || []).join(',');
      if (key === 'name') return normalize(row.name);
      return row[key];
    }

    function sortedRows(rows) {
      const { key, dir } = state.sort;
      // без сортировки — порядок выбора, товары без сделок в выборке — в конце
      if (!key) return rows.filter((r) => r.records).concat(rows.filter((r) => !r.records));
      const sign = dir === 'asc' ? 1 : -1;
      return rows.map((r, i) => [r, i]).sort((a, b) => {
        const va = sortValue(a[0], key);
        const vb = sortValue(b[0], key);
        const ea = va === null || va === undefined || va === '';
        const eb = vb === null || vb === undefined || vb === '';
        if (ea || eb) return ea === eb ? a[1] - b[1] : (ea ? 1 : -1); // пустые — всегда в конце
        if (va < vb) return -sign;
        if (va > vb) return sign;
        return a[1] - b[1];
      }).map((pair) => pair[0]);
    }

    function numCell(value, format, extra) {
      return el('td', { class: 'num', title: extra && extra.title }, [
        isNumber(value) ? el('span', { class: 'cell-main', text: format(value) }) : dash(),
        extra && extra.sub ? el('div', { class: 'cell-sub', text: extra.sub }) : null,
      ]);
    }

    function renderCompareTable(data) {
      const rows = data.comparison;
      const wrap = document.getElementById('compare-wrap');
      const head = document.getElementById('compare-head');
      const body = document.getElementById('compare-body');
      wrap.hidden = !rows.length;
      clear(head);
      clear(body);
      if (!rows.length) return;
      const hasVolume = rows.some((r) => isNumber(r.volume));
      const columns = COMPARE_COLUMNS.filter((c) => !c.optional || hasVolume);
      columns.forEach((c) => {
        const active = state.sort.key === c.key;
        const th = el('th', {
          scope: 'col', class: c.text ? '' : 'num',
          'aria-sort': active ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none',
        }, [el('button', { type: 'button', class: 'th-sort', data: { sort: c.key } }, [
          c.label, el('span', { class: 'th-arrow', 'aria-hidden': 'true', text: active ? (state.sort.dir === 'asc' ? '▲' : '▼') : '' }),
        ])]);
        head.appendChild(th);
      });
      const order = new Map(data.series.map((s, i) => [s.id, i]));
      sortedRows(rows).forEach((r) => {
        const style = seriesStyle(r.id, order.has(r.id) ? order.get(r.id) : 0);
        const codes = [];
        if (r.code) codes.push(_('код {code}', { code: r.code }));
        if (r.tnved) codes.push(_('ТН ВЭД {code}', { code: r.tnved }));
        const empty = !r.records;
        const cells = [
          el('td', { class: 'cell-compare-product' }, [
            el('div', { class: 'compare-name' }, [
              swatch(style),
              el('span', { class: 'cell-main', title: r.name, text: r.name }),
            ]),
            el('div', { class: 'cell-sub', text: empty ? _('нет сделок в выбранной бирже и периоде') : codes.join(' · ') }),
          ]),
          el('td', {}, (r.exchange_codes || []).length
            ? r.exchange_codes.map((code) => exchangeBadge(code, exchangeName(code)))
            : [dash()]),
          numCell(r.deals, (v) => nfInt.format(v)),
        ];
        if (hasVolume) cells.push(numCell(r.volume, (v) => nfQty.format(v)));
        cells.push(
          numCell(r.amount, (v) => compactMoney(v), { title: isNumber(r.amount) ? fullMoney(r.amount) : '' }),
          numCell(r.avg_price, (v) => nfMoney.format(v)),
          numCell(r.min_price, (v) => nfMoney.format(v)),
          numCell(r.max_price, (v) => nfMoney.format(v)),
          numCell(r.first_price, (v) => nfMoney.format(v), { sub: r.first_date ? formatDate(r.first_date) : '' }),
          numCell(r.last_price, (v) => nfMoney.format(v), { sub: r.last_price_date ? formatDate(r.last_price_date) : '' }),
        );
        const change = r.change_pct;
        cells.push(el('td', {
          class: 'num',
          title: isNumber(change) ? _('Последняя цена к первой за период') : _('Нужно хотя бы два дня с ценой'),
        }, [
          isNumber(change)
            ? el('span', { class: 'change ' + (change > 0 ? 'change-up' : (change < 0 ? 'change-down' : '')), text: signedPercent(change) })
            : dash(),
        ]));
        cells.push(el('td', { class: 'cell-date' }, [r.last_date ? formatDate(r.last_date) : dash()]));
        body.appendChild(el('tr', { class: empty ? 'is-empty' : '' }, cells));
      });
    }

    document.getElementById('compare-head').addEventListener('click', (event) => {
      const button = event.target.closest('[data-sort]');
      if (!button) return;
      const key = button.dataset.sort;
      const column = COMPARE_COLUMNS.find((c) => c.key === key);
      if (state.sort.key === key) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
      else state.sort = { key, dir: column && column.text && key !== 'last_date' ? 'asc' : 'desc' };
      if (state.data) renderCompareTable(state.data);
      const again = document.querySelector('#compare-head [data-sort="' + key + '"]');
      if (again) again.focus();
    });

    function renderTurnover(days) {
      if (!days.length) {
        setEmpty('turnover', _('Нет данных за выбранный период'));
        resetChart('turnover');
        return;
      }
      setEmpty('turnover', '');
      const chart = ensureChart('turnover', {
        data: { labels: [], datasets: [
          { type: 'bar', label: _('Сумма сделок'), data: [], yAxisID: 'y', backgroundColor: COLORS.accentBar, borderRadius: 3, order: 2 },
          { type: 'line', label: _('Число сделок'), data: [], yAxisID: 'y1', borderColor: COLORS.caspy, backgroundColor: COLORS.caspy,
            borderWidth: 2, tension: 0.2, pointRadius: 2.5, order: 1 },
        ] },
        options: Object.assign(baseOptions(), {
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
            y: valueAxis({ position: 'left' }),
            y1: {
              position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
              ticks: { callback: (v) => nfInt.format(v) },
            },
          },
          plugins: {
            legend: { display: true, position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } },
            tooltip: {
              padding: 10,
              callbacks: {
                title: (items) => formatDate(items[0].chart.$days[items[0].dataIndex].date),
                label: (item) => item.dataset.label + ': ' +
                  (item.datasetIndex === 0 ? fullMoney(item.parsed.y) : nfInt.format(item.parsed.y)),
                afterBody: (items) => {
                  const d = items[0].chart.$days[items[0].dataIndex];
                  return d.records_without_amount
                    ? _('Без суммы: {n} {records}', { n: nfInt.format(d.records_without_amount), records: plural(d.records_without_amount, RECORDS) })
                    : '';
                },
              },
            },
          },
        }),
      });
      chart.$days = days;
      chart.data.labels = days.map((d) => shortDate(d.date));
      chart.data.datasets[0].data = days.map((d) => d.amount);
      // если число сделок биржа не публикует, показываем число записей
      chart.data.datasets[1].data = days.map((d) => (d.deals > 0 ? d.deals : d.records));
      chart.update();
    }

    // названия месяцев — из словаря языка (12 названий через «|»), а не из Intl:
    // так подписи одинаковы во всех браузерах, в том числе без данных локали kk
    const MONTHS_SHORT = _('янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек').split('|');
    const MONTHS_FULL = _('январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь').split('|');

    /** 'ГГГГ-ММ' -> 'окт 2026' (или 'октябрь 2026'). */
    function monthLabel(value, full) {
      const m = /^(\d{4})-(\d{2})/.exec(value || '');
      if (!m) return value;
      const names = full ? MONTHS_FULL : MONTHS_SHORT;
      return (names[Number(m[2]) - 1] || m[2]) + ' ' + m[1];
    }

    /** Какие дни неполного месяца попали в период: «с 14.07» / «по 17.10» / «с 14.07 по 17.10»; полный месяц — ''. */
    function partialRange(month) {
      const from = shortDate(month.period_from);
      const to = shortDate(month.period_to);
      if (month.partial_start && month.partial_end) return _('с {from} по {to}', { from, to });
      if (month.partial_start) return _('с {date}', { date: from });
      if (month.partial_end) return _('по {date}', { date: to });
      return '';
    }

    /** «месяц неполный: данные с 14.07» / «… по 17.10» / «… с 14.07 по 17.10»; полный месяц — ''. */
    function partialText(month) {
      const range = partialRange(month);
      return range ? _('месяц неполный: данные {range}', { range }) : '';
    }

    function renderMonths(months) {
      const hint = document.getElementById('months-hint');
      if (!months.length) {
        setEmpty('months', _('Нет данных за выбранный период'));
        resetChart('months');
        hint.hidden = true;
        return;
      }
      setEmpty('months', '');
      const chart = ensureChart('months', {
        data: { labels: [], datasets: [
          { type: 'bar', label: _('Сумма сделок'), data: [], yAxisID: 'y', backgroundColor: COLORS.accentBar, borderRadius: 3,
            maxBarThickness: 56, order: 2 },
          { type: 'line', label: _('Число сделок'), data: [], yAxisID: 'y1', borderColor: COLORS.caspy, backgroundColor: COLORS.caspy,
            borderWidth: 2, tension: 0.2, pointRadius: 3, order: 1 },
        ] },
        options: Object.assign(baseOptions(), {
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: narrowScreen.matches ? 5 : 12 } },
            y: valueAxis({ position: 'left' }),
            y1: {
              position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
              ticks: { callback: (v) => nfInt.format(v) },
            },
          },
          plugins: {
            legend: { display: true, position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } },
            tooltip: {
              padding: 10,
              callbacks: {
                title: (items) => monthLabel(items[0].chart.$months[items[0].dataIndex].month, true),
                label: (item) => item.dataset.label + ': ' +
                  (item.datasetIndex === 0 ? fullMoney(item.parsed.y) : nfInt.format(item.parsed.y)),
                afterBody: (items) => {
                  const m = items[0].chart.$months[items[0].dataIndex];
                  const lines = [];
                  const partial = partialText(m);
                  if (partial) lines.push(partial.charAt(0).toUpperCase() + partial.slice(1));
                  if (m.records_without_amount) {
                    lines.push(_('Без суммы: {n} {records}', { n: nfInt.format(m.records_without_amount), records: plural(m.records_without_amount, RECORDS) }));
                  }
                  return lines;
                },
              },
            },
          },
        }),
      });
      chart.$months = months;
      chart.data.labels = months.map((m) => monthLabel(m.month));
      chart.data.datasets[0].data = months.map((m) => m.amount);
      // неполные месяцы на краях периода — бледнее
      chart.data.datasets[0].backgroundColor = months.map((m) => (partialText(m) ? COLORS.accentBarSoft : COLORS.accentBar));
      chart.data.datasets[1].data = months.map((m) => (m.deals > 0 ? m.deals : m.records));
      chart.update();

      const partial = months.filter((m) => partialText(m));
      const partialList = partial.map((m) => monthLabel(m.month) + ' — ' + _('данные {range}', { range: partialRange(m) })).join('; ');
      hint.textContent = partial.length
        ? (partial.length === 1
          ? _('Бледный столбец — неполный месяц: {months}.', { months: partialList })
          : _('Бледные столбцы — неполные месяцы: {months}.', { months: partialList }))
        : '';
      hint.hidden = !hint.textContent;
    }

    /** Горизонтальные столбцы «топ по сумме» — для товаров и участников. */
    function renderTop(name, items, color, emptyMessage) {
      if (!items.length) {
        setEmpty(name, emptyMessage);
        resetChart(name);
        return;
      }
      setEmpty(name, '');
      const chart = ensureChart(name, {
        type: 'bar',
        data: { labels: [], datasets: [{ label: _('Сумма сделок, тг'), data: [], borderRadius: 3, backgroundColor: color }] },
        options: Object.assign(baseOptions(), {
          indexAxis: 'y',
          interaction: { mode: 'nearest', axis: 'y', intersect: false },
          scales: {
            x: valueAxis(),
            y: {
              grid: { display: false },
              ticks: {
                autoSkip: false,
                font: { size: 11.5 },
                // подпись занимает не больше ~40% ширины графика; полное название — в подсказке
                callback: function (value) {
                  const limit = Math.max(14, Math.floor((this.chart.width * 0.4) / 6.2));
                  return truncate(this.getLabelForValue(value), limit);
                },
              },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              padding: 10,
              callbacks: {
                title: (tips) => tips[0].chart.$items[tips[0].dataIndex].name,
                label: (item) => fullMoney(item.parsed.x),
                afterLabel: (item) => {
                  const it = item.chart.$items[item.dataIndex];
                  const lines = [it.records + ' ' + plural(it.records, RECORDS)];
                  if (it.bin) lines.push(_('БИН {bin}', { bin: it.bin }));
                  if (it.code) lines.push(_('Код {code}', { code: it.code }));
                  return lines;
                },
              },
            },
          },
        }),
      });
      chart.$items = items;
      chart.data.labels = items.map((it) => it.name);
      chart.data.datasets[0].data = items.map((it) => it.amount);
      chart.data.datasets[0].backgroundColor = color;
      chart.update();
    }

    function renderParties() {
      if (!state.data) return;
      const buyers = state.role === 'buyers';
      renderTop(
        'parties',
        buyers ? state.data.top_buyers : state.data.top_sellers,
        buyers ? COLORS.accentBar : COLORS.caspyBar,
        buyers ? _('Нет покупателей с суммой сделок') : _('Нет продавцов с суммой сделок')
      );
    }

    /* ---- сводка ---- */

    function renderSummary(summary, days) {
      const has = summary.records > 0;
      document.getElementById('s-records').textContent = nfInt.format(summary.records);
      document.getElementById('s-days').textContent = nfInt.format(summary.trading_days);
      document.getElementById('s-days-sub').textContent =
        days.length ? formatDate(days[0].date) + ' – ' + formatDate(days[days.length - 1].date) : '';
      document.getElementById('s-amount').textContent = has ? _('{amount} тг', { amount: compactMoney(summary.total_amount) }) : '—';
      document.getElementById('s-amount-sub').textContent = has ? fullMoney(summary.total_amount) : '';
      document.getElementById('s-noamount').textContent = has ? percent(summary.share_without_amount) : '—';
      document.getElementById('s-noamount-sub').textContent = has
        ? _('{n} из {total} {records}', {
          n: nfInt.format(summary.records_without_amount), total: nfInt.format(summary.records),
          records: plural(summary.records, RECORDS_GEN),
        })
        : '';
      if (has && summary.records_without_amount > 0) {
        noteBox.hidden = false;
        noteBox.textContent = _('У {n} {records} ({share}) нет суммы сделки: они учтены в числе записей и дней, но не входят в суммы и топы.', {
          n: nfInt.format(summary.records_without_amount),
          records: plural(summary.records_without_amount, RECORDS_GEN),
          share: percent(summary.share_without_amount),
        });
      } else {
        noteBox.hidden = true;
      }
    }

    function render(data) {
      state.data = data;
      rememberProducts(data.selected);
      if (data.unknown_ids && data.unknown_ids.length) {
        // товары из старой ссылки, которых больше нет в базе, — убираем из выбора
        state.selected = state.selected.filter((id) => !data.unknown_ids.includes(id));
        assignSlots();
        syncUrl();
        notify(_('Не найдены и убраны из выбора: {ids}', { ids: data.unknown_ids.map((id) => '№' + id).join(', ') }));
      }
      renderChips();
      if (picker.open) renderList();
      renderSummary(data.summary, data.turnover_by_day);
      renderCompareChart(data);
      renderCompareTable(data);
      renderCharts(data);
    }

    function renderCharts(data) {
      renderTurnover(data.turnover_by_day);
      renderMonths(data.turnover_by_month || []);
      renderTop('products', data.top_products, COLORS.accentBar, _('Нет товаров с суммой сделок'));
      renderParties();
    }

    /** Смена темы: новые цвета из CSS, графики пересоздаются без перезагрузки страницы. */
    document.addEventListener('portal:themechange', () => {
      readColors();
      applyChartTheme();
      Object.keys(charts).forEach((name) => { charts[name].destroy(); delete charts[name]; });
      renderChips(); // цветные метки товаров
      if (!state.data || typeof Chart === 'undefined') return;
      renderCompareChart(state.data);
      renderCompareTable(state.data);
      renderCharts(state.data);
    });

    /* ---- загрузка ---- */

    function setLoading(on) {
      loader.hidden = !on;
      summaryBox.classList.toggle('loading', on);
      chartsBox.classList.toggle('loading', on);
      summaryBox.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    async function load() {
      loadDebounced.cancel();
      const seq = ++requestSeq;
      if (abortController) abortController.abort();
      abortController = new AbortController();
      const params = filterParams();
      syncUrl();
      setLoading(true);
      try {
        const response = await fetch('/api/analytics/?' + params.toString(), {
          signal: abortController.signal,
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(await readError(response));
        const data = await response.json();
        if (seq !== requestSeq) return;
        errorBox.hidden = true;
        render(data);
        setLoading(false);
      } catch (error) {
        if (error.name === 'AbortError' || seq !== requestSeq) return;
        errorText.textContent = _('Не удалось загрузить аналитику: {error}', { error: error.message || _('нет связи с сервером') });
        errorBox.hidden = false;
        setLoading(false);
      }
    }

    /* ---- события ---- */

    /** Смена биржи или периода: новый список товаров и пересчёт (выбор сохраняется). */
    function periodChanged() {
      loadProducts();
      load();
    }
    PERIOD_KEYS.forEach((key) => inputs[key].addEventListener('change', periodChanged));
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      load();
    });
    document.getElementById('a-reset').addEventListener('click', () => {
      PERIOD_KEYS.forEach((key) => { inputs[key].value = ''; });
      state.selected = [];
      state.legacyQ = '';
      state.hidden.clear();
      state.sort = { key: null, dir: 'desc' };
      picker.chipsExpanded = false;
      closePicker();
      assignSlots();
      renderChips();
      loadProducts();
      load();
    });
    document.getElementById('an-retry').addEventListener('click', () => { loadProducts(); load(); });
    roleButtons.forEach((button) => {
      button.addEventListener('click', () => {
        state.role = button.dataset.role;
        roleButtons.forEach((b) => {
          const active = b === button;
          b.classList.toggle('is-active', active);
          b.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        renderParties();
      });
    });
    metricButtons.forEach((button) => {
      button.addEventListener('click', () => {
        state.metric = button.dataset.metric;
        metricButtons.forEach((b) => {
          const active = b === button;
          b.classList.toggle('is-active', active);
          b.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        if (state.data) renderCompareChart(state.data);
      });
    });

    /* ---- старт ---- */
    const truncated = readUrl();
    assignSlots();
    renderChips();
    syncUrl();
    if (truncated) notify(_('В ссылке больше {max} товаров — взяты первые {max}.', { max: MAX_SELECTED }));
    loadProducts();
    if (typeof Chart === 'undefined') {
      errorText.textContent = _('Библиотека графиков не загружена ({path}).', { path: 'static/portal/vendor/chart.umd.min.js' });
      errorBox.hidden = false;
      return;
    }
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
    Chart.defaults.font.size = narrowScreen.matches ? 11.5 : 12.5;
    applyChartTheme();
    load();
  }

  /* ===================================================== страница «Новости» */

  /** Страница работает и без JS (обычная GET-форма); смена биржи сразу применяет фильтр. */
  function initNews() {
    const form = document.getElementById('news-filters-form');
    const select = document.getElementById('n-exchange');
    if (!form || !select) return;
    select.addEventListener('change', () => {
      if (typeof form.requestSubmit === 'function') form.requestSubmit();
      else form.submit();
    });
  }

  /* ===================================================== баннер cookie */

  const CONSENT_KEY = 'cookie_consent';

  /** Запись о согласии из localStorage (хранилище может быть недоступно — тогда null). */
  function readConsent() {
    try {
      const raw = window.localStorage.getItem(CONSENT_KEY);
      if (!raw) return null;
      const value = JSON.parse(raw);
      return value && value.accepted ? value : null;
    } catch (e) {
      return null;
    }
  }

  function saveConsent() {
    try {
      window.localStorage.setItem(CONSENT_KEY, JSON.stringify({ accepted: true, date: new Date().toISOString() }));
      return true;
    } catch (e) {
      return false; // приватный режим или запрет хранилища: баннер просто скроется до перезагрузки
    }
  }

  function initCookieBanner() {
    const banner = document.getElementById('cookie-banner');
    const acceptButton = document.getElementById('cookie-accept');
    if (!banner || !acceptButton || readConsent()) return;

    // Отступ снизу, чтобы баннер не перекрывал пагинацию и подвал
    function updateOffset() {
      document.body.style.paddingBottom = banner.hidden ? '' : banner.offsetHeight + 'px';
    }

    function hide() {
      banner.hidden = true;
      document.body.classList.remove('has-cookie-banner');
      updateOffset();
      window.removeEventListener('resize', updateOffset);
    }

    banner.hidden = false;
    document.body.classList.add('has-cookie-banner');
    updateOffset();
    window.addEventListener('resize', updateOffset);

    acceptButton.addEventListener('click', () => {
      saveConsent();
      hide();
    });
    banner.addEventListener('keydown', (event) => {
      // Escape закрывает баннер до следующего визита (без сохранения согласия)
      if (event.key === 'Escape') hide();
    });
    // Фокус на кнопке «Принять», не прокручивая страницу
    try { acceptButton.focus({ preventScroll: true }); } catch (e) { acceptButton.focus(); }
  }

  /* ===================================================== запуск */

  document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.dataset.page;
    initThemeToggle();
    initLanguageSwitch();
    initCollapsibleFilters();
    if (page === 'trades') initTrades();
    else if (page === 'status') initStatus();
    else if (page === 'analytics') initAnalytics();
    else if (page === 'news') initNews();
    initCookieBanner();
  });
})();
