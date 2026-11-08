"""A local-only page (HUB_ENV=dev) to browse profiles, call tools as a user and decide held calls."""

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>connector-hub (dev)</title>
<style>
body{font:14px/1.45 system-ui,sans-serif;margin:24px;max-width:1100px;color:#1d1d1f}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}code,pre{font:12px ui-monospace,monospace}
pre{background:#f5f5f7;padding:10px;border-radius:6px;overflow:auto;max-height:320px}
input,select,textarea{font:13px system-ui;padding:5px;border:1px solid #ccc;border-radius:5px}
textarea{width:100%;height:90px}button{padding:6px 12px;border-radius:6px;border:1px solid #888;background:#fff;cursor:pointer}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:6px 0}.muted{color:#666}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left;vertical-align:top}
.w{color:#b3261e;font-weight:600}
</style></head><body>
<h1>connector-hub <span class="muted">dev mode: fakes and unauthenticated users</span></h1>
<div class="row">User <input id="uid" value="cust-001"> Roles <input id="roles" placeholder="e.g. claims_supervisor">
Tenant <input id="tenant" value="default"> <button onclick="load()">Reload</button>
<button onclick="post('/v1/dev/reset').then(load)">Reset fakes</button></div>
<h2>Tools</h2><div class="row">Profile <select id="profile" onchange="tools()"></select></div>
<table id="tools"></table>
<h2>Call</h2><div class="row"><select id="tool"></select></div>
<textarea id="args">{}</textarea><div class="row"><button onclick="call()">Call</button></div><pre id="out"></pre>
<h2>Held calls you can decide</h2><table id="apr"></table>
<script>
const H=()=>({'content-type':'application/json','x-user-id':uid.value,'x-user-roles':roles.value,'x-tenant':tenant.value});
const get=u=>fetch(u,{headers:H()}).then(r=>r.json());
const post=(u,b)=>fetch(u,{method:'POST',headers:H(),body:JSON.stringify(b||{})}).then(r=>r.json());
let T=[];
async function load(){const ps=await get('/v1/profiles');profile.innerHTML=ps.map(p=>`<option>${p.name}</option>`).join('');await tools();await apr();}
async function tools(){T=await get(`/v1/profiles/${profile.value}/tools`);
 document.getElementById('tools').innerHTML='<tr><th>Tool</th><th>System</th><th>Description</th></tr>'+T.map(t=>`<tr><td><code>${t.name}</code>${t.writes?' <span class=w>writes</span>':''}</td><td>${t.system}</td><td>${t.description.split('\\n')[0]}</td></tr>`).join('');
 tool.innerHTML=T.map(t=>`<option>${t.name}</option>`).join('');}
async function call(){let a;try{a=JSON.parse(args.value||'{}')}catch(e){out.textContent='arguments are not JSON';return}
 out.textContent=JSON.stringify(await post(`/v1/call/${profile.value}/${tool.value}`,{arguments:a}),null,2);apr();}
async function apr(){const rows=await get('/v1/approvals');
 document.getElementById('apr').innerHTML='<tr><th>Tool</th><th>For</th><th>Why</th><th></th></tr>'+(Array.isArray(rows)?rows:[]).map(r=>`<tr><td><code>${r.tool}</code><pre>${JSON.stringify(r.args,null,1)}</pre></td><td>${r.user.id}</td><td>${r.message}</td><td><button onclick="dec('${r.id}',true)">Approve</button> <button onclick="dec('${r.id}',false)">Reject</button></td></tr>`).join('');}
async function dec(id,ok){out.textContent=JSON.stringify(await post('/v1/approvals/'+id,{approve:ok,reason:ok?'approved in dev page':'rejected in dev page'}),null,2);apr();}
load();
</script></body></html>"""
