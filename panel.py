"""Self-contained single-file panel (no external JS/CSS — platform-proxy-proof).

Served at /panel and as the SPA fallback for browser routes. Everything
(login, dashboard, wizard, instance pages, admin) is one HTML document with
inline CSS + JS talking to the existing JSON API.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Lunel Console</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cpath d='M16 2.5a13.5 13.5 0 1 0 13.06 17.02 11 11 0 0 1-14.58-14.58A13.6 13.6 0 0 1 16 2.5Z' fill='%23d8e0ee'/%3E%3C/svg%3E">
<style>
:root{--bg:#0a0c10;--bg2:#10131a;--sur:#12151c;--sur2:#171b24;--bd:#1e2430;--bd2:#2a3242;
--tx:#e7ebf3;--dim:#9aa4b8;--fnt:#5d6678;--acc:#d8e0ee;--accd:#0b0d11;--blu:#6f9bff;
--grn:#4ecb95;--amb:#e3b341;--red:#ef6b73;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;--r:10px;--rs:7px}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--blu);text-decoration:none}
.shell{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
.sb{border-right:1px solid var(--bd);background:var(--bg2);padding:20px 12px;display:flex;flex-direction:column;gap:2px;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:9px;padding:2px 8px 16px}
.bm{width:24px;height:24px;color:var(--acc);flex:none}
.bn{font-weight:650;font-size:15px;letter-spacing:.4px}
.ni{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:var(--rs);color:var(--dim);font-weight:500;cursor:pointer;border:1px solid transparent;background:none;width:100%;text-align:left;font-size:13px;font-family:inherit}
.ni:hover{color:var(--tx);background:var(--sur)}
.ni.act{color:var(--tx);background:var(--sur2);border-color:var(--bd)}
.ni svg{width:15px;height:15px}
.sbft{margin-top:auto;padding-top:10px;border-top:1px solid var(--bd);display:flex;align-items:center;gap:8px}
.sbft .who{min-width:0}.sbft .who b{display:block;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sbft .who span{font-size:11px;color:var(--fnt)}
.main{min-width:0}.ct{padding:26px 30px 70px;max-width:1100px;margin:0 auto}
.topbar{display:none}
.menu-btn{display:none}
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:55;display:none}
.scrim.on{display:block}
@media(max-width:840px){
  .shell{grid-template-columns:1fr}
  .menu-btn{display:inline-flex;margin-left:auto}
  .sb{display:flex;position:fixed;top:0;left:0;bottom:0;width:250px;z-index:60;
      transform:translateX(-105%);transition:transform .22s ease;box-shadow:none}
  .sb.open{transform:translateX(0);box-shadow:0 0 44px rgba(0,0,0,.55)}
.topbar{display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:30;background:rgba(10,12,16,.94);border-bottom:1px solid var(--bd);padding:12px 14px}
.ct{padding:16px 12px 90px}
.bnav{display:flex;position:fixed;bottom:0;left:0;right:0;z-index:30;background:rgba(13,16,22,.97);border-top:1px solid var(--bd);padding:6px 6px calc(6px + env(safe-area-inset-bottom))}
.bnav .ni{flex:1;flex-direction:column;gap:2px;font-size:10px;align-items:center;padding:6px 0}}
.bnav{display:none}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:8px 13px;border-radius:var(--rs);border:1px solid var(--bd2);background:var(--sur2);color:var(--tx);font:600 13px var(--sans);cursor:pointer;white-space:nowrap}
.btn:hover{background:#1c212c;border-color:#37415a}.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.pri{background:var(--acc);border-color:var(--acc);color:var(--accd)}.btn.pri:hover{background:#e6ecf7}
.btn.dng{color:var(--red);border-color:rgba(239,107,115,.35)}.btn.dng:hover{background:rgba(239,107,115,.09)}
.btn.sm{padding:5px 9px;font-size:12px}
.inp{width:100%;padding:9px 12px;background:var(--bg2);color:var(--tx);border:1px solid var(--bd2);border-radius:var(--rs);font:400 13.5px var(--sans)}
.inp:focus{outline:none;border-color:var(--blu);box-shadow:0 0 0 3px rgba(111,155,255,.15)}
.fld{margin-bottom:14px}.fld label{display:block;font-size:12px;font-weight:600;color:var(--dim);margin-bottom:5px}
.card{background:var(--sur);border:1px solid var(--bd);border-radius:var(--r);padding:16px}
.card h3{margin:0 0 6px;font-size:13.5px}
.sgs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:22px}
@media(max-width:840px){.sgs{grid-template-columns:repeat(2,1fr)}}
.sg{background:var(--sur);border:1px solid var(--bd);border-radius:var(--r);padding:12px 14px}
.sg .l{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--fnt)}
.sg .v{font:650 24px var(--mono);margin-top:2px}
.st{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600}
.st .d{width:8px;height:8px;border-radius:50%;background:var(--fnt)}
.st.run .d{background:var(--grn);animation:pu 2.2s infinite}
.st.fail .d{background:var(--red)}.st.sto .d{background:var(--fnt)}.st.tr .d{background:var(--amb)}
@keyframes pu{0%{box-shadow:0 0 0 0 rgba(78,203,149,.45)}70%{box-shadow:0 0 0 6px rgba(78,203,149,0)}100%{box-shadow:0 0 0 0 rgba(78,203,149,0)}}
.ig{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:11px}
.ic{background:var(--sur);border:1px solid var(--bd);border-radius:var(--r);padding:14px;cursor:pointer;display:flex;flex-direction:column;gap:9px;transition:border-color .12s}
.ic:hover{border-color:var(--bd2)}
.ic .t{display:flex;align-items:center;justify-content:space-between;gap:8px}
.ic .nm{font-size:14.5px;font-weight:650}
.ic .ep{font-family:var(--mono);font-size:11px;color:var(--dim);background:var(--bg2);border:1px solid var(--bd);padding:5px 7px;border-radius:var(--rs);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.ic .mt{display:flex;gap:12px;color:var(--fnt);font-size:11.5px;flex-wrap:wrap}
.ph{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.ph h1{margin:0;font-size:20px}.ph .sub{color:var(--dim);margin-top:3px;font-size:13px}
.ha{display:flex;gap:7px;flex-wrap:wrap}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--bd);margin-bottom:18px;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:8px 12px;font-size:12.5px;font-weight:600;color:var(--fnt);border:none;background:none;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap;font-family:inherit}
.tab.act{color:var(--tx);border-bottom-color:var(--acc)}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--fnt);padding:7px 10px;border-bottom:1px solid var(--bd)}
.tbl td{padding:9px 10px;border-bottom:1px solid var(--bd)}
.tbl tr:last-child td{border-bottom:none}
.term{background:#07090c;border:1px solid var(--bd);border-radius:var(--r);font-family:var(--mono);font-size:11.5px;overflow:hidden}
.tb{display:flex;gap:7px;align-items:center;padding:7px 9px;border-bottom:1px solid var(--bd);background:var(--sur);flex-wrap:wrap}
.tb .sp{flex:1}
.tbody{height:400px;overflow:auto;padding:9px 11px}
.ll{white-space:pre-wrap;word-break:break-all}.ll .t{color:var(--fnt)}.ll .lv{font-weight:700}
.ll.info .lv{color:var(--blu)}.ll.warning .lv,.ll.warn .lv{color:var(--amb)}.ll.error .lv{color:var(--red)}.ll.ok .lv{color:var(--grn)}
.te{color:var(--fnt);padding:26px;text-align:center}
.empty{text-align:center;padding:44px 16px;color:var(--dim);border:1px dashed var(--bd2);border-radius:var(--r)}
.empty b{display:block;color:var(--tx);margin-bottom:3px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.kv .it{background:var(--bg2);border:1px solid var(--bd);border-radius:var(--rs);padding:9px 11px}
.kv .k{font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--fnt)}
.kv .v{font-family:var(--mono);font-size:13.5px;margin-top:2px}
.optg{display:grid;grid-template-columns:1fr 1fr;gap:9px}
@media(max-width:640px){.optg{grid-template-columns:1fr}}
.opt{border:1px solid var(--bd2);border-radius:var(--rs);padding:11px 13px;cursor:pointer;background:var(--bg2)}
.opt.sel{border-color:var(--acc);background:var(--sur2)}
.opt .t{font-weight:650;font-size:13px}.opt .d{font-size:11.5px;color:var(--fnt);margin-top:2px}
.mono{font-family:var(--mono);font-size:12px}
.mut{color:var(--dim)}.ftx{color:var(--fnt)}
.row{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.grow{flex:1}
.chip{display:inline-flex;padding:1px 7px;border:1px solid var(--bd2);border-radius:999px;font-size:11px;color:var(--dim);font-family:var(--mono)}
.tw{position:fixed;bottom:18px;right:18px;z-index:100;display:flex;flex-direction:column;gap:7px}
.to{background:var(--sur2);border:1px solid var(--bd2);border-left:3px solid var(--blu);border-radius:var(--rs);padding:9px 13px;min-width:220px;max-width:340px;font-size:12.5px;box-shadow:0 8px 24px rgba(0,0,0,.35)}
.to.ok{border-left-color:var(--grn)}.to.err{border-left-color:var(--red)}
.sp1{width:15px;height:15px;border:2px solid var(--bd2);border-top-color:var(--acc);border-radius:50%;animation:sp .7s linear infinite;display:inline-block}
@keyframes sp{to{transform:rotate(360deg)}}
.lw{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:18px}
.lc{width:360px;max-width:100%;text-align:center}
.lc h2{margin:8px 0 0;font-size:21px}
.lc .p{color:var(--dim);font-size:13px;margin:8px 0 20px}
.lc .card{padding:24px 22px;text-align:left}
.fn{color:var(--fnt);font-size:11px;margin-top:14px}
.copy{border:none;background:none;color:var(--fnt);cursor:pointer;font-family:var(--mono);font-size:11px;padding:2px 4px}
.copy:hover{color:var(--tx)}
.qr-ov{position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:200}
.qr-c{background:var(--sur);border:1px solid var(--bd2);border-radius:12px;padding:20px;text-align:center;max-width:340px}
.qr-c .qrbox svg{width:240px;height:240px;display:block;margin:8px auto;background:#fff;border-radius:8px}
.free{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;letter-spacing:1px;font-weight:700;color:var(--grn);border:1px solid rgba(78,203,149,.4);border-radius:999px;padding:2px 9px;text-transform:uppercase}
</style>
</head>
<body>
<div id="app"><div class="lw"><span class="sp1"></span></div></div>
<script>
(function(){"use strict";
// ───────────────────────────── helpers ─────────────────────────────
var CSRF="";
function $(s,el){return (el||document).querySelector(s)}
function esc(s){var d=document.createElement("div");d.textContent=s==null?"":String(s);return d.innerHTML}
function toast(msg,kind,ms){var w=$(".tw");if(!w){w=document.createElement("div");w.className="tw";document.body.appendChild(w)}
var e=document.createElement("div");e.className="to "+(kind||"");e.textContent=msg;w.appendChild(e);setTimeout(function(){e.remove()},ms||3500)}
function fmtBytes(n){if(n==null)return"—";if(n<1024)return n+" B";if(n<1048576)return(n/1024).toFixed(1)+" KB";if(n<1073741824)return(n/1048576).toFixed(2)+" MB";return(n/1073741824).toFixed(2)+" GB"}
function fmtUp(s){if(s==null)return"—";var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);if(d>0)return d+"d "+h+"h";if(h>0)return h+"h "+m+"m";if(m>0)return m+"m "+s+"s";return s+"s"}
function ago(iso){if(!iso)return"—";var t=new Date(iso),df=(Date.now()-t.getTime())/1e3;if(df<60)return"just now";if(df<3600)return Math.floor(df/60)+"m ago";if(df<86400)return Math.floor(df/3600)+"h ago";return t.toLocaleDateString(undefined,{month:"short",day:"numeric"})}
function dur(ms){if(ms==null)return"—";if(ms<1e3)return ms+"ms";if(ms<6e4)return(ms/1e3).toFixed(1)+"s";return Math.floor(ms/6e4)+"m"}
var LBL={queued:"Queued",preparing:"Preparing",building:"Building",starting:"Starting",health_check:"Health check",running:"Running",failed:"Failed",stopping:"Stopping",stopped:"Stopped",deleted:"Deleted",online:"Running",offline:"Stopped",unknown:"—"};
var BUSY={queued:1,preparing:1,building:1,starting:1,health_check:1,stopping:1};
function stEl(s){var cls=s==="running"||s==="online"?"run":(s==="failed"?"fail":(s==="stopped"||s==="offline"||s==="deleted"?"sto":(s==="stopping"?"sto":"tr")));
var sp=document.createElement("span");sp.className="st "+cls;sp.innerHTML='<span class="d"></span>'+(LBL[s]||s);return sp}
function copyBtn(text){var b=document.createElement("button");b.className="copy";b.textContent="copy";
b.onclick=function(e){e.stopPropagation();if(navigator.clipboard){navigator.clipboard.writeText(text).then(function(){toast("Copied","ok",1200)})}else{toast("Copy not supported","err")}};return b}
// ───────────────────────────── api ─────────────────────────────
function api(method,path,body,retry){
  var h={"Content-Type":"application/json"};
  if(CSRF)h["X-Lunel-CSRF"]=CSRF;
  return fetch(path,{method:method,headers:h,credentials:"same-origin",body:body!==undefined?JSON.stringify(body):undefined})
  .then(function(r){
    // 429 = too fast; wait what the server asks (or 2s) and retry silently
    if(r.status===429&&(retry||0)<3){
      var wait=parseInt(r.headers.get("Retry-After")||"2",10)||2;
      return new Promise(function(res){setTimeout(res,wait*1e3)}).then(function(){return api(method,path,body,(retry||0)+1)});
    }
    return r.json().catch(function(){return{}}).then(function(d){
    if(!r.ok){
      if(r.status===401&&!(retry)&&!path.startsWith("/auth")){
        // confirm the session really died before bouncing the user
        return fetch("/auth/me",{credentials:"same-origin"}).then(function(m){return m.json()}).then(function(me){
          if(me.authenticated){CSRF=me.csrf_token;return api(method,path,body,3)}
          USER=null;render();throw new Error("please sign in again");
        });
      }
      throw new Error((d&&d.detail)||("HTTP "+r.status));
    }
    return d})})}

// ───────────────────────────── icons ─────────────────────────────
function ic(n){var p={dash:'<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
plus:'<path d="M12 5v14M5 12h14"/>',gear:'<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/>',
gh:'<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" fill="currentColor" stroke="none"/>',
tg:'<path d="M21.9 4.6 18.9 19c-.2 1-.8 1.2-1.7.8l-4.6-3.4-2.2 2.1c-.3.3-.5.5-1 .5l.4-4.7L18.6 6c.4-.3-.1-.5-.6-.2L7.3 12.4l-4.3-1.4c-.9-.3-.9-.9.2-1.3L20.7 3.3c.8-.3 1.5.2 1.2 1.3Z" fill="currentColor" stroke="none"/>'};
return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" style="width:15px;height:15px">'+p[n]+"</svg>"}
var MARK='<svg class="bm" viewBox="0 0 32 32" fill="none"><path d="M16 2.5a13.5 13.5 0 1 0 13.06 17.02 11 11 0 0 1-14.58-14.58A13.6 13.6 0 0 1 16 2.5Z" fill="currentColor"/><path d="M4 29.5h24" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

// ───────────────────────────── shell/state ─────────────────────────────
var USER=null, cleanup=null, pollTimer=null, LINKS={github:"https://github.com/ArasTey/lunel",telegram:""};
function setCleanup(fn){if(cleanup)cleanup();cleanup=fn||null}
function stopPoll(){if(pollTimer){clearInterval(pollTimer);pollTimer=null}if(hiddenTimer){clearInterval(hiddenTimer);hiddenTimer=null}}
var hiddenTimer=null;
function every(ms,fn){return setInterval(function(){if(!document.hidden)fn()},ms)}
function shell(nav){
  stopPoll();setCleanup(null);
  var app=$("#app");
  app.innerHTML='<div class="shell"><aside class="sb">'+
    '<div class="brand">'+MARK+'<div><div class="bn">Lunel</div><div style="font-size:10px;color:var(--fnt);letter-spacing:1.2px">CONSOLE</div></div></div>'+
    '<button class="ni '+(nav==="dash"?"act":"")+'" data-nav="dash">'+ic("dash")+' Dashboard</button>'+
    '<button class="ni '+(nav==="new"?"act":"")+'" data-nav="new">'+ic("plus")+' Create Instance</button>'+
    (USER.is_admin?'<button class="ni '+(nav==="admin"?"act":"")+'" data-nav="admin">'+ic("gear")+' Admin</button>':"")+
    (LINKS.github?'<a class="ni" href="'+LINKS.github+'" target="_blank" rel="noopener">'+ic("gh")+' GitHub</a>':"")+
    (LINKS.telegram?'<a class="ni" href="'+esc(LINKS.telegram)+'" target="_blank" rel="noopener">'+ic("tg")+' Telegram</a>':"")+
    '<div style="padding:6px 8px"><span class="free">● Free</span></div>'+
    '<div class="sbft"><div class="who"><b>'+esc(USER.name||USER.login)+'</b><span>@'+esc(USER.login)+'</span></div>'+
    '<button class="btn sm" style="margin-left:auto" id="lg">Sign out</button></div></aside>'+
    '<div class="main"><div class="topbar">'+MARK+'<b style="font-size:14px">Lunel</b>'+
    '<button class="btn sm" style="margin-left:auto" id="lgm">Sign out</button></div>'+
    '<div class="ct" id="view"></div>'+
    '<nav class="bnav"><button class="ni '+(nav==="dash"?"act":"")+'" data-nav="dash">'+ic("dash")+'<span>Home</span></button>'+
    '<button class="ni '+(nav==="new"?"act":"")+'" data-nav="new">'+ic("plus")+'<span>Create</span></button>'+
    (USER.is_admin?'<button class="ni '+(nav==="admin"?"act":"")+'" data-nav="admin">'+ic("gear")+'<span>Admin</span></button>':"")+
    '</nav></div></div>';
  var lg=$("#lg");if(lg)lg.onclick=logout;
  var lgm=$("#lgm");if(lgm)lgm.onclick=logout;
  var sc=document.createElement("div");sc.className="scrim";document.body.appendChild(sc);
  var sb=$(".sb"),tg=$("#mb");
  if(tg){tg.onclick=function(){sb.classList.toggle("open");sc.classList.toggle("on",sb.classList.contains("open"))}}
  if(sc)sc.onclick=function(){sb.classList.remove("open");sc.classList.remove("on")};
  window.__closeDrawer=function(){sb.classList.remove("open");sc.classList.remove("on")};
  Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"),function(b){
    b.onclick=function(){window.__closeDrawer();nav_(b.dataset.nav)}});
}
function nav_(name){stopPoll();setCleanup(null);
  if(name==="dash")viewDash();else if(name==="new")viewWizard();else if(name==="admin")viewAdmin()}
function logout(){api("POST","/auth/logout").then(function(){render()})}
function closeDrawer(){var w=window.__closeDrawer;if(w)w()}
// ───────────────────────────── login ─────────────────────────────
function viewLogin(){
  stopPoll();setCleanup(null);
  $("#app").innerHTML='<div class="lw"><div class="lc"><div class="card">'+
    '<div style="text-align:center">'+MARK+'<h2>Lunel</h2>'+
    '<p class="p" style="text-align:center">Deploy and manage multi-protocol proxy instances.</p></div>'+
    '<div class="fld"><label>Account name</label><input class="inp" id="u" placeholder="admin" autocomplete="username"></div>'+
    '<div class="fld"><label>Password</label><input class="inp" id="p" type="password" autocomplete="current-password"></div>'+
    '<button class="btn pri" id="go" style="width:100%">Sign in</button>'+
    '<p class="fn">Default account is <span class="mono">admin / admin</span> — change it in Admin → System.</p>'+
    '</div></div></div>';
  $("#go").onclick=function(){
    var b=$("#go");b.disabled=true;
    api("POST","/auth/login-password",{name:$("#u").value.trim()||"admin",password:$("#p").value})
    .then(function(){render()}).catch(function(e){b.disabled=false;toast(e.message,"err")});
  };
  $("#p").addEventListener("keydown",function(e){if(e.key==="Enter")$("#go").click()});
}
// ───────────────────────────── dashboard ─────────────────────────────
function viewDash(){
  shell("dash");
  var v=$("#view");
  v.innerHTML='<div class="ph"><div><h1>Dashboard</h1><div class="sub">Your Lunel instances at a glance.</div></div>'+
    '<div class="ha"><button class="btn pri" data-go="new">+ Create Instance</button></div></div>'+
    '<div class="sgs" id="sgs"></div><h3 style="margin:0 0 10px;font-size:13.5px">Instances</h3><div id="il"></div>'+
    '<div class="card" style="margin-top:22px"><h3>Recent activity</h3><div id="ac" class="mut">—</div></div>';
  Array.prototype.forEach.call(v.querySelectorAll("[data-go]"),function(b){b.onclick=function(){nav_(b.dataset.go)}});
  var t=null;
  function load(){
    return Promise.all([api("GET","/api/instances"),api("GET","/api/activity")]).then(function(rs){
      var list=rs[0].instances, act=rs[1].activity;
      var run=0,sto=0,fail=0;list.forEach(function(i){if(i.status==="running")run++;else if(i.status==="failed")fail++;else sto++});
      $("#sgs").innerHTML=sg("Active instances",list.length)+sg("Running",run,"var(--grn)")+sg("Stopped",sto)+sg("Failed",fail,fail?"var(--red)":null);
      var il=$("#il");
      if(!list.length){il.innerHTML='<div class="empty"><b>No instances yet</b>Deploy your first one in under a minute.<div style="margin-top:14px"><button class="btn pri" data-go="new">Create your first instance</button></div></div>'}
      else{il.innerHTML='<div class="ig">'+list.map(card).join("")+"</div>";
        Array.prototype.forEach.call(il.querySelectorAll("[data-id]"),function(c){c.onclick=function(){viewInst(c.dataset.id)}})}
      $("#ac").innerHTML=act.length?act.slice(0,8).map(function(a){return '<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--bd)"><span class="ftx mono" style="width:64px;flex:none">'+ago(a.ts)+'</span><span class="mut">'+esc(a.message)+"</span></div>"}).join(""):"Nothing yet.";
      var go=v.querySelector(".empty [data-go]");if(go)go.onclick=function(){nav_("new")};
      return list;
    });
  }
  function sg(l,v,c){return '<div class="sg"><div class="l">'+l+'</div><div class="v" style="'+(c?"color:"+c:"")+'">'+v+"</div></div>"}
  function card(i){
    var ep=i.endpoint_url||(i.domain&&i.domain.indexOf("-")>0&&i.domain.length>30?null:null);
    return '<div class="ic" data-id="'+i.id+'"><div class="t"><span class="nm">'+esc(i.name)+"</span>"+stEl(i.status).outerHTML+"</div>"+
      (i.endpoint_url?'<div class="ep">'+esc(i.endpoint_url)+"</div>":'<div class="ep ftx">no endpoint yet</div>')+
      '<div class="mt"><span>'+esc(i.region)+"</span><span>"+i.deployments_count+' deploys</span><span>created '+ago(i.created_at)+"</span></div></div>";
  }
  load().then(function(list){
    pollTimer=every(6000,function(){
      if(list.some(function(i){return BUSY[i.status]}))load();
    });
  });
}
// ───────────────────────────── wizard ─────────────────────────────
var PROTOS=[["vless-ws","VLESS over WebSocket","Widest client support (v2rayNG, NekoBox). Recommended."],
["trojan-ws","Trojan over WebSocket","TLS-like handshake, good under strict DPI."],
["shadowsocks","Shadowsocks AEAD","Lightweight AEAD (chacha20 / aes-gcm) over WebSocket."],
["xhttp-packet-up","VLESS xHTTP (packet-up)","HTTP-native transport, resists connection shaping."]];
function viewWizard(){
  shell("new");
  var m={name:"",region:"local",protocol:"vless-ws",cpu:0.5,mem:256},step=0;
  var v=$("#view");
  v.innerHTML='<div class="ph"><div><h1>Create Instance</h1><div class="sub">Name it, pick a protocol, deploy. No servers, no YAML.</div></div></div>'+
    '<div class="row" id="stb" style="gap:4px;margin-bottom:20px"></div><div class="card" id="sb"></div>'+
    '<div class="row" style="margin-top:18px"><button class="btn" id="bk">Back</button><div class="grow"></div><button class="btn pri" id="nx">Continue</button></div>';
  var steps=["Name","Region","Config","Networking","Review","Deploy"];
  function bar(){ $("#stb").innerHTML=steps.map(function(s,i){return '<div class="grow" style="height:3px;border-radius:2px;background:'+(i<step?"var(--acc)":i===step?"var(--blu)":"var(--bd)")+'"></div>'}).join("")}
  function show(){
    bar();var b=$("#sb");
    $("#bk").disabled=step===0;$("#nx").textContent=step===4?"Deploy":step===5?"Go to instance":"Continue";
    $("#nx").classList.toggle("pri",step!==5);
    if(step===0){b.innerHTML='<h3 style="margin:0 0 10px">Step 1 — Name</h3><div class="fld"><label>Instance name</label><input class="inp" id="f-n" maxlength="60" placeholder="e.g. Production" value="'+esc(m.name)+'"></div><div class="ftx" style="font-size:12px">Letters, numbers, dashes. Up to 25 instances per account.</div>';
      $("#f-n").oninput=function(e){m.name=e.target.value}}
    else if(step===1){b.innerHTML='<h3 style="margin:0 0 10px">Step 2 — Region</h3><div class="optg" id="rg"><div class="opt sel" data-id="local"><div class="t">Local node</div><div class="d">Default worker on this platform</div></div></div>';
      Array.prototype.forEach.call(b.querySelectorAll(".opt"),function(o){o.onclick=function(){m.region=o.dataset.id;Array.prototype.forEach.call(b.querySelectorAll(".opt"),function(x){x.classList.toggle("sel",x===o)})}})}
    else if(step===2){b.innerHTML='<h3 style="margin:0 0 10px">Step 3 — Protocol & resources</h3><div class="fld"><div class="optg">'+PROTOS.map(function(p){return '<div class="opt '+(m.protocol===p[0]?"sel":"")+'" data-id="'+p[0]+'"><div class="t">'+p[1]+'</div><div class="d">'+p[2]+"</div></div>"}).join("")+'</div></div><div class="row"><div class="fld" style="width:160px;margin:0"><label>CPU (cores)</label><select class="inp" id="f-c">'+[0.25,0.5,1,2,4].map(function(x){return '<option value="'+x+'" '+(m.cpu===x?"selected":"")+">"+x+"</option>"}).join("")+'</select></div><div class="fld" style="width:160px;margin:0"><label>Memory</label><select class="inp" id="f-m">'+[128,256,512,1024,2048].map(function(x){return '<option value="'+x+'" '+(m.mem===x?"selected":"")+">"+x+" MB</option>"}).join("")+"</select></div></div>";
      Array.prototype.forEach.call(b.querySelectorAll(".opt"),function(o){o.onclick=function(){
        var i=m.protocols.indexOf(o.dataset.id);
        if(i>=0){if(m.protocols.length>1){m.protocols.splice(i,1);o.classList.remove("sel")}}
        else{m.protocols.push(o.dataset.id);o.classList.add("sel")}}});
      $("#f-c").onchange=function(e){m.cpu=parseFloat(e.target.value)};$("#f-m").onchange=function(e){m.mem=parseInt(e.target.value,10)}}
    else if(step===3){b.innerHTML='<h3 style="margin:0 0 10px">Step 4 — Networking</h3><div class="card" style="background:var(--bg2)"><div class="row"><span class="chip">https</span><span class="mono">&lt;console-host&gt;/i/&lt;private-token&gt;</span></div><p class="ftx" style="margin:9px 0 0;font-size:12.5px">WebSocket, xHTTP and all Lunel protocols work through this endpoint with automatic TLS. Ready on deploy.</p></div>'}
    else if(step===4){b.innerHTML='<h3 style="margin:0 0 10px">Step 5 — Review</h3><table class="tbl"><tr><td style="color:var(--fnt);width:40%">Name</td><td class="mono">'+(esc(m.name)||"—")+"</td></tr><tr><td style='color:var(--fnt)'>Region</td><td class='mono'>"+esc(m.region)+"</td></tr><tr><td style='color:var(--fnt)'>Protocols</td><td class='mono'>"+esc(m.protocols.join(", "))+"</td></tr><tr><td style='color:var(--fnt)'>CPU / Memory</td><td class='mono'>"+m.cpu+" core / "+m.mem+" MB</td></tr></table>"}
    else if(step===5){b.innerHTML='<h3 style="margin:0 0 10px">Step 6 — Deploy</h3><div class="kv"><div class="it"><div class="k">Status</div><div class="v" id="ds">Deploying…</div></div><div class="it"><div class="k">Deployment</div><div class="v" id="di">—</div></div></div><div class="term" style="margin-top:14px"><div class="tbody" id="dl" style="height:220px"><div class="ll"><span class="t">»</span> queued</div></div></div>'}
  }
  $("#bk").onclick=function(){if(step>0&&step!==5){step--;show()}};
  $("#nx").onclick=function(){
    if(step===0){if(m.name.trim().length<2){toast("Give the instance a name (2+ chars)","err");return}step=1}
    else if(step===4){step=5;show();$("#nx").disabled=true;
      api("POST","/api/instances",{name:m.name,region:m.region,config:{protocol:m.protocol,cpu_limit:m.cpu,memory_mb:m.mem}})
      .then(function(created){return api("POST","/api/instances/"+created.id+"/deploy").then(function(d){return{c:created,d:d}})})
      .then(function(r){
        $("#di").textContent=r.d.deployment_id.slice(0,8);var seen=0;
        pollTimer=every(2000,function(){
          if(document.hidden)return;
          Promise.all([api("GET","/api/instances/"+r.c.id+"/deployments/"+r.d.deployment_id+"/logs"),api("GET","/api/instances/"+r.c.id+"/deployments")])
          .then(function(rs){
            var logs=rs[0].logs;for(;seen<logs.length;seen++){var e=document.createElement("div");e.className="ll "+logs[seen].level;e.innerHTML='<span class="lv">'+logs[seen].level+"</span> "+esc(logs[seen].message);var dl=$("#dl");if(dl){dl.appendChild(e);dl.scrollTop=dl.scrollHeight}}
            var dep=(rs[1].deployments||[]).filter(function(d){return d.id===r.d.deployment_id})[0];
            if(dep){$("#ds").textContent=dep.status.replace("_"," ");
              if(dep.status==="running"){$("#ds").style.color="var(--grn)";stopPoll();$("#nx").disabled=false;$("#nx").textContent="Go to instance";$("#nx").onclick=function(){viewInst(r.c.id)};toast("Instance is running","ok")}
              else if(dep.status==="failed"){$("#ds").style.color="var(--red)";stopPoll();$("#nx").disabled=false;$("#nx").textContent="Retry";$("#nx").onclick=function(){viewInst(r.c.id)};toast("Deployment failed: "+(dep.error||"unknown"),"err",8000)}}
          }).catch(function(){});
        },1500);
      }).catch(function(e){toast(e.message,"err",6000);step=4;show();$("#nx").disabled=false});
      return}
    else if(step<5)step+=1;
    show();
  };
  show();
}
// ───────────────────────────── instance page ─────────────────────────────
var TABS=["config","overview","logs","networking","deployments","activity","settings"];
function viewInst(id){
  shell("dash");
  var v=$("#view");v.innerHTML='<div class="lw"><span class="sp1"></span></div>';
  var inst=null,tab="overview",logPaused=false,logBuf=[];
  function head(){
    var pd=(inst.domains||[]).filter(function(d){return d.kind==="path"})[0];
    v.innerHTML='<div class="ph"><div><div class="row" style="gap:11px"><h1>'+esc(inst.name)+"</h1>"+stEl(inst.status).outerHTML+'</div><div class="sub" id="ep"></div></div>'+
      '<div class="ha"><button class="btn" id="a-r">Restart</button><button class="btn" id="a-s">Stop</button><button class="btn" id="a-rd">Redeploy</button><button class="btn dng" id="a-d">Delete</button></div></div>'+
      '<div class="tabs">'+TABS.map(function(t){return '<button class="tab '+(t===tab?"act":"")+'" data-t="'+t+'">'+t[0].toUpperCase()+t.slice(1)+"</button>"}).join("")+'</div><div id="tb"></div>';
    var epEl=$("#ep");
    if(pd){var url=location.origin+"/i/"+pd.domain;
      epEl.innerHTML='<span class="mono">'+esc(url)+"</span> ";epEl.appendChild(copyBtn(url));var a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener";a.textContent="open ↗";epEl.appendChild(a)}
    else epEl.textContent="no endpoint yet — deploy the instance";
    var busy=BUSY[inst.status];["a-r","a-s","a-rd","a-d"].forEach(function(x){var b=$("#"+x);if(b)b.disabled=!!busy});
    $("#a-r").onclick=function(){act("restart")};$("#a-s").onclick=function(){act("stop")};$("#a-rd").onclick=function(){act("redeploy")};
    $("#a-d").onclick=function(){if(confirm('Delete "'+inst.name+'"? This is permanent.')){api("DELETE","/api/instances/"+id).then(nav_("dash")).catch(function(e){toast(e.message,"err")})}};
    Array.prototype.forEach.call(v.querySelectorAll(".tab"),function(b){b.onclick=function(){tab=b.dataset.t;head();draw()}});
  }
  function act(k){api("POST","/api/instances/"+id+"/"+k).then(function(){toast({restart:"Restarting…",stop:"Stopping…",redeploy:"Redeploying…"}[k],"ok");refresh()}).catch(function(e){toast(e.message,"err")})}
  function refresh(){return api("GET","/api/instances/"+id).then(function(d){inst=d;head();draw()})}
  function draw(){
    var b=$("#tb");if(!b)return;
    if(tab==="config"){
      b.innerHTML='<div class="card"><div class="row" style="justify-content:space-between"><h3>Subscription <span class="free" style="margin-left:6px">Free</span></h3><button class="btn sm" id="cf-r">Refresh</button></div>'+
        '<p class="mut" style="font-size:12.5px;margin:6px 0 10px">One URL, <b>all 4 protocols</b> (VLESS, Trojan, Shadowsocks, xHTTP). Add it under Subscriptions in your client — it auto-updates.</p>'+
        '<div class="row"><div class="mono grow" id="suburl" style="background:var(--bg2);border:1px solid var(--bd);border-radius:7px;padding:8px 10px;word-break:break-all"></div><button class="btn sm pri" id="subc">Copy</button><a class="btn sm" id="subo" target="_blank" rel="noopener">Open</a></div>'+
        '<div class="row" style="margin-top:9px;gap:6px"><span class="ftx" style="font-size:11.5px">Formats:</span>'+
        '<button class="btn sm" id="sub-v2">v2ray/Clash Verge</button><button class="btn sm" id="sub-sb">sing-box</button><button class="btn sm" id="sub-cl">Clash Meta</button></div>'+
        '<div class="card" style="margin-top:14px"><div class="row" style="justify-content:space-between"><h3>Individual configs</h3><button class="btn sm" id="cf-r">Refresh</button></div><div id="cf-b" class="mut">Loading…</div></div>';
      function loadCfg(){
        // tell the server the public host we're browsing on (edge hides it)
        api("POST","/api/instances/"+id+"/announce-host",{host:location.host}).catch(function(){});
        $("#cf-b").innerHTML='<span class="mut">Loading…</span>';
        api("GET","/api/instances/"+id+"/config").then(function(d){
          var subUrl=location.origin+"/i/"+(d.endpoint_path||"").replace("/i/","")+"/sub";
          if(d.endpoint_path){$("#suburl").textContent=subUrl;
            $("#subc").onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(subUrl).then(function(){toast("Subscription URL copied","ok",2500)})};
            $("#subo").href=subUrl+"?host="+location.host;
            var v2=location.origin+"/i/"+d.endpoint_path.split("/i/")[1]+"/sub?host="+location.host;
            $("#sub-v2").onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(v2).then(function(){toast("v2ray sub URL copied","ok",2500)})};
            $("#sub-sb").onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(v2+"&fmt=singbox").then(function(){toast("sing-box sub URL copied","ok",2500)})};
            $("#sub-cl").onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(v2+"&fmt=clash").then(function(){toast("Clash sub URL copied","ok",2500)})};}
          if(!d.configs||!d.configs.length){
            $("#cf-b").innerHTML='<span class="ftx">'+esc(d.error||"No configs yet — if the instance shows Running, press Redeploy once (instances created before this fix get their links on redeploy).")+"</span>";
            return;
          }
          var pubHost=location.host;
          $("#cf-b").innerHTML=d.configs.map(function(c,idx){
            var url=c.share_url;
            var m=c.share_url.match(/^(vless|trojan):\/\/([^@]+)@([^\/?#]+)([^#]*)/);
            if(m){
              var proto=m[1],cred=m[2],inner=m[3],rest=m[4]||"";
              var innerHost=inner.split(":")[0];
              if(innerHost==="127.0.0.1"||innerHost==="localhost"||innerHost==="0.0.0.0"){
                url=proto+"://"+cred+"@"+pubHost+rest;
              }
            }
            return '<div style="margin-top:12px"><div class="row" style="justify-content:space-between"><b style="font-size:12.5px">'+esc(c.label)+'</b><span class="chip">'+esc(c.protocol)+"</span></div>"+
              '<div class="mono" style="margin-top:5px;background:var(--bg2);border:1px solid var(--bd);border-radius:7px;padding:8px 10px;word-break:break-all;max-height:90px;overflow:auto">'+esc(url)+"</div>"+
              '<div class="row" style="margin-top:6px"><button class="btn sm pri" data-copy="'+esc(url)+'">Copy</button><button class="btn sm" data-qr="'+esc(url)+'">QR</button></div></div>';
          }).join("");
          Array.prototype.forEach.call($("#cf-b").querySelectorAll("[data-copy]"),function(btn){
            btn.onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(btn.dataset.copy).then(function(){toast("Copied — v2rayNG: Import from clipboard","ok",4000)})}});
          Array.prototype.forEach.call($("#cf-b").querySelectorAll("[data-qr]"),function(btn){
            btn.onclick=function(){
              var ov=document.createElement("div");ov.className="qr-ov";
              ov.innerHTML='<div class="qr-c"><b style="font-size:13px">Scan with your client</b><div class="qrbox" style="margin:10px 0"><span class="sp1"></span></div><button class="btn sm" id="qrx">Close</button></div>';
              document.body.appendChild(ov);
              ov.onclick=function(e){if(e.target===ov)ov.remove()};
              $("#qrx",ov).onclick=function(){ov.remove()};
              api("POST","/api/instances/"+id+"/qr",{text:btn.dataset.qr}).then(function(svg){
                $(".qrbox",ov).innerHTML=svg}).catch(function(e){ov.remove();toast(e.message,"err")});
            }});
        }).catch(function(e){$("#cf-b").innerHTML='<span class="ftx">'+esc(e.message)+"</span>"});
      }
      $("#cf-r").onclick=loadCfg;loadCfg();
    }
    else if(tab==="overview"){
      b.innerHTML='<div class="kv" id="okv"></div><div class="card" style="margin-top:16px"><h3>Latest deployment</h3><div id="odp" class="mut">—</div></div>';
      Promise.all([api("GET","/api/instances/"+id+"/status"),api("GET","/api/instances/"+id+"/metrics")]).then(function(rs){
        var st=rs[0],mt=rs[1],ld=inst.latest_deployment;
        $("#okv").innerHTML=
          '<div class="it"><div class="k">Status</div><div class="v">'+(LBL[inst.status]||inst.status)+"</div></div>"+
          '<div class="it"><div class="k">Uptime</div><div class="v">'+fmtUp(st.core_health&&st.core_health.uptime)+"</div></div>"+
          '<div class="it"><div class="k">Connections</div><div class="v">'+(st.core_health?st.core_health.connections:"—")+"</div></div>"+
          '<div class="it"><div class="k">Version</div><div class="v">'+esc(st.core_health&&st.core_health.version||"—")+"</div></div>"+
          '<div class="it"><div class="k">Region</div><div class="v">'+esc(inst.region)+"</div></div>"+
          '<div class="it"><div class="k">Health</div><div class="v" style="color:'+(st.healthy?"var(--grn)":"var(--fnt)")+'">'+(st.healthy?"healthy":"n/a")+"</div></div>";
        $("#odp").innerHTML=ld?'<div class="row">'+stEl(ld.status).outerHTML+'<span class="chip">v'+ld.version+'</span><span class="ftx">started '+ago(ld.started_at)+" · "+dur(ld.duration_ms)+"</span></div>"+(ld.error?'<p style="color:var(--red);font-size:12px;margin:7px 0 0">'+esc(ld.error)+"</p>":""):"—";
      }).catch(function(){});
    }
    else if(tab==="logs"){
      b.innerHTML='<div class="term"><div class="tb"><button class="btn sm" id="lp">Pause</button><div class="sp"></div><button class="btn sm" id="lc">Copy</button><button class="btn sm" id="ld">Download</button></div><div class="tbody" id="lb"><div class="te">Waiting for logs…</div></div></div>';
      $("#lp").onclick=function(e){logPaused=!logPaused;e.target.textContent=logPaused?"Resume":"Pause";e.target.classList.toggle("pri",logPaused)};
      $("#lc").onclick=function(){navigator.clipboard&&navigator.clipboard.writeText(logBuf.map(function(l){return l.level+" "+l.message}).join("\n")).then(function(){toast("Copied","ok",1200)})};
      $("#ld").onclick=function(){var blob=new Blob([logBuf.map(function(l){return new Date(l.ts*1e3).toISOString()+" "+l.level+" "+l.message}).join("\n")],{type:"text/plain"});var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=inst.slug+"-logs.txt";a.click()};
      pollLogs();
    }
    else if(tab==="networking"){
      var list=(inst.domains||[]);
      b.innerHTML='<div class="card"><div class="row" style="justify-content:space-between"><h3>Endpoints</h3><button class="btn" id="rg">Regenerate</button></div><div id="dl2"></div></div>'+
        '<div class="card" style="margin-top:14px"><h3>Protocol paths</h3><table class="tbl"><tr><td>VLESS</td><td class="mono">/ws/&lt;uuid&gt; · /xhttp-siz10/…</td></tr><tr><td>Trojan</td><td class="mono">/trojan-ws · /txhttp-siz10/…</td></tr><tr><td>Shadowsocks</td><td class="mono">/ss-ws (AEAD)</td></tr></table><p class="ftx" style="font-size:12px;margin:9px 0 0">WebSocket upgrade, keep-alive and long-lived connections supported end-to-end. TLS at the edge.</p></div>';
      $("#dl2").innerHTML=list.length?list.map(function(d){var url=d.kind==="path"?(location.origin+"/i/"+d.domain):("https://"+d.domain);
        return '<div class="row" style="margin-top:9px"><span class="chip">'+d.kind+'</span><span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%">'+esc(url)+"</span></div>"}).join(""):'<p class="mut">No endpoints yet.</p>';
      $("#rg").onclick=function(){if(!confirm("Regenerate endpoints? Old links stop working."))return;
        api("POST","/api/instances/"+id+"/domains").then(function(){toast("Endpoints regenerated","ok");refresh()}).catch(function(e){toast(e.message,"err")})};
    }
    else if(tab==="deployments"){
      b.innerHTML='<div class="card" style="padding:0"><table class="tbl"><thead><tr><th>Version</th><th>Status</th><th>Started</th><th>Took</th><th>Error</th></tr></thead><tbody id="dt"></tbody></table></div>';
      api("GET","/api/instances/"+id+"/deployments").then(function(d){
        $("#dt").innerHTML=d.deployments.length?d.deployments.map(function(x){return "<tr><td class='mono'>v"+x.version+"</td><td>"+stEl(x.status).outerHTML+"</td><td class='ftx'>"+ago(x.started_at)+"</td><td>"+dur(x.duration_ms)+"</td><td class='ftx' style='max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>"+esc(x.error||"")+"</td></tr>"}).join(""):'<tr><td colspan="5" class="ftx" style="text-align:center;padding:20px">No deployments yet.</td></tr>'});
    }
    else if(tab==="activity"){
      b.innerHTML='<div class="card"><h3>Activity</h3><div id="af"></div></div>';
      api("GET","/api/instances/"+id+"/activity").then(function(d){
        $("#af").innerHTML=d.activity.length?d.activity.map(function(a){return '<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--bd)"><span class="ftx mono" style="width:64px;flex:none">'+ago(a.ts)+'</span><span class="mut">'+esc(a.message)+"</span></div>"}).join(""):'<span class="ftx">Nothing yet.</span>'});
    }
    else if(tab==="settings"){
      b.innerHTML='<div class="card" style="max-width:520px"><h3>Danger zone</h3><p class="mut" style="font-size:12.5px">Rotate credentials (endpoint token + internal token) and redeploy, or delete this instance.</p><div class="row" style="margin-top:14px"><button class="btn" id="s-rt">Rotate credentials</button><button class="btn dng" id="s-del">Delete instance</button></div></div>';
      $("#s-rt").onclick=function(){if(!confirm("Rotate credentials and redeploy? Clients must re-import the link."))return;
        api("POST","/api/instances/"+id+"/domains").then(function(){return api("POST","/api/instances/"+id+"/redeploy")}).then(function(){toast("Rotated — redeploying","ok");refresh()}).catch(function(e){toast(e.message,"err")})};
      $("#s-del").onclick=function(){if(confirm('Delete "'+inst.name+'"? This is permanent.')){api("DELETE","/api/instances/"+id).then(nav_("dash")).catch(function(e){toast(e.message,"err")})}};
    }
  }
  function pollLogs(){
    api("GET","/api/instances/"+id+"/logs?tail=200").then(function(d){
      if(!logPaused&&d.logs&&d.logs.length){logBuf=logBuf.concat(d.logs).slice(-600);var el=$("#lb");
        if(el){el.innerHTML=logBuf.map(function(l){var lv=(l.level||"info").toLowerCase();return '<div class="ll '+lv+'"><span class="t">'+new Date(l.ts*1e3).toLocaleTimeString()+"</span> <span class='lv'>"+lv.toUpperCase()+"</span> "+esc(l.message)+"</div>"}).join("");el.scrollTop=el.scrollHeight}}})
    .catch(function(){});
  }
  refresh().then(function(){
    pollTimer=every(5000,function(){
      if(tab==="logs")pollLogs();
      else if(BUSY[inst.status]||tab==="overview")refresh();
    });
  });
  setCleanup(function(){});
}
// ───────────────────────────── admin ─────────────────────────────
function viewAdmin(){
  shell("admin");
  var v=$("#view");
  v.innerHTML='<div class="ph"><div><h1>Admin</h1><div class="sub">Platform-wide state. Actions are audited.</div></div></div><div class="sgs" id="as"></div><div id="ab"></div>';
  var tab="instances";
  function stats(){api("GET","/api/admin/overview").then(function(s){
    $("#as").innerHTML='<div class="sg"><div class="l">Users</div><div class="v">'+s.users+'</div></div><div class="sg"><div class="l">Instances</div><div class="v">'+s.instances+'</div></div><div class="sg"><div class="l">Running</div><div class="v" style="color:var(--grn)">'+s.instances_running+'</div></div><div class="sg"><div class="l">Workers online</div><div class="v">'+s.workers_online+"</div></div>"})
  .catch(function(e){if(e.message.indexOf("admin")>=0)nav_("dash")})}
  function draw(){
    var b=$("#ab");
    if(tab==="instances"){
      api("GET","/api/admin/instances").then(function(d){
        b.innerHTML='<div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>Instance</th><th>Owner</th><th>Status</th><th>Actions</th></tr></thead><tbody>'+
        d.instances.map(function(i){return '<tr data-id="'+i.id+'"><td><b>'+esc(i.name)+'</b> <span class="ftx mono">'+esc(i.slug)+'</span></td><td>@'+esc(i.owner_login)+"</td><td>"+stEl(i.status).outerHTML+
        '</td><td><div class="row" style="gap:5px"><button class="btn sm" data-a="restart">Restart</button><button class="btn sm" data-a="stop">Stop</button><button class="btn sm dng" data-a="del">Delete</button></div></td></tr>'}).join("")+"</tbody></table></div>";
        Array.prototype.forEach.call(b.querySelectorAll("tr[data-id] .btn"),function(btn){btn.onclick=function(){
          var id=btn.closest("tr").dataset.id,a=btn.dataset.a;
          var p=a==="del"?(confirm("Delete this instance?")?api("DELETE","/api/admin/instances/"+id):Promise.resolve())
            :api("POST","/api/admin/instances/"+id+"/actions/"+a);
          Promise.resolve(p).then(function(){toast(a+" done","ok");draw()}).catch(function(e){toast(e.message,"err")})}});
      });
    }
    else if(tab==="users"){
      api("GET","/api/admin/users").then(function(d){
        b.innerHTML='<div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>User</th><th>Instances</th><th>Flags</th><th>Last login</th><th>Actions</th></tr></thead><tbody>'+
        d.users.map(function(u){return '<tr data-id="'+u.id+'"><td><b>'+esc(u.name||u.login)+"</b> <span class='ftx'>@"+esc(u.login)+"</span></td><td>"+u.instance_count+
        "</td><td>"+(u.is_admin?'<span class="chip">admin</span> ':"")+(u.is_disabled?'<span class="chip" style="color:var(--red)">disabled</span>':"")+"</td><td class='ftx'>"+ago(u.last_login_at)+
        '</td><td><div class="row" style="gap:5px"><button class="btn sm" data-a="adm">'+(u.is_admin?"Revoke admin":"Make admin")+'</button><button class="btn sm '+(u.is_disabled?"":"dng")+'" data-a="dis">'+(u.is_disabled?"Enable":"Disable")+"</button></div></td></tr>"}).join("")+"</tbody></table></div>";
        Array.prototype.forEach.call(b.querySelectorAll("tr[data-id] .btn"),function(btn){btn.onclick=function(){
          var id=btn.closest("tr").dataset.id,a=btn.dataset.a;
          var patch=a==="adm"?{is_admin:btn.textContent.indexOf("Make")===0}:{is_disabled:btn.textContent!=="Enable"};
          api("PATCH","/api/admin/users/"+id,patch).then(function(){toast("Updated","ok");draw()}).catch(function(e){toast(e.message,"err")})}});
      });
    }
    else if(tab==="workers"){
      api("GET","/api/admin/workers").then(function(d){
        b.innerHTML='<div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>Node</th><th>Region</th><th>Status</th><th>CPU</th><th>Memory</th><th>Capacity</th><th>Heartbeat</th></tr></thead><tbody>'+
        (d.workers.length?d.workers.map(function(w){return "<tr><td class='mono'>"+esc(w.node_id)+"</td><td>"+esc(w.region)+"</td><td>"+stEl(w.status).outerHTML+"</td><td>"+(w.cpu_percent!=null?w.cpu_percent.toFixed(0)+"%":"—")+"</td><td>"+(w.mem_used_mb!=null?w.mem_used_mb+" / "+w.mem_total_mb+" MB":"—")+"</td><td>"+(w.instances||0)+" / "+(w.capacity||"?")+"</td><td class='ftx'>"+ago(w.last_heartbeat)+"</td></tr>"}).join(""):'<tr><td colspan="7" class="ftx" style="text-align:center;padding:20px">No workers reported yet.</td></tr>')+"</tbody></table></div>"});
    }
    else if(tab==="system"){
      Promise.all([api("GET","/api/admin/system"),api("GET","/auth/me")]).then(function(rs){
        b.innerHTML='<div class="card" style="max-width:540px"><h3>Change your password</h3>'+
        '<div class="fld"><label>Current password</label><input class="inp" id="cp" type="password"></div>'+
        '<div class="fld"><label>New password (min 8 chars)</label><input class="inp" id="np" type="password"></div>'+
        '<button class="btn pri" id="cpb">Update password</button></div>'+
        '<div class="card" style="max-width:540px;margin-top:14px"><h3>System</h3><table class="tbl">'+
        '<tr><td style="color:var(--fnt);width:45%">Database</td><td>'+(rs[0].database.ok?"PostgreSQL/SQLite OK":"down")+"</td></tr>"+
        '<tr><td style="color:var(--fnt)">GitHub OAuth</td><td>'+(rs[0].github_oauth?"configured":"not configured (password login)")+"</td></tr>"+
        '<tr><td style="color:var(--fnt)">Railway provider</td><td>'+(rs[0].provider.railway?"configured":"not configured")+"</td></tr></table></div>";
        $("#cpb").onclick=function(){
          api("POST","/auth/change-password",{current_password:$("#cp").value,new_password:$("#np").value})
          .then(function(){toast("Password updated","ok");$("#cp").value="";$("#np").value=""})
          .catch(function(e){toast(e.message,"err")});
        };
      });
    }
  }
  function tabs(){
    v.innerHTML=v.innerHTML.replace(/<div id="ab"><\/div>[\s\S]*$/,'<div class="tabs" id="atb"></div><div id="ab"></div>');
  }
  // simpler: re-render header tabs each time
  function rebuild(extra){
    var old=$("#ab");var head=v.querySelector(".ph"),sgs=$("#as");
    v.innerHTML="";v.appendChild(head);v.appendChild(sgs);
    var tb=document.createElement("div");tb.className="tabs";tb.id="atb";
    ["instances","users","workers","system"].forEach(function(t){var btn=document.createElement("button");btn.className="tab "+(t===tab?"act":"");btn.textContent=t[0].toUpperCase()+t.slice(1);btn.onclick=function(){tab=t;rebuild();draw()};tb.appendChild(btn)});
    var ab=document.createElement("div");ab.id="ab";v.appendChild(tb);v.appendChild(ab);
    draw();
  }
  stats();rebuild();
}
// ───────────────────────────── boot ─────────────────────────────
function render(){
  api("GET","/auth/me").then(function(me){
    if(!me.authenticated){viewLogin();return}
    USER=me.user;CSRF=me.csrf_token;
    if(me.links){LINKS.github=me.links.github||LINKS.github;LINKS.telegram=me.links.telegram||""}
    viewDash();
  }).catch(function(e){
    $("#app").innerHTML='<div class="lw"><div class="lc"><div class="card"><b>Lunel Console failed to load</b><p class="mut">'+esc(e.message)+"</p></div></div></div>";
  });
}
render();
})();
</script>
</body>
</html>
"""

router.add_api_route("/panel", lambda: HTMLResponse(PAGE), methods=["GET"], include_in_schema=False)
