#!/usr/bin/env node
/* Offline DOM + Canvas verification. Synthetic scenarios are never published. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {JSDOM, ResourceLoader, VirtualConsole} from 'jsdom';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const market = JSON.parse(fs.readFileSync(path.join(root, 'docs/data.json'), 'utf8'));
const synthetic = JSON.parse(execFileSync('python3', [path.join(root, 'tools/make_test_fixture.py')], {encoding:'utf8'}));
let assertions = 0;
function check(value, message) { assertions++; assert.ok(value, message); }
const clone = obj => JSON.parse(JSON.stringify(obj));
const wait = ms => new Promise(resolve=>setTimeout(resolve, ms));

class Files extends ResourceLoader {
  fetch(url) {
    const file = new URL(url).pathname.split('/').at(-1);
    if (['theme.js', 'terminal.js', 'terminal.css'].includes(file)) return Promise.resolve(fs.readFileSync(path.join(root,'docs',file)));
    return null;
  }
}
async function setup(entry, url, options = {}) {
  const errors=[], canvasCalls=[], requests=[];
  let payload=clone(market), fail=false;
  const console=new VirtualConsole(); console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,entry),'utf8'), {
    url,runScripts:'dangerously',resources:new Files(),pretendToBeVisual:true,virtualConsole:console,
    beforeParse(w) {
      if (options.denyStorage) Object.defineProperty(w,'localStorage',{get(){throw new w.DOMException('Intentional blocked storage','SecurityError');}});
      else if (options.savedTheme !== undefined) w.localStorage.setItem('radar-theme',options.savedTheme);
      Object.defineProperty(w,'devicePixelRatio',{value:2});
      w.ResizeObserver=class {observe(){} disconnect(){}};
      w.fetch=async input=>{
        const endpoint=String(input);requests.push(endpoint);
        if(fail)throw new Error('Intentional offline test');
        return {ok:true,headers:{get:()=>null},json:async()=>clone(payload)};
      };
      w.URL.createObjectURL=()=> 'blob:radar-qa';w.URL.revokeObjectURL=()=>{};
      Object.defineProperty(w.HTMLCanvasElement.prototype,'clientWidth',{get:()=>700});
      w.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({width:700,height:320,left:0,right:700,top:0,bottom:320});
      w.HTMLCanvasElement.prototype.getContext=function(){
        const canvas=this;
        return new Proxy({}, {get(obj,key) {
          if(key in obj)return obj[key];
          return (...args)=> {
            canvasCalls.push({id:canvas.id,method:key,args});
            if(['moveTo','lineTo','fillRect','rect','arc','setTransform'].includes(key)) args.filter(a=>typeof a==='number').forEach(a=>assert.ok(Number.isFinite(a),'Canvas received non-finite coordinates'));
          };
        },set(obj,key,v){canvasCalls.push({id:canvas.id,method:'set:'+key,args:[v]});obj[key]=v;return true;}});
      };
    }
  });
  const settings=entry.endsWith('settings.html');
  for(let k=0;k<100 && !(settings ? dom.window.document.readyState==='complete' : dom.window.RadarTerminal?.data);k++)await wait(20);
  check(settings ? dom.window.RadarTheme : dom.window.RadarTerminal?.data, settings ? 'Settings loaded shared theme' : 'Dashboard loaded without network');
  return {dom,errors,canvasCalls,requests,setPayload:x=>payload=x,setFail:x=>fail=x};
}

const app=await setup('docs/index.html','https://radar.example/stock-radar/');
const w=app.dom.window, doc=w.document, api=w.RadarTerminal;
const $=id=>doc.getElementById(id), rows=()=>Array.from(doc.querySelectorAll('#gridBody tr'));
function click(selector){const element=doc.querySelector(selector);check(Boolean(element),'Element exists: '+selector);element.click();}
function input(id,value){$(id).value=value;$(id).dispatchEvent(new w.Event('input',{bubbles:true}));}
function key(value){doc.dispatchEvent(new w.KeyboardEvent('keydown',{key:value,bubbles:true}));}
check(rows().length===market.signals.length,'All saved signals rendered');
check(doc.querySelectorAll('.stock-card').length===market.signals.length,'Mobile cards match table');
check(doc.documentElement.dir==='rtl','Arabic RTL terminal');
check(!doc.body.textContent.includes('NaN'),'No NaN labels');
check(app.requests.every(url=>new URL(url).origin==='https://radar.example'),'No fallback to another repository / localhost');
check(doc.querySelectorAll('#kpis .kpi').length===8,'Eight strategy-specific KPI cards');
// Default, persistence, accessibility and synchronization of all theme controls.
check(w.RadarTheme.current==='dark','Dark remains the default');
check($('themeBtn').getAttribute('aria-pressed')==='false','Accessible light-mode toggle defaults to off');
click('#themeBtn');
check(w.RadarTheme.current==='light','Header switches to light mode');
check(w.localStorage.getItem('radar-theme')==='light','Theme choice persists');
check($('themeBtn').getAttribute('aria-pressed')==='true' && $('themeBtn').textContent.includes('فاتح'),'Theme state and label agree');
check(doc.querySelector('meta[name="theme-color"]').content==='#f3f6fb','Light browser chrome matches the palette');
check(w.getComputedStyle(doc.documentElement).getPropertyValue('color-scheme')==='light','Native controls use the light color scheme');
check(w.RadarTheme.setTheme('sepia')===false && w.RadarTheme.current==='light','Invalid theme cannot replace the active mode');
click('#themeBtn');
check(w.RadarTheme.current==='dark' && doc.querySelector('meta[name="theme-color"]').content==='#0a0e17','Dark mode and browser chrome restore');

for(const tab of ['liquidity','stats','rules','ops','radar']){
  click(`[data-tab="${tab}"]`);check(!$('tab-'+tab).hidden,'Tab visible: '+tab);
  check(doc.querySelectorAll('main>section:not([hidden])').length>=1,'Content available');
}
key('/');check(doc.activeElement===$('q'),'Slash focuses search');
if(market.signals.length){
  const first=market.signals[0];
  input('q',first.ticker);check(rows().some(row=>row.dataset.ticker===first.ticker),'Ticker search');
  click('#resetBtn');check(rows().length===market.signals.length,'Reset restores full list');
  const state=first.state || Object.keys(market.states || {}).find(k=>market.states[k]===first.stage);
  if(state){click(`[data-state="${state}"]`);check(rows().every(row=>market.signals.find(s=>s.ticker===row.dataset.ticker).stage===first.stage),'Stage filter');}
  click('#resetBtn');input('minScore','70');check(api.filtered().every(x=>x.readiness_score>=70),'Readiness slider');
  click('#resetBtn');click('#onlyDormant');check(api.filtered().every(x=>x.dormant_base.is_dormant),'Dormant-base filter');
  click('#resetBtn');click('#onlySweep');check(api.filtered().every(x=>x.liquidity_sweep),'Sweep filter');
  click('#resetBtn');
  api.openDrawer(first.ticker);
  check($('drawer').getAttribute('aria-hidden')==='false','Drawer open and accessible');
  check(!$('drawer').hasAttribute('inert'),'Open drawer is interactive');
  check($('dbody').textContent.includes('كشف السيولة التجميعية'),'Liquidity analysis card');
  check($('dbody').textContent.includes('الارتكاز الخاملة'),'Dormant-base analysis card');
  for(const pane of ['cmf','mfi','rsi','obv','macd']) {click(`[data-pane="${pane}"]`);check(api.chartPane===pane,'Indicator pane: '+pane);}
  if(!first.chart_4h?.length)check(doc.querySelector('[data-timeframe="4H"]').disabled,'4H is disabled instead of fabricated');
  click('#zoomBase');check($('zoomBase').getAttribute('aria-pressed')==='true','Base zoom');
  key('Escape');check(!api.selection,'Escape closes drawer');
  check($('drawer').hasAttribute('inert'),'Closed drawer cannot receive focus');
}

const scanAt=api.data.updated_at;
app.setFail(true);await api.load(true);
check(api.data.updated_at===scanAt,'Fetch failure preserves market timestamp');
check(rows().length===market.signals.length,'Fetch failure preserves previous results');
check($('liveText').textContent.includes('تعذّر'),'Connection error is visible');
app.setFail(false);await api.load(false);

// Run actual engine output with explicitly labeled synthetic OHLCV scenarios.
api.applyData(synthetic);
check($('bannerBox').textContent.includes('اصطناعية'),'Synthetic examples explicitly labeled');
click('[data-liquidity="positive"]');check(rows().length===1 && rows()[0].dataset.ticker==='DEMO-POS','Positive divergence filter');
click('#resetBtn');click('[data-liquidity="negative"]');check(rows().length===1 && rows()[0].dataset.ticker==='DEMO-NEG','Negative divergence filter');
click('#resetBtn');
for(const ticker of ['DEMO-POS','DEMO-NEG']){
  const before=app.canvasCalls.length;api.openDrawer(ticker);
  const draws=app.canvasCalls.slice(before);
  const label=ticker==='DEMO-POS' ? 'انحراف إيجابي' : 'انحراف سلبي';
  check(draws.some(c=>c.id==='cPrice' && c.method==='fillText' && c.args[0]===label),'Price divergence line: '+ticker);
  check(draws.some(c=>c.id==='cPane' && c.method==='fillText' && c.args[0]===label),'CMF divergence line aligned with actual endpoints: '+ticker);
  check(draws.some(c=>c.id==='cVolume' && c.method==='fillRect'),'Actual fixture volume drawn');
  click('[data-timeframe="4H"]');check(api.chartTimeframe==='4H','Independent 4H candles');
  click('[data-pane="mfi"]');check(api.chartPane==='mfi','MFI 4H pane');
  click('[data-timeframe="1D"]');
  api.closeDrawer();
}
// A theme change repaints every Canvas layer without rebuilding the drawer.
click('[data-liquidity="positive"]');
api.openDrawer('DEMO-POS');
click('[data-timeframe="4H"]');click('[data-pane="macd"]');click('#zoomBase');
$('dbody').scrollTop=83;
$('cPrice').dispatchEvent(new w.MouseEvent('pointermove',{clientX:250,bubbles:true}));await wait(35);
const beforeTheme={canvas:$('cPrice'),scroll:$('dbody').scrollTop,ohlc:$('ohlc').textContent,filter:rows().map(r=>r.dataset.ticker).join(',')};
for (const theme of ['light','dark']) {
  const before=app.canvasCalls.length;
  click('#drawerThemeBtn');await wait(35);
  const styles=w.getComputedStyle(doc.documentElement), draws=app.canvasCalls.slice(before);
  check(w.RadarTheme.current===theme,'Drawer theme switch: '+theme);
  check(api.selection==='DEMO-POS' && api.chartPane==='macd' && api.chartTimeframe==='4H','Switch preserves ticker, pane and timeframe');
  check($('zoomBase').getAttribute('aria-pressed')==='true' && $('dbody').scrollTop===beforeTheme.scroll,'Switch preserves base zoom and scroll');
  check($('cPrice')===beforeTheme.canvas && $('ohlc').textContent===beforeTheme.ohlc,'Switch preserves Canvas nodes and hover');
  check(rows().map(r=>r.dataset.ticker).join(',')===beforeTheme.filter,'Switch preserves filters');
  check($('themeBtn').getAttribute('aria-pressed')===String(theme==='light') && $('drawerThemeBtn').getAttribute('aria-pressed')===String(theme==='light'),'Header and drawer controls stay synchronized');
  for (const id of ['cPrice','cVolume','cPane']) check(draws.some(c=>c.id===id && c.method==='set:fillStyle' && c.args[0]===styles.getPropertyValue('--chart-bg').trim()),'Canvas background repaint: '+id+' '+theme);
  check(draws.some(c=>c.id==='cPrice' && c.method==='set:strokeStyle' && c.args[0]===styles.getPropertyValue('--chart-green').trim()),'Candle palette follows theme');
  check(draws.some(c=>c.id==='cPrice' && c.method==='set:strokeStyle' && c.args[0]===styles.getPropertyValue('--chart-vwap').trim()),'VWAP palette follows theme');
  check(draws.some(c=>c.id==='cVolume' && c.method==='set:fillStyle' && [styles.getPropertyValue('--chart-volume-up').trim(),styles.getPropertyValue('--chart-volume-down').trim()].includes(c.args[0])),'Volume bars follow theme');
  check(draws.some(c=>c.id==='cPane' && c.method==='set:fillStyle' && [styles.getPropertyValue('--chart-histogram-up').trim(),styles.getPropertyValue('--chart-histogram-down').trim()].includes(c.args[0])),'MACD histogram follows theme');
}
api.closeDrawer();click('#resetBtn');

// Other tabs update the preference without a storage feedback loop.
w.dispatchEvent(new w.StorageEvent('storage',{key:'radar-theme',newValue:'light',storageArea:w.localStorage}));
check(w.RadarTheme.current==='light','Cross-tab preference is applied');
check(w.localStorage.getItem('radar-theme')==='dark','Cross-tab update does not write back');
w.dispatchEvent(new w.StorageEvent('storage',{key:'unrelated',newValue:'dark'}));
check(w.RadarTheme.current==='light','Unrelated storage key is ignored');
w.dispatchEvent(new w.StorageEvent('storage',{key:null,newValue:null}));
check(w.RadarTheme.current==='dark','Clearing storage restores the default');

click('[data-sort="cmf"]');check(api.filtered()[0].liquidity.cmf>=api.filtered().at(-1).liquidity.cmf,'CMF descending sort');
const csv=api.csvText();check(csv.startsWith('\ufeff'),'UTF-8 CSV BOM');check(csv.includes('"cmf20"') && csv.includes('"volume_source"'),'CSV includes CMF and provenance');

const malicious=clone(synthetic);
malicious.signals[0].company='<img src=x onerror="window.radarInjected=1">';
malicious.signals[1].company='=HYPERLINK("https://evil.test","click")';
malicious.signals[0].news={warnings:[{title:'<script>alert(1)</script>',url:'javascript:alert(1)'}],catalysts:[]};
api.applyData(malicious);api.openDrawer(malicious.signals[0].ticker);
check(!doc.querySelector('#dbody a[href^="javascript:"]'),'Unsafe news URLs rejected');
check(!doc.querySelector('#gridBody img'),'Company HTML escaped');
check(!w.radarInjected,'No injected event handler');
check(api.csvText().includes("'=HYPERLINK"),'Spreadsheet formula injection guarded');
api.closeDrawer();

api.applyData({...clone(market),signals:[],count:0});check(rows().length===0,'Valid zero-signal result');
check($('gridEmpty').textContent.includes('لا أسهم'),'Zero-signal empty state');
const partial={updated_at:market.updated_at,signals:[{ticker:'OLD',price:.45,stage:'قيد المتابعة',readiness_score:30,chart:[]}],diagnostics:{}};
api.applyData(partial);api.openDrawer('OLD');check(doc.body.textContent.includes('بانتظار الحجم'),'Legacy schema degrades without false zero flow');api.closeDrawer();
api.applyData(market);
check(app.canvasCalls.length>50,'Canvas renderer exercised');
check(app.errors.length===0,'No uncaught browser errors: '+app.errors.join('; '));
app.dom.window.close();

const preview=await setup('index.html','https://radar.example/');
check(preview.requests.every(url=>new URL(url).pathname==='/docs/data.json'),'Root preview uses shared /docs assets and data');
check(preview.errors.length===0,'Root entry point has no script errors');
preview.dom.window.close();

for (const [entry,url] of [['docs/index.html','https://radar.example/stock-radar/'],['index.html','https://radar.example/'],['docs/settings.html','https://radar.example/stock-radar/settings.html']]) {
  const saved=await setup(entry,url,{savedTheme:'light'}), sw=saved.dom.window;
  check(sw.RadarTheme.current==='light','Saved light preference applies on boot: '+entry);
  check(sw.document.querySelector('[data-theme-toggle]').getAttribute('aria-pressed')==='true','Saved preference initializes control: '+entry);
  const html=fs.readFileSync(path.join(root,entry),'utf8');
  check(html.indexOf('theme.js')<html.indexOf('terminal.css') && !sw.document.querySelector('script[src$="theme.js"]').defer,'Preference applied before CSS, not deferred: '+entry);
  sw.document.querySelector('[data-theme-toggle]').click();
  check(sw.localStorage.getItem('radar-theme')==='dark','Preference can be changed: '+entry);
  check(saved.errors.length===0,'Theme boot has no script errors: '+entry);
  sw.close();
}
const blocked=await setup('docs/index.html','https://radar.example/stock-radar/',{denyStorage:true});
blocked.dom.window.document.querySelector('[data-theme-toggle]').click();
check(blocked.dom.window.RadarTheme.current==='light','Switch works when localStorage is denied');
check(blocked.errors.length===0,'Denied storage does not crash dashboard');blocked.dom.window.close();
const invalidPreference=await setup('docs/settings.html','https://radar.example/stock-radar/settings.html',{savedTheme:'invalid'});
check(invalidPreference.dom.window.RadarTheme.current==='dark','Invalid stored preference falls back safely');invalidPreference.dom.window.close();

// WCAG contrast checks cover text, semantic badges and meaningful chart graphics.
const css=fs.readFileSync(path.join(root,'docs/terminal.css'),'utf8');
const declarations=block=>Object.fromEntries(Array.from(block.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g),m=>[m[1],m[2].trim()]));
const dark=declarations(css.match(/:root\{([^}]+)\}/)[1]);
const light={...dark,...declarations(css.match(/:root\[data-theme="light"\]\{([^}]+)\}/)[1])};
const rgb=hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255);
const luminance=color=>color.reduce((sum,c,i)=>sum+[.2126,.7152,.0722][i]*(c<=.04045 ? c/12.92 : ((c+.055)/1.055)**2.4),0);
function contrast(fg,bg) {
  const a=fg.length===9 ? parseInt(fg.slice(7),16)/255 : 1, back=rgb(bg);
  const front=rgb(fg).map((c,i)=>c*a+back[i]*(1-a));
  const values=[luminance(front),luminance(back)].sort((a,b)=>a-b);
  return (values[1]+.05)/(values[0]+.05);
}
const surfaces=['bg','panel','surface','surface-top','surface-bottom','surface-header','surface-hover','surface-selected','surface-inset','selected-bg','selected-end'];
for (const [theme,palette] of [['dark',dark],['light',light]]) {
  function ratio(fg,bg,min=4.5) {const value=contrast(palette[fg],palette[bg]);check(value>=min,`${theme}: ${fg}/${bg} contrast ${value.toFixed(2)} >= ${min}`);}
  for (const fg of ['text','muted','dim','green','red','cyan','amber','violet']) for (const bg of surfaces) ratio(fg,bg);
  for (const role of ['positive','negative','warning','info','neutral','violet','selected']) ratio(role+'-text',role+'-bg');
  for (const bg of ['primary-start','primary-end']) ratio('primary-text',bg);
  ratio('check-icon-text','check-icon-bg');
  for (const fg of ['green','red','cyan','amber','violet','vwap','muted']) ratio('chart-'+fg,'chart-bg');
  for (const fg of ['chart-volume-up','chart-volume-down','chart-histogram-up','chart-histogram-down']) ratio(fg,'chart-bg',3);
  for (const fg of ['chart-green','chart-red']) ratio('chart-price-text',fg);
  for (const fg of ['funnel-top','funnel-bottom','funnel-cold-top','funnel-cold-bottom','green','green-deep']) ratio(fg,'surface-bottom',3);
  ratio('focus','bg',3);ratio('border-2','surface',3);
}
check(!/#[0-9a-fA-F]{3,8}\b/.test(css.slice(css.indexOf('*{box-sizing'))),'Components use semantic colors, not fixed dark literals');
check(!/#[0-9a-fA-F]{3,8}\b/.test(fs.readFileSync(path.join(root,'docs/terminal.js'),'utf8')),'Canvas/SVG colors come from the active palette');
const sw=fs.readFileSync(path.join(root,'docs/sw.js'),'utf8');
check(sw.includes("'./theme.js'") && sw.includes("'theme.js'") && !sw.includes('stock-radar-terminal-v3'),'Offline theme asset is precached and cache version updated');

console.log(`PASS: ${assertions} dashboard assertions; saved market data + positive/negative synthetic engine scenarios.`);
