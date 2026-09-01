/* Stock Radar - Candlestick dashboard add-on
   Save as docs/candles.js and add before </body>:
   <script src="./candles.js"></script>
*/
(() => {
  "use strict";

  let RAW = [];
  let lastTk = null;

  const css = `
    .sr-candle-panel{margin:0 0 22px;background:var(--panel,#12171F);border:1px solid var(--border,#232B37);border-radius:14px;overflow:hidden}
    .sr-candle-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid var(--border-soft,#1B222C)}
    .sr-candle-title{font-weight:800;font-family:var(--font-display,'Cairo',sans-serif)}
    .sr-candle-meta{font-size:11px;color:var(--txt-dim,#8B94A5)}
    .sr-candle-toolbar{display:flex;gap:6px;flex-wrap:wrap}
    .sr-candle-btn{border:1px solid var(--border,#232B37);background:var(--panel-2,#161C26);color:var(--txt,#E7EBF1);border-radius:8px;padding:5px 9px;font:600 11px var(--font-ui,'IBM Plex Sans Arabic',sans-serif);cursor:pointer}
    .sr-candle-btn.on{background:var(--gold-soft,rgba(217,168,78,.12));border-color:var(--gold,#D9A84E);color:var(--gold,#D9A84E)}
    .sr-candle-wrap{position:relative;width:100%;height:430px;background:#0b1017}
    .sr-candle-canvas{display:block;width:100%;height:100%}
    .sr-candle-tooltip{position:absolute;display:none;pointer-events:none;z-index:3;background:#0b1017eF;border:1px solid var(--border,#232B37);border-radius:8px;padding:7px 9px;font-size:11px;line-height:1.55;box-shadow:0 8px 25px #0008;white-space:nowrap}
    .sr-candle-legend{display:flex;gap:14px;flex-wrap:wrap;padding:8px 14px 12px;color:var(--txt-dim,#8B94A5);font-size:10.5px}
    .sr-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-left:4px}
    @media(max-width:700px){.sr-candle-wrap{height:330px}.sr-candle-head{align-items:flex-start;flex-direction:column}}
  `;
  const st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  async function loadRaw() {
    try {
      const r = await fetch("./data.json?candles=" + Date.now(), {cache:"no-store"});
      const d = await r.json();
      RAW = d.stocks || [];
    } catch (e) {
      console.error("Candlestick addon: data.json", e);
    }
  }

  function esc(v){ return String(v ?? "").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }

  function makePanel(raw) {
    if (!raw || !Array.isArray(raw.candles) || raw.candles.length < 2) {
      return `<div class="sr-candle-panel"><div class="sr-candle-head">
        <div><div class="sr-candle-title">📊 الشموع اليومية</div>
        <div class="sr-candle-meta">${raw?.dataError ? esc(raw.dataError) : "لا توجد بيانات شموع كافية لهذا السهم"}</div></div>
      </div></div>`;
    }
    return `<div class="sr-candle-panel" id="sr-candle-panel">
      <div class="sr-candle-head">
        <div>
          <div class="sr-candle-title">📊 الشموع اليومية — ${esc(raw.tk)}</div>
          <div class="sr-candle-meta">${raw.candleCount || raw.candles.length} شمعة · ${esc(raw.candleStart || "")} → ${esc(raw.candleEnd || "")}</div>
        </div>
        <div class="sr-candle-toolbar">
          <button class="sr-candle-btn" data-n="30">30</button>
          <button class="sr-candle-btn" data-n="60">60</button>
          <button class="sr-candle-btn on" data-n="120">120</button>
          <button class="sr-candle-btn" data-n="250">250</button>
        </div>
      </div>
      <div class="sr-candle-wrap">
        <canvas class="sr-candle-canvas"></canvas>
        <div class="sr-candle-tooltip"></div>
      </div>
      <div class="sr-candle-legend">
        <span><i class="sr-dot" style="background:#3ECF8E"></i>إغلاق أعلى</span>
        <span><i class="sr-dot" style="background:#EF6461"></i>إغلاق أقل</span>
        <span>خط الدعم: ${raw.support != null ? "$"+Number(raw.support).toFixed(3) : "—"}</span>
        <span>المقاومات: ${(raw.droppedCandles||[]).map(x=>"$"+Number(x).toFixed(3)).join(" · ") || "—"}</span>
      </div>
    </div>`;
  }

  function draw(panel, raw, count) {
    const canvas = panel.querySelector("canvas");
    const wrap = panel.querySelector(".sr-candle-wrap");
    const tip = panel.querySelector(".sr-candle-tooltip");
    if (!canvas || !wrap) return;
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const W = Math.max(300, wrap.clientWidth);
    const H = wrap.clientHeight;
    canvas.width = Math.floor(W*dpr); canvas.height = Math.floor(H*dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,W,H);

    const candles = raw.candles.slice(-Math.min(count, raw.candles.length));
    const pad = {l:12,r:64,t:18,b:46};
    const chartH = H - pad.t - pad.b;
    const volH = Math.max(46, chartH*0.18);
    const priceH = chartH - volH - 10;
    const minP = Math.min(...candles.map(x=>x.low));
    const maxP = Math.max(...candles.map(x=>x.high));
    const span = Math.max(maxP-minP, 0.0001);
    const y = p => pad.t + (maxP-p)/span*priceH;
    const plotW = W-pad.l-pad.r;
    const step = plotW/candles.length;
    const bodyW = Math.max(1.5, Math.min(11, step*0.62));

    ctx.strokeStyle = "rgba(139,148,165,.13)";
    ctx.lineWidth=1;
    ctx.fillStyle="#8B94A5";
    ctx.font="10px Arial";
    for(let i=0;i<5;i++){
      const py=pad.t+(priceH*i/4);
      ctx.beginPath();ctx.moveTo(pad.l,py);ctx.lineTo(W-pad.r,py);ctx.stroke();
      const val=maxP-(span*i/4);
      ctx.fillText("$"+val.toFixed(val<1?3:2), W-pad.r+7, py+3);
    }

    const volumeMax=Math.max(...candles.map(x=>x.volume||0),1);
    candles.forEach((c,i)=>{
      const x=pad.l+i*step+step/2;
      const up=c.close>=c.open;
      ctx.strokeStyle=up?"#3ECF8E":"#EF6461";
      ctx.fillStyle=ctx.strokeStyle;
      ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(x,y(c.high));ctx.lineTo(x,y(c.low));ctx.stroke();
      const top=y(Math.max(c.open,c.close)), bottom=y(Math.min(c.open,c.close));
      ctx.fillRect(x-bodyW/2, top, bodyW, Math.max(1,bottom-top));
      const vh=(c.volume||0)/volumeMax*volH;
      ctx.globalAlpha=.32;
      ctx.fillRect(x-bodyW/2, H-pad.b+2+(volH-vh), bodyW, vh);
      ctx.globalAlpha=1;
    });

    function level(price, color, label){
      if(price==null || price<minP || price>maxP) return;
      const yy=y(price);
      ctx.save(); ctx.strokeStyle=color; ctx.setLineDash([5,4]); ctx.globalAlpha=.8;
      ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(W-pad.r,yy);ctx.stroke();
      ctx.setLineDash([]);ctx.fillStyle=color;ctx.font="700 10px Arial";ctx.fillText(label+" $"+Number(price).toFixed(3), pad.l+5, yy-5);ctx.restore();
    }
    level(raw.support, "#D9A84E", "دعم");
    (raw.droppedCandles||[]).forEach((p,i)=>level(p, "#6EA8FE", "مقاومة "+(i+1)));

    ctx.fillStyle="#5A6376";ctx.font="10px Arial";
    [0,Math.floor(candles.length/2),candles.length-1].forEach(i=>{
      const x=pad.l+i*step+step/2;
      ctx.fillText(candles[i].time, Math.max(pad.l,x-30), H-15);
    });

    canvas.onmousemove = ev => {
      const rect=canvas.getBoundingClientRect();
      const px=ev.clientX-rect.left;
      const i=Math.max(0,Math.min(candles.length-1,Math.floor((px-pad.l)/step)));
      const c=candles[i];
      tip.style.display="block";
      tip.style.left=Math.min(Math.max(8,px+10),W-150)+"px";
      tip.style.top="10px";
      tip.innerHTML=`<b>${esc(c.time)}</b><br>فتح $${c.open.toFixed(4)} · أعلى $${c.high.toFixed(4)}<br>أدنى $${c.low.toFixed(4)} · إغلاق $${c.close.toFixed(4)}<br>حجم ${Number(c.volume||0).toLocaleString("en-US")}`;
    };
    canvas.onmouseleave=()=>tip.style.display="none";
  }

  function attach() {
    const view = document.getElementById("view-detail");
    if (!view || !view.classList.contains("active")) return;
    const head = view.querySelector(".detail-head");
    if (!head) return;
    const tk = head.querySelector(".dh-tk")?.textContent?.trim();
    if (!tk || tk===lastTk && view.querySelector("#sr-candle-panel")) return;
    const raw = RAW.find(x=>x.tk===tk);
    if (!raw) return;
    const old=view.querySelector("#sr-candle-panel");
    if(old) old.remove();
    const holder=document.createElement("div");
    holder.innerHTML=makePanel(raw);
    head.insertAdjacentElement("afterend", holder.firstElementChild);
    const panel=view.querySelector("#sr-candle-panel");
    if(!panel) return;
    let count=120;
    panel.querySelectorAll(".sr-candle-btn").forEach(btn=>{
      btn.addEventListener("click",()=>{
        panel.querySelectorAll(".sr-candle-btn").forEach(b=>b.classList.remove("on"));
        btn.classList.add("on");
        count=Number(btn.dataset.n);
        draw(panel,raw,count);
      });
    });
    draw(panel,raw,count);
    lastTk=tk;
  }

  loadRaw().then(attach);
  const view=document.getElementById("view-detail");
  if(view) new MutationObserver(()=>setTimeout(attach,30)).observe(view,{subtree:true,childList:true,attributes:true});
  window.addEventListener("resize",()=>{ const p=document.querySelector("#sr-candle-panel"); if(p){const tk=p.closest(".view")?.querySelector(".dh-tk")?.textContent?.trim(); const raw=RAW.find(x=>x.tk===tk); if(raw) draw(p,raw,120);} });
})();

/* Dashboard crash recovery: the original dashboard groups by status without a fallback.
   Run the safe renderer after data.json finishes loading. */
(function(){
  function recover(){
    try{
      if(window.StockRadarDashboardFix && window.StockRadarDashboardFix.run){
        window.StockRadarDashboardFix.run();
      }
    }catch(e){ console.error('Dashboard recovery:',e); }
  }
  [0,80,200,500,1200].forEach(ms=>setTimeout(recover,ms));
})();
