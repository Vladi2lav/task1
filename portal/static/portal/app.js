/* Портал итогов торгов ETS и Каспий.
 * Страница «/»      — таблица итогов торгов (fetch к /api/trades/, без перезагрузки);
 * страница «/status/» — состояние данных и ручной запуск сбора;
 * страница «/analytics/» — сводка и графики Chart.js (fetch к /api/analytics/);
 * страница «/news/»   — лента новостей (серверная разметка, JS только применяет фильтр);
 * на всех страницах   — баннер уведомления о cookie (согласие в localStorage).
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

  const nfInt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
  const nfMoney = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nfQty = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 3 });

  /** 'ГГГГ-ММ-ДД' -> 'дд.мм.гггг' (без Date, чтобы не зависеть от часового пояса). */
  function formatDate(iso) {
    if (!iso) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    return m ? m[3] + '.' + m[2] + '.' + m[1] : iso;
  }

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /** Склонение: plural(5, ['запись', 'записи', 'записей']). */
  function plural(n, forms) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1) return forms[0];
    return forms[2];
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
    if (response.status === 403) return 'Сессия устарела или нет токена CSRF: обновите страницу и повторите';
    return 'Ошибка сервера (' + response.status + ')';
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

  /* ===================================================== страница «Итоги торгов» */

  function initTrades() {
    const DEFAULT_ORDERING = '-trade_date';
    const DEFAULT_PAGE_SIZE = 50;
    const FILTER_KEYS = ['exchange', 'date_from', 'date_to', 'q', 'party', 'broker', 'amount_min', 'amount_max', 'delivery'];
    const TEXT_KEYS = ['q', 'party', 'broker', 'amount_min', 'amount_max', 'delivery'];
    const SUGGEST_KEYS = { q: 'product', party: 'party', broker: 'broker', delivery: 'delivery' };

    // key — поле сортировки в API; firstDir — направление при первом клике
    const COLUMNS = [
      { key: 'trade_date', label: 'Дата', firstDir: '-' },
      { key: 'exchange', label: 'Биржа', firstDir: '' },
      { key: 'product', label: 'Товар', firstDir: '' },
      { key: 'seller', label: 'Продавец', firstDir: '' },
      { key: 'buyer', label: 'Покупатель', firstDir: '' },
      { key: 'seller_broker', label: 'Брокер продавца', firstDir: '' },
      { key: 'buyer_broker', label: 'Брокер покупателя', firstDir: '' },
      { key: 'quantity', label: 'Объём', num: true, firstDir: '-' },
      { key: 'price_avg', label: 'Цена ср., тг', num: true, firstDir: '-' },
      { key: 'amount', label: 'Сумма, тг', num: true, firstDir: '-' },
      { key: 'delivery_place', label: 'Место / базис поставки', firstDir: '' },
    ];
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
        const button = el('button', { type: 'button', class: 'th-sort', title: 'Сортировать' }, [col.label, arrow]);
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
          const suffix = dir === '-'
            ? (isDate ? ': сначала новые' : ': по убыванию')
            : (isDate ? ': сначала старые' : ': по возрастанию');
          sortSelect.appendChild(el('option', { value: dir + col.key, text: col.label + suffix }));
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
      if (bin) td.appendChild(el('div', { class: 'cell-sub', text: 'БИН ' + bin }));
      return td;
    }

    /** Место поставки; базис (FCA, EXW…) — второй строкой. У ETS места нет — показываем базис. */
    function placeCell(place, basis, label) {
      const td = el('td', { class: 'cell-place', 'data-label': label });
      if (!place && !basis) { td.appendChild(dash()); return td; }
      td.appendChild(el('div', { class: 'cell-main', text: place || basis }));
      if (place && basis) td.appendChild(el('div', { class: 'cell-sub', text: 'базис ' + basis }));
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

    function money(value) { return isNumber(value) ? nfMoney.format(value) + ' тг' : '—'; }

    function buildDetails(row) {
      const items = [
        detailItem('Время торгов', row.trade_time ? row.trade_time.slice(0, 5) : ''),
        detailItem('Число сделок', isNumber(row.deals_count) ? nfInt.format(row.deals_count) : ''),
        detailItem('Код товара', row.product_code),
        detailItem('Код ТН ВЭД', row.tnved),
        detailItem('Цена открытия', money(row.price_open)),
        detailItem('Цена закрытия', money(row.price_close)),
        detailItem('Мин. цена', money(row.price_min)),
        detailItem('Макс. цена', money(row.price_max)),
        detailItem('Цена с НДС', money(row.price_with_vat)),
        detailItem('Цена без НДС', money(row.price_without_vat)),
        detailItem('Базис поставки', row.delivery_basis),
        detailItem('Срок поставки', row.delivery_term),
      ];
      if (row.source_url) items.push(detailItem('Источник', safeLink(row.source_url)));
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
      const tr = el('tr', { class: 'trade-row', tabindex: '0', 'aria-expanded': 'false', title: 'Показать подробности' }, [
        el('td', { class: 'cell-date', text: formatDate(row.trade_date) }),
        el('td', { class: 'cell-exchange' }, [exchangeBadge(row.exchange, row.exchange_name)]),
        textCell(row.product, 'cell-product'),
        partyCell(row.seller, row.seller_bin, 'cell-seller', 'Продавец'),
        partyCell(row.buyer, row.buyer_bin, 'cell-buyer', 'Покупатель'),
        textCell(row.seller_broker, 'cell-broker cell-sbroker', 'Брокер продавца'),
        textCell(row.buyer_broker, 'cell-broker cell-bbroker', 'Брокер покупателя'),
        numCell(row.quantity, nfQty, 'cell-qty', row.unit, 'Объём'),
        numCell(row.price_avg, nfMoney, 'cell-price', null, 'Цена ср., тг'),
        numCell(row.amount, nfMoney, 'amount'),
        placeCell(row.delivery_place, row.delivery_basis, 'Место / базис поставки'),
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
      pagination.appendChild(pageButton('‹ Назад', page - 1, { disabled: page <= 1 }));
      const visible = new Set([1, pages]);
      for (let p = page - 2; p <= page + 2; p++) if (p >= 1 && p <= pages) visible.add(p);
      let previous = 0;
      [...visible].sort((a, b) => a - b).forEach((p) => {
        if (p - previous > 1) pagination.appendChild(el('span', { class: 'page-gap', text: '…' }));
        pagination.appendChild(pageButton(nfInt.format(p), p, { current: p === page }));
        previous = p;
      });
      pagination.appendChild(pageButton('Вперёд ›', page + 1, { disabled: page >= pages }));
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
      foundCount.textContent = 'Ошибка загрузки';
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
        foundCount.textContent = 'Найдено: 0 записей';
        foundSum.textContent = '';
        clear(pagination);
        return;
      }
      table.hidden = false;
      emptyState.hidden = true;
      renderRows(data.results);
      const from = (data.page - 1) * data.page_size + 1;
      const to = from + data.results.length - 1;
      foundCount.textContent = 'Найдено: ' + nfInt.format(count) + ' ' +
        plural(count, ['запись', 'записи', 'записей']) +
        ' · показаны ' + nfInt.format(from) + '–' + nfInt.format(to);
      foundSum.textContent = isNumber(data.amount_sum) ? 'Сумма по выборке: ' + nfMoney.format(data.amount_sum) + ' тг' : '';
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
        showError(error.message || 'Не удалось связаться с сервером');
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
          select.appendChild(el('option', { value: '', text: 'Обе биржи' }));
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

    const STATUS_LABELS = { success: 'Успешно', partial: 'С ошибками', failed: 'Ошибка', running: 'Выполняется' };

    function statusBadge(status, label) {
      if (!status) return el('span', { class: 'badge badge-none', text: 'Нет запусков' });
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
        stat('Последняя торговая запись', exchange.last_trade_date ? formatDate(exchange.last_trade_date) : '—', true),
        stat('Записей в базе', nfInt.format(exchange.trades_count), true),
      ];
      if (run) {
        stats.push(stat('Последний запуск', run.started_at_display + ' (' + run.trigger_display.toLowerCase() + ')'));
        stats.push(stat('Период запуска', periodText(run)));
        const result = el('span', {}, [
          '+' + nfInt.format(run.rows_added) + ' добавлено, ',
          nfInt.format(run.rows_skipped) + ' пропущено',
        ]);
        stats.push(stat('Результат', result));
        stats.push(stat('Окончание', run.finished_at_display || (run.status === 'running' ? 'идёт…' : '—')));
      } else {
        stats.push(stat('Последний запуск', 'ещё не запускался'));
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
          el('td', { text: run.started_at_display, class: 'cell-date' }),
          el('td', { text: run.finished_at_display || '—', class: 'cell-date' }),
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
        const names = (collect.exchanges || []).map((c) => (c === 'caspy' ? 'Каспий' : c.toUpperCase())).join(', ');
        setCollectBox('running', [
          el('span', { class: 'spinner' }),
          'Идёт сбор: ' + names + ', период ' + formatDate(collect.date_from) + ' – ' + formatDate(collect.date_to) +
            ' (начат ' + (collect.started_at_display || '') + ')',
        ]);
      } else if (collect.running_elsewhere) {
        setCollectBox('running', [
          el('span', { class: 'spinner' }),
          'Сбор уже выполняется (запущен по расписанию или из командной строки). Дождитесь завершения.',
        ]);
      } else if (lastMessage) {
        setCollectBox(lastMessage.kind, [lastMessage.text]);
      } else if (collect.finished_at && collect.error) {
        setCollectBox('fail', ['Ручной запуск завершился ошибкой (' + collect.finished_at_display + '): ' + collect.error]);
      } else if (collect.finished_at) {
        setCollectBox('ok', ['Ручной запуск завершён в ' + collect.finished_at_display + '. Результат — в таблице ниже.']);
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
        totalLine.textContent = 'Всего записей в базе: ' + nfInt.format(data.total_trades);
        renderRuns(data.runs);
        renderCollect(data.collect);
        const anyRunning = data.collect.running || data.collect.running_elsewhere ||
          data.exchanges.some((e) => e.last_run && e.last_run.status === 'running');
        if (anyRunning) delay = POLL_FAST;
      } catch (error) {
        clear(cardsBox);
        cardsBox.appendChild(el('div', { class: 'panel card-error', text: 'Не удалось загрузить состояние: ' + error.message }));
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
          lastMessage = { kind: 'fail', text: 'Глубина должна быть от ' + min + ' до ' + max + ' дней.' };
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
          lastMessage = { kind: 'fail', text: 'Не удалось связаться с сервером' };
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
    const FILTER_KEYS = ['exchange', 'date_from', 'date_to', 'q', 'product_id'];
    const COLORS = {
      accent: '#0e5a61', accentSoft: 'rgba(14, 90, 97, 0.10)', accentBar: 'rgba(14, 90, 97, 0.78)',
      caspy: '#b0681e', caspyBar: 'rgba(176, 104, 30, 0.8)',
      text: '#5b6068', grid: '#ebe9e3',
    };
    const inputs = {};
    FILTER_KEYS.forEach((key) => { inputs[key] = document.getElementById('a-' + key); });
    const loader = document.getElementById('an-loader');
    const errorBox = document.getElementById('an-error');
    const errorText = document.getElementById('an-error-text');
    const noteBox = document.getElementById('an-note');
    const summaryBox = document.getElementById('an-summary');
    const chartsBox = document.querySelector('.charts-grid');
    const roleButtons = [...document.querySelectorAll('.chart-switch [data-role]')];

    const state = { role: 'buyers', data: null };
    const charts = {};
    let requestSeq = 0;
    let abortController = null;

    const nfOne = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
    const nfTwo = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

    /* ---- форматирование ---- */

    /** Короткая запись денег для осей: 1,2 млрд, 35 млн, 400 тыс. */
    function compactMoney(value) {
      const abs = Math.abs(value);
      if (abs >= 1e9) return nfTwo.format(value / 1e9) + ' млрд';
      if (abs >= 1e6) return nfTwo.format(value / 1e6) + ' млн';
      if (abs >= 1e3) return nfOne.format(value / 1e3) + ' тыс.';
      return nfInt.format(value);
    }

    function fullMoney(value) { return nfMoney.format(value) + ' тг'; }

    function shortDate(iso) {
      const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
      return m ? m[3] + '.' + m[2] : iso;
    }

    /** Подпись для оси: длинное название обрезается, полное — в подсказке. */
    function truncate(text, max) {
      const value = String(text || '');
      return value.length > max ? value.slice(0, max - 1).trimEnd() + '…' : value;
    }

    function percent(share) {
      const value = share * 100;
      return (value > 0 && value < 0.1 ? '<0,1' : nfOne.format(value)) + '%';
    }

    const RECORDS = ['запись', 'записи', 'записей'];
    const RECORDS_GEN = ['записи', 'записей', 'записей'];

    /* ---- параметры и адресная строка ---- */

    function readUrl() {
      const params = new URLSearchParams(window.location.search);
      ['exchange', 'date_from', 'date_to', 'q'].forEach((key) => {
        const value = params.get(key) || '';
        if (inputs[key].tagName === 'SELECT' && ![...inputs[key].options].some((o) => o.value === value)) return;
        inputs[key].value = value;
      });
      const productId = params.get('product_id') || '';
      if (/^\d+$/.test(productId)) {
        // список товаров придёт с сервером; пока подставляем выбранный из адреса
        inputs.product_id.appendChild(el('option', { value: productId, text: 'Товар №' + productId }));
        inputs.product_id.value = productId;
      }
    }

    function filterParams() {
      const params = new URLSearchParams();
      FILTER_KEYS.forEach((key) => {
        const value = (inputs[key].value || '').trim();
        if (value) params.set(key, value);
      });
      return params;
    }

    function syncUrl(params) {
      const query = params.toString();
      history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
      FILTER_KEYS.forEach((key) => inputs[key].classList.toggle('has-value', !!(inputs[key].value || '').trim()));
      updateFiltersBadge(document.getElementById('an-filters-form'));
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

    function renderPrice(block) {
      const points = block.points;
      const product = block.product;
      document.getElementById('price-sub').textContent =
        product ? product.name + (product.code ? ' · ' + product.code : '') : 'товар не выбран';
      if (!points.length) {
        setEmpty('price', product ? 'По этому товару нет цен за выбранный период' : 'Нет данных для графика');
        resetChart('price');
        return;
      }
      setEmpty('price', '');
      const chart = ensureChart('price', {
        type: 'line',
        data: { labels: [], datasets: [{
          label: 'Средневзвешенная цена, тг',
          data: [],
          borderColor: COLORS.accent,
          backgroundColor: COLORS.accentSoft,
          fill: true, tension: 0.25, borderWidth: 2,
          pointRadius: 2.5, pointHoverRadius: 5, pointBackgroundColor: COLORS.accent,
        }] },
        options: Object.assign(baseOptions(), {
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 14 } },
            y: valueAxis({ beginAtZero: false, ticks: { callback: (v) => nfInt.format(v) } }),
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              padding: 10,
              callbacks: {
                title: (items) => formatDate(items[0].chart.$points[items[0].dataIndex].date),
                label: (item) => 'Цена: ' + fullMoney(item.parsed.y),
                afterLabel: (item) => {
                  const n = item.chart.$points[item.dataIndex].records;
                  return n + ' ' + plural(n, RECORDS) + ' за день';
                },
              },
            },
          },
        }),
      });
      chart.$points = points;
      chart.data.labels = points.map((p) => shortDate(p.date));
      chart.data.datasets[0].data = points.map((p) => p.price);
      chart.update();
    }

    function renderTurnover(days) {
      if (!days.length) {
        setEmpty('turnover', 'Нет данных за выбранный период');
        resetChart('turnover');
        return;
      }
      setEmpty('turnover', '');
      const chart = ensureChart('turnover', {
        data: { labels: [], datasets: [
          { type: 'bar', label: 'Сумма сделок', data: [], yAxisID: 'y', backgroundColor: COLORS.accentBar, borderRadius: 3, order: 2 },
          { type: 'line', label: 'Число сделок', data: [], yAxisID: 'y1', borderColor: COLORS.caspy, backgroundColor: COLORS.caspy,
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
                    ? 'Без суммы: ' + d.records_without_amount + ' ' + plural(d.records_without_amount, RECORDS)
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
        data: { labels: [], datasets: [{ label: 'Сумма сделок, тг', data: [], borderRadius: 3, backgroundColor: color }] },
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
                  if (it.bin) lines.push('БИН ' + it.bin);
                  if (it.code) lines.push('Код ' + it.code);
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
        buyers ? 'Нет покупателей с суммой сделок' : 'Нет продавцов с суммой сделок'
      );
    }

    /* ---- сводка ---- */

    function renderSummary(summary, days) {
      const has = summary.records > 0;
      document.getElementById('s-records').textContent = nfInt.format(summary.records);
      document.getElementById('s-days').textContent = nfInt.format(summary.trading_days);
      document.getElementById('s-days-sub').textContent =
        days.length ? formatDate(days[0].date) + ' – ' + formatDate(days[days.length - 1].date) : '';
      document.getElementById('s-amount').textContent = has ? compactMoney(summary.total_amount) + ' тг' : '—';
      document.getElementById('s-amount-sub').textContent = has ? fullMoney(summary.total_amount) : '';
      document.getElementById('s-noamount').textContent = has ? percent(summary.share_without_amount) : '—';
      document.getElementById('s-noamount-sub').textContent = has
        ? nfInt.format(summary.records_without_amount) + ' из ' + nfInt.format(summary.records) + ' ' +
          plural(summary.records, RECORDS_GEN)
        : '';
      if (has && summary.records_without_amount > 0) {
        noteBox.hidden = false;
        noteBox.textContent = 'У ' + nfInt.format(summary.records_without_amount) + ' ' +
          plural(summary.records_without_amount, RECORDS_GEN) + ' (' + percent(summary.share_without_amount) +
          ') нет суммы сделки: они учтены в числе записей и дней, но не входят в суммы и топы.';
      } else {
        noteBox.hidden = true;
      }
    }

    function renderProductSelect(products, selected) {
      const select = inputs.product_id;
      clear(select);
      if (!products.length && !selected) {
        select.appendChild(el('option', { value: '', text: 'Нет товаров' }));
        select.disabled = true;
        return;
      }
      products.forEach((p) => {
        select.appendChild(el('option', {
          value: String(p.id), title: p.name,
          text: truncate(p.name, 90) + ' (' + nfInt.format(p.records) + ')',
        }));
      });
      if (selected && !products.some((p) => p.id === selected.id)) {
        select.appendChild(el('option', { value: String(selected.id), text: truncate(selected.name, 90), title: selected.name }));
      }
      select.disabled = false;
      if (selected) select.value = String(selected.id);
    }

    function render(data) {
      state.data = data;
      renderSummary(data.summary, data.turnover_by_day);
      renderProductSelect(data.products, data.price_dynamics.product);
      renderPrice(data.price_dynamics);
      renderTurnover(data.turnover_by_day);
      renderTop('products', data.top_products, COLORS.accentBar, 'Нет товаров с суммой сделок');
      renderParties();
    }

    /* ---- загрузка ---- */

    function setLoading(on) {
      loader.hidden = !on;
      summaryBox.classList.toggle('loading', on);
      chartsBox.classList.toggle('loading', on);
      summaryBox.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    async function load() {
      const seq = ++requestSeq;
      if (abortController) abortController.abort();
      abortController = new AbortController();
      const params = filterParams();
      syncUrl(params);
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
        errorText.textContent = 'Не удалось загрузить аналитику: ' + (error.message || 'нет связи с сервером');
        errorBox.hidden = false;
        setLoading(false);
      }
    }

    /* ---- события ---- */

    /** Смена любого фильтра, кроме самого товара, возвращает график цены к самому частому товару. */
    function reloadWithDefaultProduct() {
      inputs.product_id.value = '';
      load();
    }
    const loadDebounced = debounce(reloadWithDefaultProduct, 350);
    inputs.q.addEventListener('input', loadDebounced);
    ['exchange', 'date_from', 'date_to'].forEach((key) => {
      inputs[key].addEventListener('change', () => { loadDebounced.cancel(); reloadWithDefaultProduct(); });
    });
    inputs.product_id.addEventListener('change', load);
    document.getElementById('an-filters-form').addEventListener('submit', (event) => {
      event.preventDefault();
      loadDebounced.cancel();
      reloadWithDefaultProduct();
    });
    document.getElementById('a-reset').addEventListener('click', () => {
      FILTER_KEYS.forEach((key) => { inputs[key].value = ''; });
      loadDebounced.cancel();
      load();
    });
    document.getElementById('an-retry').addEventListener('click', load);
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

    /* ---- старт ---- */
    if (typeof Chart === 'undefined') {
      errorText.textContent = 'Библиотека графиков не загружена (static/portal/vendor/chart.umd.min.js).';
      errorBox.hidden = false;
      return;
    }
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
    Chart.defaults.font.size = narrowScreen.matches ? 11.5 : 12.5;
    Chart.defaults.color = COLORS.text;
    readUrl();
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
    initCollapsibleFilters();
    if (page === 'trades') initTrades();
    else if (page === 'status') initStatus();
    else if (page === 'analytics') initAnalytics();
    else if (page === 'news') initNews();
    initCookieBanner();
  });
})();
