/* Портал итогов торгов ETS и Каспий.
 * Страница «/»      — таблица итогов торгов (fetch к /api/trades/, без перезагрузки);
 * страница «/status/» — состояние данных и ручной запуск сбора.
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
      { key: 'delivery_place', label: 'Место поставки', firstDir: '' },
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

    function textCell(value, className) {
      const td = el('td', { class: className || '' });
      if (value) td.appendChild(el('div', { class: 'cell-main', text: value }));
      else td.appendChild(dash());
      return td;
    }

    function partyCell(name, bin) {
      const td = el('td', { class: 'cell-party' });
      if (!name) { td.appendChild(dash()); return td; }
      td.appendChild(el('div', { class: 'cell-main', text: name }));
      if (bin) td.appendChild(el('div', { class: 'cell-sub', text: 'БИН ' + bin }));
      return td;
    }

    function numCell(value, formatter, className, sub) {
      const td = el('td', { class: 'num ' + (className || '') });
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
        el('td', {}, [exchangeBadge(row.exchange, row.exchange_name)]),
        textCell(row.product, 'cell-product'),
        partyCell(row.seller, row.seller_bin),
        partyCell(row.buyer, row.buyer_bin),
        textCell(row.seller_broker, 'cell-broker'),
        textCell(row.buyer_broker, 'cell-broker'),
        numCell(row.quantity, nfQty, '', row.unit),
        numCell(row.price_avg, nfMoney),
        numCell(row.amount, nfMoney, 'amount'),
        textCell(row.delivery_place, 'cell-place'),
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

    refresh();
  }

  /* ===================================================== запуск */

  document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.dataset.page;
    if (page === 'trades') initTrades();
    else if (page === 'status') initStatus();
  });
})();
