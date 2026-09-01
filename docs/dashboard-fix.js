/* Stock Radar - Dashboard compatibility fix
   Fixes the current dashboard error:
   Cannot read properties of undefined (reading 'push')

   IMPORTANT: this file is loaded AFTER docs/index.html.
   It replaces the original renderLists/stockRow bindings safely.
*/
(function () {
  'use strict';

  function normalizeStatus(s) {
    const valid = ['ready','near','watch','flag','excluded'];
    if (valid.includes(s.status)) return s.status;
    if (s.exclusionReason) return 'excluded';
    const met = Array.isArray(s.conds) ? s.conds.reduce((a,b)=>a+(b ? 1 : 0),0) : 0;
    if (met >= 7) return 'ready';
    if (met >= 5) return 'near';
    if (met >= 3) return 'watch';
    return 'flag';
  }

  const originalStockRow = (typeof stockRow === 'function') ? stockRow : null;

  function safeStockRow(s) {
    s.status = normalizeStatus(s);
    // Data-error/insufficient rows may have no price; never let them crash the dashboard.
    if (s.price == null || !Number.isFinite(Number(s.price))) {
      const msg = s.dataError || 'بيانات الشموع غير متوفرة أو غير كافية';
      const met = Array.isArray(s.conds) ? s.conds.reduce((a,b)=>a+(b?1:0),0) : 0;
      return `<div class="stock-row" data-model="${s.model || 'split'}" onclick="openDetail('${String(s.tk||'').replace(/'/g,"\\'")}')">
        <div class="ring-wrap"><div class="ring-txt">${Math.round(met/7*100)}٪</div></div>
        <div class="stock-id"><div class="stock-tk">${s.tk || '—'}</div><div class="stock-nm">${s.nm || s.tk || '—'}</div></div>
        <div class="stock-tags"><span class="tag bad">بيانات</span><span class="tag bad">شموع</span></div>
        <div class="stock-price"><div class="price-num">—</div><div class="price-chg down">غير متاح</div></div>
        <div class="stock-status"><span class="status-pill flag"><span class="dt"></span>${msg}</span></div>
      </div>`;
    }
    try {
      return originalStockRow ? originalStockRow(s) : '';
    } catch (e) {
      console.error('Stock Radar row error:', e, s);
      return `<div class="stock-row"><div class="stock-id"><div class="stock-tk">${s.tk||'—'}</div><div class="stock-nm">تعذر عرض السهم</div></div><div class="stock-status"><span class="status-pill flag">خطأ في عرض البيانات</span></div></div>`;
    }
  }

  function safeRenderLists() {
    const stocks = (typeof STOCKS !== 'undefined' && Array.isArray(STOCKS)) ? STOCKS : [];
    stocks.forEach(s => { s.status = normalizeStatus(s); });

    const groups = {ready:[], near:[], watch:[], flag:[], excluded:[]};
    stocks.forEach(s => groups[normalizeStatus(s)].push(s));

    const set = (id, value) => { const el=document.getElementById(id); if(el) el.textContent=value; };
    set('cnt-ready', groups.ready.length); set('cnt-near', groups.near.length);
    set('cnt-watch', groups.watch.length); set('cnt-flag', groups.flag.length);
    set('s-ready', groups.ready.length); set('s-near', groups.near.length);
    set('s-watch', groups.watch.length); set('s-flag', groups.flag.length);

    const filterState = (typeof modelFilterState !== 'undefined') ? modelFilterState : {};
    const filtered = (status) => groups[status].filter(s => (filterState[status] || 'all') === 'all' || s.model === filterState[status]);
    const put = (id, arr, empty) => {
      const el=document.getElementById(id); if(!el) return;
      el.innerHTML = arr.map(safeStockRow).join('') || emptyMsg(empty);
    };

    put('list-ready-full', filtered('ready'), 'لا توجد أسهم جاهزة فنيًا ضمن هذا النموذج حاليًا');
    put('list-near-full', filtered('near'), 'لا توجد أسهم شبه جاهزة ضمن هذا النموذج حاليًا');
    put('list-watch-full', filtered('watch'), 'قائمة المتابعة فارغة ضمن هذا النموذج');
    put('list-flag-full', filtered('flag'), 'لا توجد أسهم مرصودة ضمن هذا النموذج');
    put('list-ready-preview', groups.ready.slice(0,3), 'لا توجد أسهم جاهزة فنيًا حاليًا');
    put('list-near-preview', groups.near.slice(0,3), 'لا توجد أسهم شبه جاهزة حاليًا');
    put('list-flag-preview', groups.flag.slice(0,3), 'لا توجد أسهم مرصودة جديدة');
    put('list-excluded', groups.excluded, 'لا توجد أسهم مستبعدة حاليًا');
  }

  // Replace the binding used by loadData() itself, not just window.renderLists.
  try { renderLists = safeRenderLists; } catch (e) { window.renderLists = safeRenderLists; }
  try { stockRow = safeStockRow; } catch (e) { window.stockRow = safeStockRow; }

  // In case data was already loaded before this file executed.
  [0, 100, 300, 700, 1500, 3000].forEach(ms => setTimeout(() => {
    try { safeRenderLists(); if (typeof renderLog === 'function') renderLog(); } catch (e) { console.error('Dashboard fix:', e); }
  }, ms));

  window.StockRadarDashboardFix = { safeRenderLists };
})();
