/* Reverse-split terminal. Market data is always same-origin data.json.
 * Layout adapted from OMERMASAAD/reverse-split-radar V2; analysis is separate.
 */
"use strict";
(() => {
  const ASSET_BASE = new URL(".", document.currentScript.src);
  const DATA_URL = new URL("data.json", ASSET_BASE);
  const REFRESH_MS = 60000;
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const valid = v => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));
  const n = (v, d = 2) => valid(v) ? Number(v).toLocaleString("en-US", {minimumFractionDigits:d, maximumFractionDigits:d}) : "—";
  const i0 = v => n(v, 0);
  const money = v => valid(v) ? "$" + n(v, Number(v) < 1 ? 4 : 2) : "—";
  const signed = (v, d = 3) => valid(v) ? (v > 0 ? "+" : "") + n(v, d) : "—";
  const pct = v => valid(v) ? signed(v, 1) + "%" : "—";
  const unit = (v, suffix, d = 1) => valid(v) ? n(v, d) + suffix : "—";
  const color = v => valid(v) ? (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "mut") : "dim";
  function compact(v) {
    if (!valid(v)) return "—";
    const x = Math.abs(v);
    return x >= 1e9 ? n(v / 1e9) + "B" : x >= 1e6 ? n(v / 1e6) + "M" : x >= 1e3 ? n(v / 1e3, 1) + "K" : i0(v);
  }
  const STAGES = {READY:"جاهز فنيًا", SEMI:"شبه جاهز", WATCH:"قيد المتابعة", MOVED:"انطلق بالفعل"};
  const DEFAULT_CONDITIONS = [
    ["drop", "هبوط قوي بعد التقسيم", 10], ["rsi", "RSI لمس التشبع البيعي", 10],
    ["hold5", "ثبات الدعم 5 جلسات", 15], ["res", "اختبار أقرب مقاومة", 10],
    ["retest", "إعادة اختبار الدعم / قاع أعلى", 15], ["hold_after", "ثبات بعد الاختبار", 10],
    ["neck", "اختراق خط العنق", 10], ["ema", "استعادة EMA20 وVWAP", 10], ["news", "مراجعة التحذيرات الإخبارية", 10]
  ].map(([key,label,weight]) => ({key,label,weight,core:true}));
  let D = null, items = [], activeTab = "radar", selected = null, lastFocus = null;
  let fetching = false, offline = false, loadError = false, auto = true, nextRefresh = Date.now() + REFRESH_MS;
  let pane = "cmf", timeframe = "1D", baseZoom = false, overlays = true, hoverIndex = null, chartObserver = null, drawFrame = null;
  const filters = {stage:"all", liquidity:"all", score:0, dormant:false, sweep:false, sort:"default", ascending:false};
  try { auto = localStorage.getItem("radar-auto-refresh") !== "off"; } catch (_) { /* Private browsing may block storage. */ }

  function fmtDate(iso, withTime = true) {
    if (!iso || Number.isNaN(new Date(iso).getTime())) return "—";
    return new Date(iso).toLocaleString("ar-SA-u-ca-gregory-nu-latn", {timeZone:"Asia/Riyadh", year:"numeric",month:"short",day:"numeric", ...(withTime ? {hour:"2-digit",minute:"2-digit"} : {})});
  }
  function toast(message) {
    const t = $("toast"); t.textContent = message; t.classList.add("on");
    clearTimeout(t._timer); t._timer = setTimeout(() => t.classList.remove("on"), 3500);
  }
  const stateClass = state => ({READY:"ready",SEMI:"semi",WATCH:"watch",MOVED:"moved"}[state] || "watch");
  const stageBadge = x => `<span class="badge b-${stateClass(x.state)}">${esc(x.stage)}</span>`;
  function divBadge(x) {
    const d = x.liquidity.divergence;
    const type = d.direction === "positive" ? "b-ready" : d.direction === "negative" ? "b-weakened" : d.type === "unavailable" ? "b-wait" : "b-neutral";
    const text = d.type === "stealth_accumulation" ? "تجميع خفي" : d.direction === "positive" ? "↗ انحراف إيجابي" : d.direction === "negative" ? "↘ انحراف سلبي" : d.type === "unavailable" ? "بانتظار الحجم" : "لا انحراف";
    return `<span class="badge ${type}" title="${esc(d.summary || d.direction_ar)}">${text}</span>`;
  }
  function grade(x) {
    return `<span class="grade g-${esc(x.grade || "D")}" title="تصنيف مركب${x.grade_provisional ? ' مبدئي بسبب نقص بيانات التأكيد' : ''}">${esc(x.grade || "—")}${x.grade_provisional ? "*" : ""}</span>`;
  }
  function meter(score, accent = "var(--cyan)") {
    return `<div class="meter"><i style="width:${Math.max(0,Math.min(100,Number(score) || 0))}%;background:${accent}"></i></div>`;
  }
  function spark(x) {
    const bars = (x.chart || []).slice(-30), a = bars.map(b => b.close).filter(valid);
    if (a.length < 2) return '<span class="dim">—</span>';
    const min = Math.min(...a), max = Math.max(...a), span = max - min || 1;
    const path = a.map((v,i) => `${i ? "L" : "M"}${(i/(a.length-1)*80+1).toFixed(1)},${(25-(v-min)/span*23).toFixed(1)}`).join(" ");
    return `<svg class="spark ${a[a.length-1]>=a[0] ? 'pos-spark' : 'neg-spark'}" viewBox="0 0 82 27" aria-hidden="true"><path d="${path}"/></svg>`;
  }
  function miniChecks(x) {
    return `<div class="checks-mini" aria-label="${(x.checklist || []).filter(c=>c.status).length} شروط متحققة">${(x.checklist || []).map(c=>`<i class="cdot${c.status ? ' on' : ''}" title="${esc(c.rule)}: ${c.status ? 'متحقق' : 'ناقص'}" aria-hidden="true"></i>`).join("")}</div>`;
  }
  function normalize(raw) {
    const stage = raw.stage || raw.state_label || STAGES[raw.state] || STAGES.WATCH;
    const state = raw.state || Object.keys(STAGES).find(k => STAGES[k] === stage) || "WATCH";
    const l = raw.liquidity || {};
    const cmf = l.cmf ?? raw.indicators?.cmf ?? null;
    const d = l.divergence || raw.liquidity_divergence || {type:valid(cmf) ? "none" : "unavailable", summary:valid(cmf) ? "لا انحراف محفوظ" : "الحجم غير متاح في اللقطة الحالية."};
    return {...raw, stage, state, chart:raw.chart || [], chart_4h:raw.chart_4h || [], checklist:raw.checklist || [],
      liquidity:{...l, cmf, available:valid(cmf), divergence:d, accumulation_score:l.accumulation_score ?? null},
      dormant_base:raw.dormant_base || {}, indicators:raw.indicators || {}, risk:raw.risk || {}};
  }
  function summary() {
    const counts = {}; Object.keys(STAGES).forEach(k => counts[k] = items.filter(x => x.state === k).length);
    return {...counts, total:items.length, available:items.filter(x=>x.liquidity.available).length,
      dormant:items.filter(x=>x.dormant_base.is_dormant).length, accum:items.filter(x=>x.liquidity.accumulation_detected).length,
      positive:items.filter(x=>x.liquidity.divergence.direction === "positive").length,
      negative:items.filter(x=>x.liquidity.divergence.direction === "negative").length,
      sweep:items.filter(x=>x.liquidity_sweep).length};
  }
  function applyData(raw) {
    if (!raw || !Array.isArray(raw.signals ?? raw.items)) throw new Error("Invalid radar data schema");
    D = raw; items = (raw.signals || raw.items).map(normalize); loadError = false;
    renderAll();
  }
  async function load(manual = false) {
    if (fetching) return;
    fetching = true; $("refreshBtn").disabled = true; $("refreshBtn").textContent = "جارٍ التحديث…";
    try {
      const url = new URL(DATA_URL); url.searchParams.set("t",Date.now());
      const response = await fetch(url, {cache:"no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      offline = response.headers?.get("X-Radar-Offline") === "true";
      applyData(await response.json());
      if (manual) toast(offline ? "تُعرض آخر لقطة محفوظة دون اتصال؛ لم يحدث مسح جديد." : "تمت إعادة قراءة النتائج. تشغيل مسح جديد يتم من GitHub Actions.");
    } catch (error) {
      loadError = true; renderBanner(); updateConnection();
      if (!D) $("gridEmpty").innerHTML = '<div class="empty"><b>تعذّر تحميل نتائج الرادار</b>تحقق من وجود data.json أو شغّل المسح من GitHub Actions.<br><button class="btn retry-load">إعادة المحاولة</button></div>';
      if (manual) toast("تعذّر التحديث؛ احتُفظ بالنتائج السابقة وتوقيتها.");
      console.warn("Radar data:", error.message);
    } finally {
      fetching = false; nextRefresh = Date.now() + REFRESH_MS;
      $("refreshBtn").disabled = false; $("refreshBtn").textContent = "تحديث النتائج";
    }
  }
  function updateConnection() {
    const old = !D?.updated_at || Date.now() - new Date(D.updated_at).getTime() > 24*3600*1000;
    $("livePill").querySelector(".dot").className = "dot" + (loadError ? " off" : offline || old || !D ? " idle" : "");
    $("liveText").textContent = loadError ? "تعذّر تحديث الملف" : offline ? "لقطة محفوظة · دون اتصال" : !D ? "جارٍ تحميل اللقطة…" : old ? "لقطة قديمة · غير لحظية" : "لقطة محفوظة · غير لحظية";
  }
  function renderBanner() {
    const boxes = [];
    if (loadError) boxes.push('<div class="banner error">تعذّر الوصول إلى ملف النتائج. البيانات الظاهرة هي آخر لقطة ناجحة، لا قراءات جديدة.</div>');
    if (D?.demo) boxes.push('<div class="banner">بيانات اصطناعية تعليمية فقط — ليست أسعارًا أو إشارات سوق حقيقية.</div>');
    if (offline) boxes.push('<div class="banner">وضع دون اتصال — يتم عرض لقطة مخزنة مع وقتها الأصلي.</div>');
    if (D && !D.updated_at) boxes.push('<div class="banner">وقت مسح السوق غير متاح؛ لا يمكن تأكيد حداثة هذه النتائج.</div>');
    if (D?.updated_at && Date.now() - new Date(D.updated_at).getTime() > 24*3600*1000) boxes.push('<div class="banner">هذه اللقطة أقدم من 24 ساعة. لا تستخدم الأسعار أو مستويات الدخول دون تحديث مستقل.</div>');
    if (D && summary().available < items.length) {
      boxes.push(`<div class="banner info"><span>بيانات السيولة غير مكتملة لـ <b class="num">${i0(items.length-summary().available)}</b> سهم. يلزم حجم فعلي وتاريخ كافٍ؛ لن تُحسب CMF أو الانحراف بأحجام تقديرية. تشغيل المسح بعد نشر النسخة الجديدة يجلب OHLCV الفعلية.</span><a class="btn" href="https://github.com/omermasaad75-alt/stock-radar/actions/workflows/scan.yml" target="_blank" rel="noopener noreferrer">تشغيل المسح ↗</a></div>`);
    }
    $("bannerBox").innerHTML = boxes.join("");
  }
  function renderAll() {
    renderBanner(); updateConnection(); renderKpis(); renderTape(); renderFunnel(); renderFilters(); renderGrid();
    renderLiquidity(); renderStats(); renderRules(); renderOps();
    $("cntRadar").textContent = i0(items.length);
    const dates = items.flatMap(x=>x.chart.slice(-1).map(b=>b.date)).sort();
    $("snapshotMeta").innerHTML = `آخر مسح <b>${esc(fmtDate(D.updated_at))}</b> · الرياض<br>آخر شمعة يومية <span class="num">${esc(dates.at(-1)?.slice(0,10) || "—")}</span>`;
    if (selected) {
      const fresh = items.find(x=>x.ticker === selected);
      if (fresh) openDrawer(fresh, true); else { closeDrawer(); toast("لم يعد السهم ضمن اللقطة الجديدة."); }
    }
  }
  function renderKpis() {
    const s = summary(), diag = D.diagnostics || {};
    const pendingHint = s.available ? `من ${s.available} قراءات متاحة` : "بانتظار الحجم الفعلي";
    const cards = [
      ["الكون المرصود", i0(diag.symbols_total), "NASDAQ + NYSE", "var(--cyan)", null],
      ["تحت الرادار", i0(s.total), "تقسيم عكسي · دعم", "var(--violet)", "all"],
      ["جاهز فنيًا", i0(s.READY), "اختراق + إعادة اختبار", "var(--green)", "READY"],
      ["شبه جاهز", i0(s.SEMI), "ينتظر اكتمال الشروط", "var(--amber)", "SEMI"],
      ["ارتكاز خامل", i0(s.dormant), "قاعدة ضيقة قرب الدعم", "var(--cyan)", "dormant"],
      ["كشف التجميع", s.available ? i0(s.accum) : "—", pendingHint, "var(--green)", "accum"],
      ["انحراف إيجابي", s.available ? i0(s.positive) : "—", pendingHint, "var(--green)", "positive"],
      ["انحراف سلبي", s.available ? i0(s.negative) : "—", pendingHint, "var(--red)", "negative"],
    ];
    $("kpis").innerHTML = cards.map(([label,v,hint,accent,action]) => `${action ? `<button type="button" data-quick="${action}"` : '<div'} class="kpi" style="--accent:${accent}"><div class="k">${label}</div><div class="v">${v}</div><div class="m">${esc(hint)}</div>${action ? '</button>' : '</div>'}`).join("");
  }
  function renderTape() {
    const bits = items.map(x=>`<span class="tape-item"><b class="num">${esc(x.ticker)}</b><span class="num">${money(x.price)}</span><span class="num neg">−${n(x.max_drawdown_pct,1)}%</span><span>${esc(x.stage)}</span><span class="num ${color(x.liquidity.cmf)}">CMF ${signed(x.liquidity.cmf)}</span></span>`).join("");
    $("tape").innerHTML = bits ? bits + bits : '<span class="tape-item">لا توجد أسهم مستوفية للبوابات في اللقطة الحالية.</span>';
  }
  function renderFunnel() {
    const d = D.diagnostics || {};
    const steps = [[d.symbols_total,"الكون"],[d.split_window_pass,"تقسيم 20–50 يومًا"],[d.rise_ok_pass,"صعود ≤ 20%"],[d.drop_rsi_pass,"هبوط + تشبع بيعي"],[items.length,"تحت المتابعة"]];
    const max = Math.max(1,...steps.map(([v])=>v || 0));
    $("funnel").innerHTML = steps.map(([v,label],j)=>`<div class="fstep ${j===4 ? 'hot' : ''}"><div class="fn">${i0(v)}</div><div class="fbar" style="height:${valid(v) ? Math.max(4,Math.round(Math.log1p(v)/Math.log1p(max)*42)) : 3}px"></div><div class="fl">${label}</div></div>`).join("");
    $("funnelHint").textContent = `آخر مسح · التغطية ${unit(d.data_coverage_pct,"%",0)} · ارتفاع الأعمدة بمقياس لوغاريتمي`;
  }
  function renderFilters() {
    const s = summary();
    $("stateChips").innerHTML = [["all","الكل",items.length],...Object.entries(STAGES).map(([k,label])=>[k,label,s[k]])].map(([key,label,count])=>`<button class="chip ${filters.stage===key ? 'on' : ''}" data-state="${key}" aria-pressed="${filters.stage===key}">${label}<span class="n">${i0(count)}</span></button>`).join("");
    $("liquidityChips").innerHTML = [["all","كل السيولة",null],["accum","تجميع CMF",s.available ? s.accum : null],["positive","↗ إيجابي",s.available ? s.positive : null],["negative","↘ سلبي",s.available ? s.negative : null],["unavailable","بيانات ناقصة",items.length-s.available]].map(([key,label,count])=>`<button class="chip ${key} ${filters.liquidity===key ? 'on' : ''}" data-liquidity="${key}" aria-pressed="${filters.liquidity===key}">${label}${key!=="all" ? `<span class="n">${i0(count)}</span>` : ''}</button>`).join("");
    for (const [id,key] of [["onlyDormant","dormant"],["onlySweep","sweep"]]) { $(id).classList.toggle("on",filters[key]); $(id).setAttribute("aria-pressed",filters[key]); }
  }
  function sortValue(x, key) {
    return ({score:x.readiness_score,cmf:x.liquidity.cmf,accum:x.liquidity.accumulation_score,dorm:x.dormant_base.dormancy_score,
      hold:x.support_hold_sessions,drop:x.max_drawdown_pct,rr:x.risk.rr_t1,price:x.price,float:x.float,split:x.split?.days_since})[key];
  }
  function filtered() {
    const query = $("q").value.trim().toLowerCase();
    let result = items.filter(x => (!query || `${x.ticker} ${x.company || ''}`.toLowerCase().includes(query)) &&
      (filters.stage==="all" || x.state===filters.stage) && (Number(x.readiness_score) || 0) >= filters.score &&
      (!filters.dormant || x.dormant_base.is_dormant) && (!filters.sweep || x.liquidity_sweep) &&
      (filters.liquidity==="all" || filters.liquidity==="accum" && x.liquidity.accumulation_detected ||
       filters.liquidity==="positive" && x.liquidity.divergence.direction==="positive" ||
       filters.liquidity==="negative" && x.liquidity.divergence.direction==="negative" ||
       filters.liquidity==="unavailable" && !x.liquidity.available));
    const key = filters.sort === "default" ? "score" : filters.sort;
    result = result.slice().sort((a,b)=> {
      const av=sortValue(a,key), bv=sortValue(b,key);
      if (!valid(av) && valid(bv)) return 1;
      if (valid(av) && !valid(bv)) return -1;
      const primary = (Number(bv)||0)-(Number(av)||0);
      return primary ? primary*(filters.ascending ? -1 : 1) : (b.confirm_score || 0)-(a.confirm_score || 0) || a.ticker.localeCompare(b.ticker);
    });
    return result;
  }
  const columns = [["السهم",null],["السعر","price"],["المرحلة",null],["الجاهزية / التصنيف","score"],["CMF (20)","cmf"],["MFI (14)",null],["التجميع / 100","accum"],["انحراف السيولة",null],["الارتكاز","hold"],["الدعم",null],["Float","float"],["منذ التقسيم","split"],["أقصى هبوط","drop"],["النمط",null],["آخر 30 جلسة",null]];
  function renderGrid() {
    const rows = filtered();
    $("gridHead").innerHTML = columns.map(([label,key])=>`<th scope="col"${key ? ` aria-sort="${filters.sort===key || filters.sort==='default' && key==='score' ? (filters.ascending ? 'ascending' : 'descending') : 'none'}"` : ''}>${key ? `<button data-sort="${key}">${label} <span class="ar">${(filters.sort===key || filters.sort==='default' && key==='score') ? (filters.ascending ? '↑' : '↓') : '↕'}</span></button>` : label}</th>`).join("");
    $("gridBody").innerHTML = rows.map(x => {
      const l=x.liquidity, b=x.dormant_base;
      return `<tr data-ticker="${esc(x.ticker)}"${x.ticker===selected ? ' class="sel"' : ''}>
        <td><button class="ticker-btn" data-open="${esc(x.ticker)}" aria-label="فتح تحليل ${esc(x.ticker)}"><span class="tick">${esc(x.ticker)}</span><div class="co">${esc(x.company || "اسم الشركة غير متاح")}</div></button>${miniChecks(x)}</td>
        <td class="num">${money(x.price)}</td><td>${stageBadge(x)}${x.has_warning ? '<div class="small-line neg">تحذير إخباري</div>' : ''}</td>
        <td><div class="score-cell">${grade(x)}<div><span class="num">${i0(x.readiness_score)}<span class="dim"> /100</span></span>${meter(x.readiness_score)}</div></div></td>
        <td><div class="num ${color(l.cmf)}">${signed(l.cmf)}</div><div class="small-line num">${valid(l.cmf_change) ? 'Δ5 '+signed(l.cmf_change) : 'حجم غير متاح'}</div></td>
        <td class="num">${n(l.mfi,1)}</td><td><span class="num">${i0(l.accumulation_score)}</span>${valid(l.accumulation_score) ? meter(l.accumulation_score,'var(--green)') : '<div class="small-line">غير محسوب</div>'}</td>
        <td>${divBadge(x)}</td><td><span class="num">${i0(x.support_hold_sessions)}</span> <span class="dim">جلسات</span><div class="small-line ${b.is_dormant ? 'pos' : ''}">${b.is_dormant ? 'ارتكاز خامل' : 'قيد التشكّل'}</div></td>
        <td class="num">${money(x.support)}</td><td class="num" title="${x.float_exact ? 'فلوت Yahoo الفعلي' : 'بديل تقريبي من الأسهم القائمة عند غياب الفلوت'}">${!x.float_exact && valid(x.float) ? '≈ ' : ''}${compact(x.float)}</td>
        <td><span class="num">${i0(x.split?.days_since)}</span> <span class="dim">يوم</span><div class="small-line num">${esc(x.split?.ratio || '')}</div></td><td class="num neg">${valid(x.max_drawdown_pct) ? '−'+n(x.max_drawdown_pct,1)+'%' : '—'}</td><td class="dim">${esc(x.pattern_type || '—')}</td><td>${spark(x)}</td></tr>`;
    }).join("");
    $("cardList").innerHTML = rows.map(x=>`<button class="stock-card ${stateClass(x.state)}" data-open="${esc(x.ticker)}" aria-label="فتح تحليل ${esc(x.ticker)}"><div class="stock-head"><div><div class="tick">${esc(x.ticker)}</div><div class="co">${esc(x.company || 'اسم الشركة غير متاح')}</div></div><div class="num price">${money(x.price)}</div></div><div class="card-metrics"><div><span>الجاهزية</span><b class="num">${i0(x.readiness_score)} / 100</b>${meter(x.readiness_score)}</div><div><span>CMF (20)</span><b class="num ${color(x.liquidity.cmf)}">${signed(x.liquidity.cmf)}</b></div><div><span>الدعم</span><b class="num">${money(x.support)}</b></div><div><span>ثبات الدعم</span><b class="num">${i0(x.support_hold_sessions)}</b> جلسات</div></div><div class="card-tags">${stageBadge(x)}${divBadge(x)}${x.dormant_base.is_dormant ? '<span class="badge b-triggered">ارتكاز خامل</span>' : ''}</div></button>`).join("");
    $("resultCount").textContent = `${rows.length} / ${items.length} سهم · الجاهزية من الشروط الأصلية`;
    $("gridEmpty").innerHTML = rows.length ? "" : `<div class="empty"><b>${items.length ? 'لا نتائج لهذا الفلتر' : 'لا أسهم تحت الرصد'}</b>${filters.liquidity==='positive' || filters.liquidity==='negative' || filters.liquidity==='accum' ? 'تُعرض إشارات السيولة من القراءات الفعلية المتاحة فقط. عدم توفر الحجم ليس إشارة حياد.' : 'جرّب إزالة الفلاتر أو تحديث نتائج المسح.'}<br><button class="btn reset-filters">إزالة الفلاتر</button></div>`;
  }
  function setTab(key) {
    activeTab=key;
    document.querySelectorAll('[data-tab]').forEach(b=> {b.classList.toggle('active',b.dataset.tab===key); if(b.dataset.tab===key) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current');});
    ["radar","liquidity","stats","rules","ops"].forEach(t=>$("tab-"+t).hidden=t!==key);
  }
  function resetFilters() {
    Object.assign(filters,{stage:"all",liquidity:"all",score:0,dormant:false,sweep:false,sort:"default",ascending:false});
    $("q").value=""; $("minScore").value=0; $("minScoreVal").textContent="0"; $("sortBy").value="default";
    renderFilters(); renderGrid();
  }
  function quickFilter(key) {
    resetFilters(); setTab("radar");
    if (key in STAGES) filters.stage=key;
    else if(key==="dormant") filters.dormant=true;
    else if(["accum","positive","negative"].includes(key)) filters.liquidity=key;
    renderFilters(); renderGrid();
  }

  function metric(label,text,hint="",textual=false) {
    return `<div class="metric"><div class="k">${esc(label)}</div><div class="v ${textual ? 'text-val' : 'num'}">${esc(text)}</div>${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</div>`;
  }
  function barsHTML(rows, total, accent="var(--cyan)") {
    return rows.map(([label,count])=>`<div class="bar-row"><span class="lab" title="${esc(label)}">${esc(label)}</span><span class="track"><i style="width:${total ? Math.min(100,Math.round(count/total*100)) : 0}%;background:${accent}"></i></span><span class="val num">${i0(count)} / ${i0(total)}</span></div>`).join("");
  }
  function flowList(direction) {
    const list=items.filter(x=>x.liquidity.divergence.direction===direction);
    return list.length ? list.map(x=>`<button class="flow-list-btn" data-open="${esc(x.ticker)}"><div><b class="tick">${esc(x.ticker)}</b><div class="flow-summary">${esc(x.liquidity.divergence.direction_ar)} · ${esc(x.liquidity.divergence.strength || '')}</div></div><span class="num ${color(x.liquidity.cmf)}">CMF ${signed(x.liquidity.cmf)}</span></button>`).join("") : `<div class="empty"><b>لا إشارات محفوظة</b>${summary().available ? 'لا انحراف مستوفٍ للشروط في القراءات المتاحة.' : 'حجم التداول غير متاح حاليًا. تظهر النتائج هنا بعد مسح OHLCV جديد.'}</div>`;
  }
  function renderLiquidity() {
    const s=summary(), values=items.map(x=>x.liquidity.cmf).filter(valid);
    const avg=values.length ? values.reduce((a,b)=>a+b,0)/values.length : null;
    $("liquidityKpis").innerHTML = metric("قراءات السيولة المتاحة",`${s.available} / ${s.total}`,"CMF(20) من أحجام فعلية")+metric("متوسط CMF(20)",signed(avg),"المتوسط لا يشمل البيانات الناقصة")+metric("انحراف إيجابي",s.available ? i0(s.positive) : '—',"يشمل التجميع الخفي")+metric("انحراف سلبي",s.available ? i0(s.negative) : '—',"مقارنة قمتين مؤكدتين");
    $("positiveList").innerHTML=flowList("positive"); $("negativeList").innerHTML=flowList("negative");
  }
  function renderStats() {
    const s=summary();
    const avg=items.length ? items.reduce((a,x)=>a+Number(x.readiness_score || 0),0)/items.length : null;
    $("statCards").innerHTML=metric("متوسط الجاهزية",n(avg,1),"من 100 · الشروط الأصلية")+metric("قواعد خاملة",i0(s.dormant),"ثبات + نطاق ضيق")+metric("سحب سيولة",i0(s.sweep),"كسر بذيل الشمعة وإغلاق فوق الدعم")+metric("قراءات حجم ناقصة",i0(s.total-s.available),"لا تُعامل كسيولة صفرية");
    $("stateBars").innerHTML=barsHTML(Object.entries(STAGES).map(([k,v])=>[v,s[k]]),s.total);
    $("flowBars").innerHTML=s.available ? barsHTML([["CMF موجب > 0.05",items.filter(x=>x.liquidity.cmf > .05).length],["CMF سالب < −0.05",items.filter(x=>valid(x.liquidity.cmf) && x.liquidity.cmf < -.05).length],["CMF محايد",items.filter(x=>valid(x.liquidity.cmf) && Math.abs(x.liquidity.cmf)<=.05).length]],s.available) : '<div class="empty">لا توجد أحجام فعلية كافية لحساب توزيع السيولة.</div>';
    const conds=(D.conditions || DEFAULT_CONDITIONS).filter(c=>c.core);
    $("conditionBars").innerHTML=barsHTML(conds.map(c=>[c.label,items.filter(x=>x.checklist.some(k=>k.key===c.key && k.status)).length]),s.total,"var(--green)");
    $("gradeBars").innerHTML=barsHTML(["A+","A","B","C","D"].map(g=>[g,items.filter(x=>x.grade===g).length]),s.total,"var(--violet)")+`<p class="note">${items.filter(x=>x.grade_provisional).length} تصنيف مبدئي بسبب نقص بيانات التأكيد.</p>`;
  }
  function renderRules() {
    const conditions=D.conditions || DEFAULT_CONDITIONS;
    const row=c=>`<div class="check-row"><div class="ic">${c.core ? '◆' : '+'}</div><div class="lb">${esc(c.label)}</div><div class="wt">${i0(c.weight)} نقطة</div></div>`;
    $("coreRules").innerHTML=conditions.filter(c=>c.core).map(row).join("")+ '<p class="note">تظل المرحلة مبنية على الشروط الأصلية. 80 نقطة وحدها لا تكفي لإعلان الجاهزية دون اختبار الدعم وخط العنق والأخبار.</p>';
    $("extraRules").innerHTML=conditions.filter(c=>!c.core).map(row).join("")+ '<p class="note">المؤشر غير المتاح حالة مستقلة عن الشرط غير المتحقق. لا تُمنح نقاط التأكيد الناقصة، ولا يُرفع التصنيف بتطبيعها إلى 100.</p>';
    const r=D.rules || {};
    const rows=[["عمر التقسيم",(r.split_window_days || [20,50]).join('–')+' يومًا'],["أقصى صعود منذ التقسيم",unit(r.max_rise_from_split_pct ?? 20,'%',0)],["أدنى هبوط قوي",unit(r.min_drop_pct ?? 35,'%',0)],["ثبات الدعم",i0(r.support_hold_sessions ?? 5)+' جلسات'],["ارتداد إلى المقاومة",unit(r.resistance_bounce_pct ?? 15,'%',0)],["تشبع RSI",'أقل من '+i0(r.rsi_oversold ?? 30)],["بُعد خط العنق عن الدعم",unit(r.neckline_max_dist_pct ?? 30,'%',0)],["استبعاد من انطلق بالفعل",unit(r.exclude_run_from_support_pct ?? 70,'%',0)],["الفريمات",'يومي + 4 ساعات'],["الفترات",'CMF 20 / 10 · MFI 14']];
    $("thresholds").innerHTML=rows.map(([k,v])=>`<div class="lvl"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
  }
  const REASONS={rose_over_20:"صعود تجاوز 20%",no_strong_drop:"الهبوط غير كافٍ",rsi_not_oversold:"لا تشبع بيعي",target_achieved:"حقق الانطلاق المطلوب",support_broken:"الجاهزية دون الحد",split_out_of_window:"التقسيم خارج النافذة",no_data:"بيانات غير كافية",error:"خطأ في التقييم",unknown:"سبب غير محدد"};
  function renderOps() {
    const d=D.diagnostics || {}, s=summary();
    $("opsKpis").innerHTML=[["تغطية البيانات",unit(d.data_coverage_pct,'%',0),"var(--cyan)"],["مرشح تقسيم",i0(d.split_window_pass),"var(--violet)"],["حجم صالح للسيولة",i0(s.available),"var(--green)"],["قراءات ناقصة",i0(s.total-s.available),"var(--amber)"]].map(([k,v,a])=>`<div class="kpi" style="--accent:${a}"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    $("scanInfo").innerHTML=`<div class="lvl"><span>وقت مسح السوق · الرياض</span><b>${esc(fmtDate(D.updated_at))}</b></div><div class="lvl"><span>إعادة حساب التحليل (لا تغيّر عمر الأسعار)</span><b>${esc(fmtDate(D.analysis?.recalculated_at))}</b></div><div class="lvl"><span>مصدر البيانات</span><b>Yahoo Finance · ملف محفوظ</b></div><p class="note">التحديث هنا يعيد قراءة data.json كل 60 ثانية فقط. المسح الخلفي مجدول على GitHub Actions ويخضع لتأخير المنصة وفحص حداثة آخر نتيجة. لا يوجد سجل أرباح أو تاريخ تشغيل مفصل ضمن هذا الملف.</p>`;
    const reasons=Object.entries(d.reject_reasons || {}).sort((a,b)=>b[1]-a[1]);
    $("rejectBars").innerHTML=reasons.length ? barsHTML(reasons.map(([k,v])=>[REASONS[k] || k,v]),Math.max(...reasons.map(([,v])=>v)),"var(--red)") : '<div class="empty">لا تشخيص استبعاد محفوظ.</div>';
    $("qualityList").innerHTML=`<div class="lvl"><span>CMF قابل للحساب</span><b class="num">${s.available} / ${s.total}</b></div><div class="lvl"><span>شموع 4 ساعات محفوظة</span><b class="num">${items.filter(x=>x.chart_4h.length).length} / ${s.total}</b></div><p class="note">لا يمكن إعادة بناء حجم كل جلسة من متوسط الحجم أو الفلوت أو شموع OHLC. القيم الناقصة تظل غير متاحة حتى تتوفر بيانات موثوقة، ولا تُعرض قراءات 4H تخمينية.</p>`;
    const near=d.near_misses || [];
    $("nearMisses").innerHTML=near.length ? `<div class="table-wrap"><table style="min-width:520px"><thead><tr><th>الرمز</th><th>السعر</th><th>منذ التقسيم</th><th>سبب الاستبعاد</th></tr></thead><tbody>${near.map(x=>`<tr><td class="tick">${esc(x.ticker)}</td><td class="num">${money(x.price)}</td><td class="num">${i0(x.days_since_split)} يوم</td><td>${esc(REASONS[x.reason] || x.reason)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">لا قائمة قريبة من الشروط محفوظة.</div>';
  }

  function checkRows(x, core) {
    const conditions=(D.conditions || DEFAULT_CONDITIONS).filter(c=>Boolean(c.core)===core);
    return conditions.map(c=> {
      const old=x.checklist.find(k=>k.key===c.key);
      const status=core ? old?.status : x.confirmations?.[c.key];
      const unknown=status===null || status===undefined;
      const state=unknown ? "غير متاح" : status ? "متحقق" : "لم يتحقق";
      const hint=core ? old?.note || "" : c.key==="cmf_accum" ? `CMF(20) ${signed(x.liquidity.cmf)} · Δ5 ${signed(x.liquidity.cmf_change)}` : c.key==="liq_divergence" ? x.liquidity.divergence.summary : c.key==="mfi_flow" ? `MFI ${n(x.liquidity.mfi,1)} · صاعد / هابط ${unit(x.liquidity.up_down_vol_ratio,'×',2)}` : c.key==="dormant_tight" ? x.dormant_base.dormancy_label : c.key==="mtf_4h" ? `CMF 4H ${signed(x.liquidity.cmf_4h)} · RSI 4H ${n(x.indicators.rsi_4h,1)}` : x.sweep ? `سحب ${x.sweep.tf} · ${x.sweep.date}` : "لم يُرصد كسر بذيل الشمعة مع إغلاق فوق الدعم";
      return `<div class="check-row ${unknown ? 'unknown' : status ? 'ok' : ''}"><div class="ic" title="${state}">${unknown ? '—' : status ? '✓' : '○'}</div><div style="flex:1"><div class="lb">${esc(c.label)}</div><div class="dt">${esc(unknown ? 'بانتظار البيانات · ' + (hint || '') : hint)}</div></div><div class="wt">${i0(c.weight)} نقطة</div></div>`;
    }).join("");
  }
  function safeUrl(input) {
    try { const u=new URL(input); return /^https?:$/.test(u.protocol) ? u.href : null; } catch (_) { return null; }
  }
  function newsHTML(x) {
    const news=x.news || {}, list=[...(news.warnings || []).map(v=>({...v,warning:true})),...(news.catalysts || [])];
    if(!list.length) return '<p class="note">لا تحذير أو محفز محفوظ في نافذة الأخبار التي فحصها المسح. هذا ليس ضمانًا لخلو الشركة من الأخبار السلبية أو التخفيف.</p>';
    return list.map(v=>`<div class="news-item ${v.warning ? 'warning' : ''}"><b class="${v.warning ? 'neg' : 'pos'}">${esc(v.type)}</b>${safeUrl(v.url) ? `<a href="${esc(safeUrl(v.url))}" target="_blank" rel="noopener noreferrer">${esc(v.title)} ↗</a>` : `<div>${esc(v.title)}</div>`}<div class="meta">${esc(v.date)} · ${esc(v.source)}${valid(v.days_until) ? ' · بعد '+i0(v.days_until)+' يوم' : ''}</div></div>`).join("");
  }
  function openDrawer(input, keep=false) {
    const x=typeof input==='string' ? items.find(v=>v.ticker===input) : input;
    if(!x) return;
    const scroll=keep ? $("dbody").scrollTop : 0;
    const oldFocus=keep && $("drawer").contains(document.activeElement) ? {id:document.activeElement.id,pane:document.activeElement.dataset.pane,timeframe:document.activeElement.dataset.timeframe} : null;
    if(!keep && !selected) lastFocus=document.activeElement;
    if(!keep) { pane="cmf"; timeframe="1D"; baseZoom=false; hoverIndex=null; overlays=true; }
    selected=x.ticker;
    if(timeframe==='4H' && !x.chart_4h.length) timeframe='1D';
    const l=x.liquidity, b=x.dormant_base, d=l.divergence, r=x.risk;
    const dclass=d.direction==='positive' ? 'positive' : d.direction==='negative' ? 'negative' : '';
    $("dhead").innerHTML=`<div style="flex:1;min-width:0"><div style="display:flex;gap:9px;align-items:center;flex-wrap:wrap"><h2 id="dTitle" class="big" style="margin:0">${esc(x.ticker)}</h2>${stageBadge(x)}<span class="rs-tag">RS ${esc(x.split?.ratio || '—')}</span></div><div class="co">${esc(x.company || 'اسم الشركة غير متاح')}</div><div class="d-summary"><span class="num price">${money(x.price)}</span><span class="num neg">${valid(x.max_drawdown_pct) ? '−'+n(x.max_drawdown_pct,1)+'% من القمة' : '—'}</span><span class="small-line">آخر يومي <b class="num">${esc(x.chart.at(-1)?.date?.slice(0,10) || '—')}</b></span></div></div><div>${grade(x)}<div class="small-line num">${i0(x.composite_score)} /100</div></div><div class="drawer-actions"><button class="btn theme-toggle" id="drawerThemeBtn" type="button" data-theme-toggle aria-label="الوضع الفاتح" aria-pressed="false" title="التبديل إلى الوضع الفاتح"><span class="theme-icon" aria-hidden="true"></span><span class="theme-label">داكن</span></button><button class="xbtn" id="closeDrawerBtn" aria-label="إغلاق تحليل السهم">×</button></div>`;
    $("dbody").innerHTML=`<div class="grid3">${metric('الدعم المرجعي',money(x.support))}${metric('المقاومة / خط العنق',money(x.neckline || x.resistance))}${metric('ثبات الدعم',unit(x.support_hold_sessions,' جلسات',0))}${metric('RSI اليومي',n(x.indicators.rsi,1),'أدنى قراءة '+n(x.rsi_min,1))}${metric('RSI 4 ساعات',n(x.indicators.rsi_4h,1),'قراءة محفوظة / غير مستنتجة')}${metric('Float',(!x.float_exact && valid(x.float) ? '≈ ' : '')+compact(x.float),x.float_exact ? 'فلوت Yahoo' : 'بديل من الأسهم القائمة / غير متاح')}</div>
      <article class="liq-card ${dclass}"><header><h3>كشف السيولة التجميعية</h3>${divBadge(x)}</header><p>${esc(d.summary || 'لا تحليل سيولة محفوظ')}</p>${d.type!=='unavailable' && d.type!=='none' ? `<div class="comparison"><span class="num">السعر ${pct(d.price_change_pct)}</span><span class="num">ΔCMF ${signed(d.cmf_delta)}</span><span class="num">ΔMFI ${signed(d.mfi_delta,1)}</span><span>${esc(d.strength || '')} · ${esc((d.indicators_involved || []).join(' / '))}</span></div><p>${esc(d.detail)}</p>` : `<p>${esc(l.data_quality?.note || d.detail || '')}</p>`}<div class="grid3" style="margin-top:11px">${metric('CMF(20)',signed(l.cmf),l.cmf_label || '')}${metric('CMF(10) السريع',signed(l.cmf_10),'قراءة منفصلة عن CMF(20)')}${metric('CMF(20) · 4H',signed(l.cmf_4h))}${metric('MFI(14)',n(l.mfi,1),'أدنى '+n(l.mfi_min,1))}${metric('درجة التجميع',unit(l.accumulation_score,' /100',0),'تقييم آلي غير مُختبر تاريخيًا')}${metric('حالة السيولة',l.accumulation_state || 'غير متاحة','',true)}</div></article>
      <section class="sect"><h3>الشموع والارتكاز والانحراف</h3><div class="chart-controls"><div class="pane-tabs" id="timeframeTabs"><button class="pane-tab ${timeframe==='1D' ? 'on' : ''}" data-timeframe="1D" aria-pressed="${timeframe==='1D'}">يومي · 1D</button><button class="pane-tab ${timeframe==='4H' ? 'on' : ''}" data-timeframe="4H" aria-pressed="${timeframe==='4H'}" ${x.chart_4h.length ? '' : 'disabled title="شموع 4 ساعات غير محفوظة؛ تتوفر بعد مسح جديد"'}>4 ساعات · 4H</button></div><div class="chart-options"><label><input type="checkbox" id="showOverlays" ${overlays ? 'checked' : ''}> EMA / VWAP</label><button class="pane-tab ${baseZoom ? 'on' : ''}" id="zoomBase" aria-pressed="${baseZoom}">تقريب القاعدة</button></div></div><div class="pane-tabs" id="paneTabs"></div><div class="chart-box"><div class="ohlc" id="ohlc"></div><canvas class="chart" id="cPrice" role="img" aria-label="رسم الشموع والدعم والمقاومة وخط الانحراف"></canvas><canvas class="volume" id="cVolume" role="img" aria-label="أحجام التداول الفعلية"></canvas><canvas class="pane" id="cPane" role="img" aria-label="رسم مؤشر السيولة المحدد وخط الانحراف"></canvas><div class="legend" id="legend"></div></div><p class="chart-notice" id="chartNotice"></p></section>
      <section class="sect"><h3>تحليل قاعدة الارتكاز الخاملة</h3><p class="note">${esc(b.dormancy_label || 'بيانات القاعدة غير متاحة')} · لا يُفترض أن كل سهم عند القاع قد شكّل ارتكازًا.</p><div class="grid3">${metric('درجة جودة الارتكاز',unit(b.dormancy_score,' /100',0))}${metric('نطاق آخر 10 شموع',unit(b.base_range_pct,'%'))}${metric('انكماش التذبذب',unit(b.volatility_compression_pct,'%'))}${metric('حجم القاعدة / ما قبلها',unit(b.volume_dryup_ratio,'×',2),'جفاف الحجم عند ≤ 0.65×')}${metric('لمسات الدعم',i0(b.support_touches))}${metric('البعد عن الدعم',pct(b.distance_to_support_pct))}${metric('النمط السعري المحتمل',b.pattern || '—','',true)}${metric('منذ التقسيم',unit(x.split?.days_since,' يومًا',0),x.split?.date || '')}${metric('ثبات بعد الاختبار',unit(x.retest_hold_sessions,' جلسات',0))}</div></section>
      <section class="sect"><h3>الحجم وتوازن التدفق</h3><div class="grid3">${metric('OBV',compact(l.obv),'ميل آخر 8 شموع '+compact(l.obv_slope))}${metric('خط A/D',compact(l.adl),'تراكم CLV × الحجم')}${metric('CLV آخر شمعة',signed(l.clv),'موقع الإغلاق؛ لا يحتاج حجمًا')}${metric('MFV آخر شمعة',compact(l.mfv),'الحجم مرجحًا بمضاعف التدفق')}${metric('حجم صاعد / هابط · 10 جلسات',unit(l.up_down_vol_ratio,'×',2))}${metric('صافي التدفق · 10 جلسات',compact(l.net_buying),'وكيل سيولة، لا بيانات أوامر')}</div><p class="note">CLV يمكن حسابه من السعر وحده، لكن CMF وMFI وOBV وA/D لا تُقدّر دون أحجام فعلية. التدفق الموجب ليس دليلًا مؤكدًا على شراء مؤسسات.</p></section>
      <section class="sect"><h3>الشروط الأساسية · جاهزية ${i0(x.readiness_score)} /100</h3>${checkRows(x,true)}</section>
      <section class="sect"><h3>تأكيد السيولة والارتكاز · ${i0(x.confirm_score)} /100</h3>${checkRows(x,false)}<p class="note">وزن التأكيدات المتاحة: <span class="num">${i0(x.confirmation_available_weight)} /100</span> · التصنيف المركب <b>${esc(x.grade || '—')}${x.grade_provisional ? '*' : ''}</b>${x.grade_provisional ? ' مبدئي، لأن بعض بيانات السيولة غير متاحة.' : ' يصف توافق الشروط فقط، وليس احتمال النجاح.'}</p></section>
      <section class="sect"><h3>خريطة المستويات والمخاطرة · سيناريو ورقي</h3><div class="lvl"><span>السعر المرجعي (ليس أمر دخول)</span><b class="num">${money(r.entry ?? x.price)}</b></div><div class="lvl stop"><span>الوقف المرجعي · ${esc(r.stop_method || '')}</span><b class="num">${money(r.stop)} · ${valid(r.risk_pct) ? '−'+n(r.risk_pct,1)+'%' : 'غير صالح للحساب'}</b></div>${(r.targets || []).map(t=>`<div class="lvl tgt"><span>${esc(t.short || t.label)} · ${esc(t.label || '')}${t.final ? ' · قمة بعد التقسيم' : ''}</span><b class="num">${money(t.price ?? t.level)} · ${pct(t.pct)} · ${unit(t.rr,':1',2)}</b></div>`).join('')}<div class="lvl"><span>مستوى اختراق خط العنق / المقاومة</span><b class="num">${money(r.breakout_trigger)}</b></div><div class="lvl"><span>VWAP ما بعد التقسيم · قراءة المسح</span><b class="num">${money(x.indicators.vwap)}</b></div><div class="grid3" style="margin-top:10px">${metric('ATR(14) اليومي',money(r.atr))}${metric('حجم ورقي',unit(r.shares,' سهم',0),'محدود برأس المال والمخاطرة')}${metric('قيمة المركز الورقي',money(r.position_value))}</div><p class="note">${esc(r.disclaimer || 'سيناريو تعليمي لا ينفذ أوامر ولا يمثل توصية.')}</p></section>
      <section class="sect"><h3>الأخبار والمخاطر المحفوظة</h3>${newsHTML(x)}</section><p class="note">${esc(x.analysis_quality?.note || '')}<br>الأسعار ومؤشرات الجاهزية من آخر مسح محفوظ. المؤشرات الحجمية تعتمد على الحجم الفعلي فقط.</p>`;
    window.RadarTheme?.syncControls();
    $("drawer").inert=false; $("drawer").removeAttribute("inert"); $("drawer").setAttribute("aria-hidden","false");
    $("drawer").classList.add("on"); $("scrim").classList.add("on"); document.body.style.overflow="hidden";
    $("main").inert=true; document.querySelector(".topbar").inert=true;
    $("dbody").scrollTop=scroll;
    $("closeDrawerBtn").addEventListener("click",closeDrawer);
    $("timeframeTabs").addEventListener("click",e=>{const b=e.target.closest('[data-timeframe]'); if(!b || b.disabled) return; timeframe=b.dataset.timeframe; hoverIndex=null; renderChartControls(); drawCharts();});
    $("showOverlays").addEventListener("change",e=>{overlays=e.target.checked;drawCharts();});
    $("zoomBase").addEventListener("click",()=>{baseZoom=!baseZoom; hoverIndex=null; renderChartControls(); drawCharts();});
    $("paneTabs").addEventListener("click",e=>{const b=e.target.closest('[data-pane]'); if(!b) return; pane=b.dataset.pane; renderChartControls(); drawCharts();});
    $("cPrice").addEventListener("pointermove",e=>{
      const view=chartData(), rect=$("cPrice").getBoundingClientRect(), width=rect.width || 600;
      const px=e.clientX-rect.left, offset=18, plot=width-offset-65;
      hoverIndex=Math.max(0,Math.min(view.bars.length-1,Math.floor((px-offset)/plot*view.bars.length)));
      queueDraw();
    });
    $("cPrice").addEventListener("pointerleave",()=>{hoverIndex=null;queueDraw();});
    if(chartObserver) chartObserver.disconnect();
    if(window.ResizeObserver){chartObserver=new ResizeObserver(queueDraw);chartObserver.observe($("cPrice"));}
    renderChartControls(); drawCharts(); renderGrid();
    if(!keep) $("closeDrawerBtn").focus();
    else if(oldFocus) {
      const target=oldFocus.id ? $(oldFocus.id) : Array.from($("drawer").querySelectorAll("button")).find(b=>oldFocus.pane && b.dataset.pane===oldFocus.pane || oldFocus.timeframe && b.dataset.timeframe===oldFocus.timeframe);
      (target || $("closeDrawerBtn")).focus();
    }
  }
  function closeDrawer() {
    if(chartObserver) {chartObserver.disconnect();chartObserver=null;}
    if(drawFrame!==null) {cancelAnimationFrame(drawFrame);drawFrame=null;}
    const closingTicker=selected;
    selected=null; hoverIndex=null;
    $("drawer").classList.remove("on"); $("scrim").classList.remove("on");
    $("drawer").setAttribute("aria-hidden","true"); $("drawer").inert=true; $("drawer").setAttribute("inert","");
    document.body.style.overflow="";
    $("main").inert=false; document.querySelector(".topbar").inert=false;
    const focusWasConnected=lastFocus?.isConnected;
    if(D) renderGrid();
    if(focusWasConnected && lastFocus?.isConnected) lastFocus.focus();
    else Array.from(document.querySelectorAll("[data-open]")).find(b=>b.dataset.open===closingTicker && b.getClientRects().length>0)?.focus();
    lastFocus=null;
  }
  const PANES=[['cmf','CMF (20 / 10)'],['mfi','MFI (14)'],['rsi','RSI (14)'],['obv','OBV'],['macd','MACD']];
  function renderChartControls() {
    $("paneTabs").innerHTML=PANES.map(([key,label])=>`<button class="pane-tab ${pane===key ? 'on' : ''}" data-pane="${key}" aria-pressed="${pane===key}">${label}</button>`).join('');
    $("timeframeTabs").querySelectorAll('[data-timeframe]').forEach(b=>{b.classList.toggle('on',b.dataset.timeframe===timeframe);b.setAttribute('aria-pressed',b.dataset.timeframe===timeframe);});
    $("zoomBase").classList.toggle('on',baseZoom); $("zoomBase").setAttribute('aria-pressed',baseZoom);
  }
  function chartData() {
    const x=items.find(x=>x.ticker===selected);
    const source=(timeframe==='4H' ? x?.chart_4h : x?.chart) || [];
    const offset=baseZoom ? Math.max(0,source.length-20) : 0;
    return {x,bars:source.slice(offset),offset};
  }
  function queueDraw() {
    if(!selected || drawFrame!==null) return;
    drawFrame=requestAnimationFrame(()=>{drawFrame=null;if(selected) drawCharts();});
  }
  const CHART_COLORS = {
    green:'green', red:'red', muted:'muted', grid:'grid', cyan:'cyan', amber:'amber', violet:'violet',
    bg:'bg', crosshair:'crosshair', vwap:'vwap', priceText:'price-text',
    volumeUp:'volume-up', volumeDown:'volume-down', histogramUp:'histogram-up', histogramDown:'histogram-down', zone:'zone',
  };
  let COLORS = {};
  function chartColors() {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(Object.entries(CHART_COLORS).map(([key, token]) => [key, style.getPropertyValue('--chart-'+token).trim()]));
  }
  function setup(canvas,height) {
    if(!canvas) return null;
    let ctx; try{ctx=canvas.getContext('2d');}catch(_){return null;} if(!ctx) return null;
    const width=canvas.clientWidth || canvas.getBoundingClientRect().width || 700;
    const ratio=Math.min(3,window.devicePixelRatio || 1);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle=COLORS.bg;ctx.fillRect(0,0,width,height);
    ctx.font='10px ui-monospace, Consolas, Tahoma, monospace';ctx.direction='ltr';
    return {ctx,width,height,left:18,right:width-65,top:16,bottom:height-24};
  }
  function scale(plot,min,max,count) {
    const range=max-min || Math.abs(max)*.1 || 1;
    return {x:i=>plot.left+(i+.5)*(plot.right-plot.left)/Math.max(1,count), y:v=>plot.bottom-(v-min)/range*(plot.bottom-plot.top), min,max,range};
  }
  function grid(plot,s,ticks=4,format=v=>n(v,2)) {
    const c=plot.ctx;c.lineWidth=1;c.strokeStyle=COLORS.grid;c.fillStyle=COLORS.muted;c.textAlign='left';
    for(let j=0;j<=ticks;j++){
      const v=s.min+s.range*j/ticks,y=s.y(v);c.beginPath();c.moveTo(plot.left,y);c.lineTo(plot.right,y);c.stroke();c.fillText(format(v),plot.right+7,y+3);
    }
  }
  function clipPlot(plot,draw) {
    const c=plot.ctx;c.save();c.beginPath();c.rect(plot.left,plot.top,plot.right-plot.left,plot.bottom-plot.top);c.clip();draw(c);c.restore();
  }
  function line(plot,s,bars,key,stroke,dashed=false) {
    const c=plot.ctx;c.strokeStyle=stroke;c.lineWidth=1.4;c.setLineDash(dashed ? [4,3] : []);c.beginPath();let connected=false;
    bars.forEach((b,i)=>{if(!valid(b[key])){connected=false;return;}const x=s.x(i),y=s.y(b[key]);if(connected)c.lineTo(x,y);else c.moveTo(x,y);connected=true;});c.stroke();c.setLineDash([]);
  }
  function message(plot,text) {
    const c=plot.ctx;c.fillStyle=COLORS.muted;c.font='11px Tahoma, system-ui';c.textAlign='center';c.direction='rtl';c.fillText(text,plot.width/2,plot.height/2);c.direction='ltr';
  }
  function crosshair(plot,s,count) {
    if(hoverIndex===null || hoverIndex>=count) return;
    const c=plot.ctx;c.strokeStyle=COLORS.crosshair;c.lineWidth=1;c.setLineDash([3,3]);c.beginPath();c.moveTo(s.x(hoverIndex),plot.top);c.lineTo(s.x(hoverIndex),plot.bottom);c.stroke();c.setLineDash([]);
  }
  function drawDivergence(plot,s,view,key) {
    const d=view.x?.liquidity.divergence;
    if(timeframe!=='1D' || !d?.direction || !valid(d.p1_idx) || !valid(d.p2_idx)) return;
    const a=d.p1_idx-view.offset,b=d.p2_idx-view.offset;
    if(a<0 || b<0 || a>=view.bars.length || b>=view.bars.length) return;
    const involved=d.indicators_involved || [];
    if(key!=='price' && !(key==='cmf' && involved.includes('CMF(20)') || key==='mfi' && involved.includes('MFI(14)') || key==='obv' && involved.includes('OBV'))) return;
    const p=key==='price' ? d.p1_price : d['p1_'+key],q=key==='price' ? d.p2_price : d['p2_'+key];
    if(!valid(p) || !valid(q)) return;
    const c=plot.ctx,stroke=d.direction==='positive' ? COLORS.green : COLORS.red;
    c.strokeStyle=stroke;c.fillStyle=stroke;c.lineWidth=2.1;c.setLineDash([5,3]);c.beginPath();c.moveTo(s.x(a),s.y(p));c.lineTo(s.x(b),s.y(q));c.stroke();c.setLineDash([]);
    for(const [i,v] of [[a,p],[b,q]]){c.beginPath();c.arc(s.x(i),s.y(v),3,0,Math.PI*2);c.fill();}
    c.font='9px Tahoma, system-ui';c.direction='rtl';c.textAlign='center';c.fillText(d.direction==='positive' ? 'انحراف إيجابي' : 'انحراف سلبي',(s.x(a)+s.x(b))/2,Math.max(plot.top+10,Math.min(s.y(p),s.y(q))-9));c.direction='ltr';
  }
  function horizontal(plot,s,level,label,stroke) {
    if(!valid(level) || level<s.min || level>s.max) return;
    const c=plot.ctx;c.strokeStyle=stroke;c.fillStyle=stroke;c.lineWidth=1;c.setLineDash([5,4]);c.beginPath();c.moveTo(plot.left,s.y(level));c.lineTo(plot.right,s.y(level));c.stroke();c.setLineDash([]);
    c.font='9px Tahoma, system-ui';c.textAlign='left';c.fillText(label+' '+money(level),plot.left+4,s.y(level)-4);
  }
  function drawPrice(view) {
    const canvas=$('cPrice'), plot=setup(canvas,window.innerWidth<480 ? 280 : 320);if(!plot) return;
    const bars=view.bars, x=view.x;if(!bars.length){message(plot,'لا توجد شموع محفوظة لهذا الفريم');return;}
    let lo=Math.min(...bars.map(b=>b.low)),hi=Math.max(...bars.map(b=>b.high));
    const pad=(hi-lo || hi*.05)*.08; lo=Math.max(.0001,lo-pad);hi+=pad;
    const s=scale(plot,lo,hi,bars.length);grid(plot,s,4,v=>n(v,hi<1 ? 4 : 2));
    clipPlot(plot,c=>{
      const width=Math.max(1,Math.min(12,(plot.right-plot.left)/bars.length*.64));
      bars.forEach((b,i)=>{const up=b.close>=b.open,stroke=up ? COLORS.green : COLORS.red;c.strokeStyle=stroke;c.fillStyle=stroke;c.lineWidth=1;c.beginPath();c.moveTo(s.x(i),s.y(b.high));c.lineTo(s.x(i),s.y(b.low));c.stroke();c.fillRect(s.x(i)-width/2,Math.min(s.y(b.open),s.y(b.close)),width,Math.max(1,Math.abs(s.y(b.open)-s.y(b.close))));});
      if(overlays){line(plot,s,bars,'ema20',COLORS.amber);line(plot,s,bars,'ema30',COLORS.cyan);line(plot,s,bars,'ema50',COLORS.violet);line(plot,s,bars,'vwap',COLORS.vwap,true);}
      horizontal(plot,s,x.support,'دعم',COLORS.green);horizontal(plot,s,x.neckline || x.resistance,'عنق / مقاومة',COLORS.cyan);
      horizontal(plot,s,x.risk.stop,'وقف',COLORS.red);
      (x.risk.targets || []).forEach(t=>horizontal(plot,s,t.price ?? t.level,t.short || 'هدف',COLORS.violet));
      drawDivergence(plot,s,view,'price');crosshair(plot,s,bars.length);
    });
    const c=plot.ctx;c.fillStyle=COLORS.muted;c.textAlign='center';c.font='9px ui-monospace, Consolas, monospace';
    const numTicks=Math.min(5,bars.length);
    for(let j=0;j<numTicks;j++){const i=Math.round(j/(numTicks-1 || 1)*(bars.length-1));const label=timeframe==='1D' ? bars[i].date.slice(5,10) : bars[i].date.slice(5,10)+' '+bars[i].date.slice(11,16);c.fillText(label,s.x(i),plot.height-7);}
    const close=bars.at(-1).close,y=s.y(close);c.fillStyle=bars.at(-1).close>=bars.at(-1).open ? COLORS.green : COLORS.red;c.fillRect(plot.right+1,y-8,64,16);c.fillStyle=COLORS.priceText;c.textAlign='left';c.font='9px ui-monospace, monospace';c.fillText(n(close,close<1 ? 4 : 2),plot.right+4,y+3);
  }
  function drawVolume(view) {
    const plot=setup($('cVolume'),68);if(!plot)return;
    const bars=view.bars,v=bars.map(b=>b.volume).filter(valid);plot.top=8;plot.bottom=58;
    if(!v.length){message(plot,'الحجم غير محفوظ — لا أحجام تقديرية');return;}
    const max=Math.max(...v,1),s=scale(plot,0,max*1.05,bars.length),c=plot.ctx;
    c.fillStyle=COLORS.muted;c.font='9px ui-monospace, monospace';c.textAlign='left';c.fillText(compact(max),plot.right+5,16);
    const width=Math.max(1,Math.min(10,(plot.right-plot.left)/Math.max(1,bars.length)*.65));
    bars.forEach((b,i)=>{if(!valid(b.volume))return;c.fillStyle=b.close>=b.open ? COLORS.volumeUp : COLORS.volumeDown;c.fillRect(s.x(i)-width/2,s.y(b.volume),width,plot.bottom-s.y(b.volume));});crosshair(plot,s,bars.length);
  }
  function drawPane(view) {
    const plot=setup($('cPane'),120);if(!plot)return;
    const bars=view.bars,key=pane==='macd' ? 'macd' : pane,values=bars.map(b=>b[key]).filter(valid);
    if(!values.length){message(plot,(pane==='cmf' || pane==='mfi' || pane==='obv') ? 'المؤشر غير متاح دون حجم فعلي وتاريخ كافٍ' : 'لا بيانات كافية لهذا المؤشر');return;}
    let lo,hi;
    if(pane==='rsi' || pane==='mfi'){lo=0;hi=100;}
    else if(pane==='cmf'){lo=-1;hi=1;}
    else {
      const more=pane==='macd' ? bars.flatMap(b=>[b.macd,b.macd_signal,b.macd_hist]).filter(valid) : values;
      lo=Math.min(...more);hi=Math.max(...more);const pad=(hi-lo || Math.abs(hi)*.1 || 1)*.15;lo-=pad;hi+=pad;
    }
    const s=scale(plot,lo,hi,bars.length);grid(plot,s,pane==='cmf' ? 4 : 2,v=>pane==='obv' ? compact(v) : n(v,pane==='cmf' || pane==='macd' ? 2 : 0));
    clipPlot(plot,c=>{
      if(pane==='cmf'){c.fillStyle=COLORS.zone;c.fillRect(plot.left,plot.top,plot.right-plot.left,s.y(0)-plot.top);horizontal(plot,s,0,'0',COLORS.muted);line(plot,s,bars,'cmf',COLORS.green);line(plot,s,bars,'cmf10',COLORS.cyan,true);}
      else if(pane==='rsi' || pane==='mfi'){horizontal(plot,s,pane==='rsi' ? 30 : 20,'',COLORS.red);horizontal(plot,s,pane==='rsi' ? 70 : 80,'',COLORS.green);line(plot,s,bars,pane,pane==='mfi' ? COLORS.cyan : COLORS.violet);}
      else if(pane==='obv') line(plot,s,bars,'obv',COLORS.cyan);
      else if(pane==='macd') {
        const width=Math.max(1,Math.min(8,(plot.right-plot.left)/bars.length*.65));
        bars.forEach((b,i)=>{if(!valid(b.macd_hist))return;c.fillStyle=b.macd_hist>=0 ? COLORS.histogramUp : COLORS.histogramDown;c.fillRect(s.x(i)-width/2,Math.min(s.y(0),s.y(b.macd_hist)),width,Math.max(1,Math.abs(s.y(0)-s.y(b.macd_hist))));});line(plot,s,bars,'macd',COLORS.cyan);line(plot,s,bars,'macd_signal',COLORS.amber);
      }
      drawDivergence(plot,s,view,pane);crosshair(plot,s,bars.length);
    });
    plot.ctx.fillStyle=COLORS.muted;plot.ctx.font='9px ui-monospace, monospace';plot.ctx.textAlign='left';plot.ctx.fillText(PANES.find(p=>p[0]===pane)[1],plot.left,11);
  }
  function drawCharts() {
    if(!selected || !$('cPrice'))return;
    const view=chartData(),bars=view.bars,x=view.x;if(!x)return;
    COLORS=chartColors();
    drawPrice(view);drawVolume(view);drawPane(view);
    const b=bars[hoverIndex===null ? bars.length-1 : Math.min(hoverIndex,bars.length-1)];
    $('ohlc').innerHTML=b ? `<span>${esc(b.date.slice(0,timeframe==='1D' ? 10 : 16))}</span>${[['O',b.open],['H',b.high],['L',b.low],['C',b.close],['V',b.volume]].map(([k,v])=>`<span>${k} <b>${k==='V' ? compact(v) : money(v)}</b></span>`).join('')}<span>${pane.toUpperCase()} <b>${n(b[pane],pane==='cmf' ? 3 : 1)}</b></span>` : 'لا شموع محفوظة';
    const legend=overlays ? [['EMA20',COLORS.amber],['EMA30',COLORS.cyan],['EMA50',COLORS.violet],['VWAP',COLORS.vwap]] : [];
    legend.push(['الدعم',COLORS.green],['المقاومة / خط العنق',COLORS.cyan]);
    if(x.liquidity.divergence.direction && timeframe==='1D')legend.push(['خط الانحراف',x.liquidity.divergence.direction==='positive' ? COLORS.green : COLORS.red]);
    if(pane==='cmf')legend.push(['CMF20',COLORS.green],['CMF10 متقطع',COLORS.cyan]);
    $('legend').innerHTML=legend.map(([label,c])=>`<span><i style="background:${c}"></i>${label}</span>`).join('');
    $('chartNotice').textContent=`${timeframe==='1D' ? 'شموع يومية' : 'شموع 4 ساعات'} · ${bars.length} شمعة ظاهرة. `+(x.liquidity.available ? 'تُرسم خطوط الانحراف عند نقاطها الفعلية وعلى المؤشرات المتوافقة فقط. ' : 'CMF وMFI غير متاحين في هذه اللقطة. ')+(x.analysis_quality?.limited_price_history ? 'التاريخ السعري محدود؛ EMA/RSI على الشارت قد تختلف عن قراءات المسح الكامل. ' : '')+'الأهداف البعيدة خارج النطاق تُعرض في خريطة المستويات أدناه.';
  }

  function csvText(rows=filtered()) {
    const heads=['ticker','company','stage','price','readiness_score','confirm_score','confirmation_available_weight','composite_score','grade','grade_provisional','support','support_hold_sessions','split_date','split_ratio','max_drawdown_pct','cmf20','cmf10','cmf_4h','mfi14','mfi_4h','accumulation_score','accumulation_state','divergence','divergence_p1_date','divergence_p2_date','liquidity_available','volume_source','last_daily_bar','market_scan_at'];
    function field(v) {
      if(v===null || v===undefined)return '""';
      let text=String(v);
      // Only untrusted text is guarded; genuine negative numeric values stay numeric.
      if(typeof v==='string' && /^[\s]*[=+\-@]/.test(text))text="'"+text;
      return '"'+text.replace(/"/g,'""')+'"';
    }
    return '\ufeff'+[heads.map(field).join(','),...rows.map(x=>[x.ticker,x.company,x.stage,x.price,x.readiness_score,x.confirm_score,x.confirmation_available_weight,x.composite_score,x.grade,x.grade_provisional,x.support,x.support_hold_sessions,x.split?.date,x.split?.ratio,x.max_drawdown_pct,x.liquidity.cmf,x.liquidity.cmf_10,x.liquidity.cmf_4h,x.liquidity.mfi,x.liquidity.mfi_4h,x.liquidity.accumulation_score,x.liquidity.accumulation_state,x.liquidity.divergence.type,x.liquidity.divergence.p1_date,x.liquidity.divergence.p2_date,x.liquidity.available,x.liquidity.data_quality?.volume_source,x.chart.at(-1)?.date,D?.updated_at].map(field).join(','))].join('\r\n');
  }
  function exportCSV() {
    const rows=filtered();if(!rows.length)return toast('لا صفوف للتصدير ضمن الفلاتر الحالية.');
    const blob=new Blob([csvText(rows)],{type:'text/csv;charset=utf-8'}), url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download='stock-radar-'+(D?.updated_at || 'snapshot').slice(0,10)+'.csv';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('تم تصدير '+rows.length+' سهم مع حالة توفر بيانات السيولة.');
  }
  function clock() {
    const now=new Date();$('etClock').textContent=now.toLocaleTimeString('en-GB',{timeZone:'America/New_York',hour12:false});
    const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',weekday:'short',hour:'numeric',minute:'numeric',hourCycle:'h23'}).formatToParts(now);
    const part=k=>parts.find(p=>p.type===k)?.value, minute=Number(part('hour'))*60+Number(part('minute'));
    const weekend=['Sat','Sun'].includes(part('weekday'));
    $('etPhase').textContent=weekend ? 'نيويورك · عطلة أسبوعية' : minute>=570 && minute<960 ? 'نيويورك · وقت الجلسة*' : 'نيويورك · خارج الجلسة';
    $('etPhase').title='الساعة لا تراعي عطلات البورصة الرسمية ولا تشير إلى اتصال بأسعار لحظية.';
    const left=Math.max(0,Math.ceil((nextRefresh-Date.now())/1000));$('refreshCountdown').textContent=auto ? left : '—';
    if(auto && Date.now()>=nextRefresh && !fetching){nextRefresh=Date.now()+REFRESH_MS;load(false);}
  }
  function autoButton() {$('autoBtn').classList.toggle('on',auto);$('autoBtn').setAttribute('aria-pressed',auto);}
  $('tabs').addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(b)setTab(b.dataset.tab);});
  $('stateChips').addEventListener('click',e=>{const b=e.target.closest('[data-state]');if(b){filters.stage=b.dataset.state;renderFilters();renderGrid();}});
  $('liquidityChips').addEventListener('click',e=>{const b=e.target.closest('[data-liquidity]');if(b){filters.liquidity=b.dataset.liquidity;renderFilters();renderGrid();}});
  $('kpis').addEventListener('click',e=>{const b=e.target.closest('[data-quick]');if(b)quickFilter(b.dataset.quick);});
  $('q').addEventListener('input',()=>{if(D)renderGrid();});
  $('minScore').addEventListener('input',e=>{filters.score=Number(e.target.value);$('minScoreVal').textContent=filters.score;if(D)renderGrid();});
  $('sortBy').addEventListener('change',e=>{filters.sort=e.target.value;filters.ascending=false;if(D)renderGrid();});
  for(const [id,key] of [['onlyDormant','dormant'],['onlySweep','sweep']])$(id).addEventListener('click',()=>{filters[key]=!filters[key];renderFilters();renderGrid();});
  $('resetBtn').addEventListener('click',resetFilters);$('csvBtn').addEventListener('click',exportCSV);
  $('refreshBtn').addEventListener('click',()=>load(true));$('scrim').addEventListener('click',closeDrawer);
  $('autoBtn').addEventListener('click',()=>{auto=!auto;try{localStorage.setItem('radar-auto-refresh',auto ? 'on' : 'off');}catch(_){}nextRefresh=Date.now()+REFRESH_MS;autoButton();clock();});
  document.addEventListener('click',e=>{
    const open=e.target.closest('[data-open]');if(open){openDrawer(open.dataset.open);return;}
    const row=e.target.closest('#gridBody tr[data-ticker]');if(row){openDrawer(row.dataset.ticker);return;}
    if(e.target.closest('.reset-filters'))resetFilters();if(e.target.closest('.retry-load'))load(true);
    const sort=e.target.closest('[data-sort]');if(sort){const key=sort.dataset.sort;filters.ascending=(filters.sort===key || filters.sort==='default' && key==='score') ? !filters.ascending : false;filters.sort=key;const option=Array.from($('sortBy').options).find(o=>o.value===key);if(option)$('sortBy').value=key;renderGrid();}
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape' && selected){closeDrawer();return;}
    if(e.key==='/' && !selected && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){e.preventDefault();setTab('radar');$('q').focus();}
    if(e.key==='Tab' && selected){
      const nodes=Array.from($('drawer').querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),[tabindex="0"]')).filter(v=>!v.closest('[hidden]'));
      const first=nodes[0],last=nodes.at(-1);if(!first)return;
      if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}
    }
  });
  window.addEventListener('resize',queueDraw);
  // Redraw pixels only: preserve selection, filters, pane, timeframe, zoom and scroll.
  window.addEventListener('radar:themechange',queueDraw);
  autoButton();clock();setInterval(clock,1000);load();
  if('serviceWorker' in navigator && /^https?:$/.test(location.protocol))navigator.serviceWorker.register(new URL('sw.js',ASSET_BASE)).catch(()=>{});
  // Small, read-only-by-default verification surface; no execution/trading endpoints.
  window.RadarTerminal={applyData,filtered,openDrawer,closeDrawer,drawCharts,csvText,resetFilters,load,get data(){return D;},get selection(){return selected;},get chartPane(){return pane;},get chartTimeframe(){return timeframe;}};
})();
