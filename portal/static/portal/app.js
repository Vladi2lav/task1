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
    const PERIOD_KEYS = ['exchange', 'date_from', 'date_to'];
    const MAX_SELECTED = 10;
    const LIST_RENDER_LIMIT = 400;
    const LIMIT_MESSAGE = 'Можно выбрать не больше ' + MAX_SELECTED + ' товаров для сравнения. ' +
      'Снимите отметку с одного из выбранных, чтобы добавить другой.';
    const COLORS = {
      accent: '#0e5a61', accentSoft: 'rgba(14, 90, 97, 0.10)', accentBar: 'rgba(14, 90, 97, 0.78)',
      caspy: '#b0681e', caspyBar: 'rgba(176, 104, 30, 0.8)',
      text: '#5b6068', grid: '#ebe9e3',
    };
    /* Цвета линий сравнения: 8 приглушённых оттенков, порядок проверен на различимость
     * (в том числе при нарушениях цветового зрения). 9-й и 10-й товар — те же оттенки пунктиром.
     * Цвет закреплён за товаром, а не за позицией: снятие отметки не перекрашивает остальные. */
    const SERIES_COLORS = ['#2a6fb5', '#d0652e', '#1f9472', '#c99a06', '#c75a8a', '#4e8a2a', '#6a58c4', '#c8433f'];

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

    const nfOne = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
    const nfTwo = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const nfPct = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 2 });

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

    /** Изменение в процентах со знаком: +12,3 % / −4,5 % (минус — типографский). */
    function signedPercent(value, format) {
      if (!isNumber(value)) return '';
      const text = (format || nfPct).format(Math.abs(value)) + ' %';
      if (value > 0) return '+' + text;
      if (value < 0) return '−' + text;
      return '0 %';
    }

    const RECORDS = ['запись', 'записи', 'записей'];
    const RECORDS_GEN = ['записи', 'записей', 'записей'];
    const PRODUCTS = ['товар', 'товара', 'товаров'];
    const DEALS = ['сделка', 'сделки', 'сделок'];

    /** Строка для поиска: нижний регистр, ё = е. */
    function normalize(text) {
      return String(text || '').toLowerCase().replace(/ё/g, 'е');
    }

    function productName(id) {
      const p = state.known.get(id);
      return p ? p.name : 'Товар №' + id;
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
      (p.exchange_codes || []).forEach((code) => meta.appendChild(exchangeBadge(code, code === 'caspy' ? 'Каспий' : 'ETS')));
      const codes = [];
      if (p.code) codes.push('код ' + p.code);
      if (p.tnved) codes.push('ТН ВЭД ' + p.tnved);
      if (codes.length) meta.appendChild(el('span', { class: 'combo-codes', text: codes.join(' · ') }));
      return meta;
    }

    function renderOption(p, index, missing) {
      const selected = state.selected.includes(p.id);
      const deals = missing
        ? el('span', { class: 'combo-deals is-missing', text: 'нет сделок' })
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
        picker.list.appendChild(el('li', { role: 'presentation', class: 'combo-group', text: 'Выбраны, но нет сделок в выбранной бирже и периоде' }));
        missing.forEach((p) => {
          picker.list.appendChild(renderOption(p, picker.options.length, true));
          picker.options.push(p);
        });
        if (picker.found.length) {
          picker.list.appendChild(el('li', { role: 'presentation', class: 'combo-group', text: 'Товары выборки' }));
        }
      }
      picker.found.slice(0, LIST_RENDER_LIMIT).forEach((p) => {
        picker.list.appendChild(renderOption(p, picker.options.length, false));
        picker.options.push(p);
      });
      if (picker.found.length > LIST_RENDER_LIMIT) {
        picker.list.appendChild(el('li', {
          role: 'presentation', class: 'combo-empty',
          text: 'Показаны первые ' + LIST_RENDER_LIMIT + ' из ' + nfInt.format(picker.found.length) + ' — уточните поиск',
        }));
      }
      if (!picker.options.length) {
        let text = 'Ничего не найдено — попробуйте часть слова или код ТН ВЭД';
        if (state.productsError) text = state.productsError;
        else if (!state.productsLoaded) text = 'Загрузка списка товаров…';
        else if (!words.length) text = 'В выбранной бирже и периоде нет сделок';
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
          ? 'Найдено: ' + nfInt.format(picker.found.length) + ' из ' + nfInt.format(total)
          : nfInt.format(total) + ' ' + plural(total, PRODUCTS) + ' в выборке';
      }
      if (n) text += (text ? ' · ' : '') + 'выбрано ' + n + ' из ' + MAX_SELECTED;
      picker.count.textContent = text;
      picker.allBtn.setAttribute('aria-pressed', n === 0 && !state.legacyQ ? 'true' : 'false');
      picker.allBtn.classList.toggle('is-active', n === 0 && !state.legacyQ);
      const addable = picker.found.filter((p) => !state.selected.includes(p.id)).length;
      picker.foundBtn.hidden = !words.length;
      picker.foundBtn.disabled = addable === 0;
      picker.foundBtn.textContent = 'Выбрать найденные' + (addable ? ' (' + nfInt.format(addable) + ')' : '');
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
      picker.arrowBtn.setAttribute('aria-label', 'Закрыть список товаров');
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
      picker.arrowBtn.setAttribute('aria-label', 'Открыть список товаров');
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
        picker.chips.appendChild(el('span', { class: 'combo-chip combo-chip-word', role: 'listitem', title: 'Старый фильтр по ключевому слову в названии товара' }, [
          el('span', { class: 'combo-chip-text', text: 'Слово: «' + state.legacyQ + '»' }),
          el('button', { type: 'button', class: 'combo-chip-remove', 'aria-label': 'Убрать фильтр по слову ' + state.legacyQ, data: { remove: 'q' } }, ['×']),
        ]));
      }
      if (collapsed) {
        const missing = ids.filter(isMissing).length;
        picker.chips.appendChild(el('button', {
          type: 'button', class: 'combo-chip combo-chip-toggle', 'aria-expanded': 'false', data: { toggle: '1' },
          title: ids.map(productName).join('\n'),
        }, [
          'Выбрано: ' + ids.length + (missing ? ' (без сделок: ' + missing + ')' : ''),
          el('span', { class: 'combo-chip-chevron', 'aria-hidden': 'true' }),
        ]));
      } else {
        ids.forEach((id) => {
          const missing = isMissing(id);
          const name = productName(id);
          picker.chips.appendChild(el('span', {
            class: 'combo-chip' + (missing ? ' is-missing' : ''), role: 'listitem',
            title: name + (missing ? '\nНет сделок в выбранной бирже и периоде' : ''),
          }, [
            swatch(styleForSlot(state.slots.get(id) || 0)),
            el('span', { class: 'combo-chip-text', text: name }),
            el('button', { type: 'button', class: 'combo-chip-remove', 'aria-label': 'Убрать: ' + name, data: { remove: String(id) } }, ['×']),
          ]));
        });
        if (ids.length > collapseAt) {
          picker.chips.appendChild(el('button', {
            type: 'button', class: 'combo-chip combo-chip-toggle', 'aria-expanded': 'true', data: { toggle: '1' },
          }, ['Свернуть', el('span', { class: 'combo-chip-chevron is-up', 'aria-hidden': 'true' })]));
        }
      }
      picker.chips.hidden = !picker.chips.firstChild;
      const any = ids.length > 0 || !!state.legacyQ;
      picker.input.placeholder = any ? (ids.length >= MAX_SELECTED ? 'Выбрано ' + MAX_SELECTED + ' — максимум' : 'Добавить товар…') : 'Все товары';
      picker.clearBtn.hidden = !any;
      renderMissingNote();
    }

    function renderMissingNote() {
      const missing = state.selected.filter(isMissing);
      clear(picker.note);
      picker.note.hidden = !missing.length;
      if (!missing.length) return;
      const n = missing.length;
      picker.note.appendChild(el('span', {
        text: (n === state.selected.length && n > 1 ? 'У всех выбранных товаров' : (n === 1 ? 'У 1 выбранного товара' : 'У ' + n + ' выбранных товаров')) +
          ' нет сделок в выбранной бирже и периоде — они отмечены серым.',
      }));
      picker.note.appendChild(el('button', { type: 'button', class: 'link-btn', data: { dropMissing: '1' } }, [n === 1 ? 'Убрать его' : 'Убрать их']));
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
        showPickerMessage('Добавлено ' + room + ' из ' + toAdd.length + ' найденных: для сравнения можно выбрать не больше ' +
          MAX_SELECTED + ' товаров (взяты самые торгуемые).');
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
          notify('Убрано: ' + truncate(productName(last), 80));
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
        state.productsError = 'Не удалось загрузить список товаров: ' + (error.message || 'нет связи с сервером');
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
      price: 'средневзвешенная цена по дням, тг за единицу товара',
      change: 'изменение средневзвешенной цены к первому дню с ценой, %',
      amount: 'сумма сделок по дням, тг',
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
          ? 'Показаны ' + series.length + ' ' + plural(series.length, ['самый торгуемый товар', 'самых торгуемых товара', 'самых торгуемых товаров']) +
            ' — выберите товары для сравнения'
          : 'Нет сделок в выбранной бирже и периоде';
      } else {
        sub.textContent = 'Выбрано: ' + series.length + ' ' + plural(series.length, PRODUCTS) + ' · ' + METRIC_TEXT[state.metric];
      }
      if (top && series.length) sub.textContent += ' · ' + METRIC_TEXT[state.metric];
      hint.textContent = state.metric === 'price' && series.length > 1
        ? 'Цены разных товаров могут быть в разных единицах (тонна, кубометр, литр) — для сопоставления динамики включите «Изменение, %».'
        : (state.metric === 'amount' && series.length > 1 ? 'Столбцы товаров за один день сложены.' : '');
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
        let message = 'Нет данных для графика';
        if (!series.length) message = 'Нет сделок в выбранной бирже и периоде';
        else if (state.metric === 'amount') message = 'У выбранных товаров нет сумм сделок за выбранный период';
        else message = series.length === 1 ? 'У этого товара нет цен за выбранный период' : 'У выбранных товаров нет цен за выбранный период';
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
        data: { labels: labels.map(manyYears ? (d) => shortDate(d) + '.' + d.slice(2, 4) : shortDate), datasets },
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
            title: s.name + (noData ? '\nНет данных за выбранный период' : '') + '\nЩелчок — скрыть или показать на графике',
            data: { id: String(s.id) },
          }, [
            swatch(seriesStyle(s.id, i)),
            el('span', { class: 'legend-text', text: names[i] }),
            noData ? el('span', { class: 'legend-note', text: 'нет данных' }) : null,
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
      { key: 'name', label: 'Товар', text: true },
      { key: 'exchange', label: 'Биржа', text: true },
      { key: 'deals', label: 'Сделок' },
      { key: 'volume', label: 'Объём', optional: true },
      { key: 'amount', label: 'Сумма, тг' },
      { key: 'avg_price', label: 'Ср. взв. цена' },
      { key: 'min_price', label: 'Мин. цена' },
      { key: 'max_price', label: 'Макс. цена' },
      { key: 'first_price', label: 'Первая' },
      { key: 'last_price', label: 'Последняя' },
      { key: 'change_pct', label: 'Изменение' },
      { key: 'last_date', label: 'Посл. сделка', text: true },
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
        if (r.code) codes.push('код ' + r.code);
        if (r.tnved) codes.push('ТН ВЭД ' + r.tnved);
        const empty = !r.records;
        const cells = [
          el('td', { class: 'cell-compare-product' }, [
            el('div', { class: 'compare-name' }, [
              swatch(style),
              el('span', { class: 'cell-main', title: r.name, text: r.name }),
            ]),
            el('div', { class: 'cell-sub', text: empty ? 'нет сделок в выбранной бирже и периоде' : codes.join(' · ') }),
          ]),
          el('td', {}, (r.exchange_codes || []).length
            ? r.exchange_codes.map((code) => exchangeBadge(code, code === 'caspy' ? 'Каспий' : 'ETS'))
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
          title: isNumber(change) ? 'Последняя цена к первой за период' : 'Нужно хотя бы два дня с ценой',
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

    function render(data) {
      state.data = data;
      rememberProducts(data.selected);
      if (data.unknown_ids && data.unknown_ids.length) {
        // товары из старой ссылки, которых больше нет в базе, — убираем из выбора
        state.selected = state.selected.filter((id) => !data.unknown_ids.includes(id));
        assignSlots();
        syncUrl();
        notify('Не найдены и убраны из выбора: ' + data.unknown_ids.map((id) => '№' + id).join(', '));
      }
      renderChips();
      if (picker.open) renderList();
      renderSummary(data.summary, data.turnover_by_day);
      renderCompareChart(data);
      renderCompareTable(data);
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
        errorText.textContent = 'Не удалось загрузить аналитику: ' + (error.message || 'нет связи с сервером');
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
    if (truncated) notify('В ссылке больше ' + MAX_SELECTED + ' товаров — взяты первые ' + MAX_SELECTED + '.');
    loadProducts();
    if (typeof Chart === 'undefined') {
      errorText.textContent = 'Библиотека графиков не загружена (static/portal/vendor/chart.umd.min.js).';
      errorBox.hidden = false;
      return;
    }
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
    Chart.defaults.font.size = narrowScreen.matches ? 11.5 : 12.5;
    Chart.defaults.color = COLORS.text;
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
