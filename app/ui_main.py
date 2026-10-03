from fastapi.responses import HTMLResponse
from app.main import app

# replace the placeholder home page with the real UI
app.router.routes = [r for r in app.router.routes if getattr(r, 'path', None) != '/']

PAGE = r'''<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ECM-Lite</title>
<style>body{font-family:sans-serif;margin:0;background:#f4f6f8}header{background:#1f3a5f;color:#fff;padding:10px 20px;display:flex;gap:18px;align-items:center}header a{color:#fff;cursor:pointer}main{max-width:960px;margin:20px auto;background:#fff;padding:20px;border-radius:8px}table{width:100%;border-collapse:collapse;margin-top:10px}td,th{padding:6px;border-bottom:1px solid #eee;text-align:left}input,select,button{padding:6px;margin:2px}button{cursor:pointer}.link{color:#1f3a5f;cursor:pointer;text-decoration:underline}.err{color:#b00}.bar{background:#f0f3f7;padding:8px;border-radius:6px;margin:8px 0}</style></head><body>
<header><b>ECM-Lite</b><span id='nav' hidden><a data-t='repo'>Repository</a> <a data-t='cases'>Cases</a> <a data-t='quality'>Quality</a> <a data-t='audit'>Audit</a> <a id='lo'>Logout</a></span><a href='/docs' style='margin-left:auto'>API docs</a></header>
<main><div id='msg'></div>
<section id='login'><h2>Sign in</h2><input id='em' placeholder='email'> <input id='pw' type='password' placeholder='password'> <button id='bl'>Login</button> <button id='br'>Register + login</button></section>
<section id='repo' hidden><div id='crumbs'></div>
<div class='bar'><input id='fn' placeholder='New folder name'><button id='mk'>Create folder</button> | <input id='fl' type='file'><input id='cat' placeholder='category (optional)'><button id='up'>Upload</button></div>
<div class='bar'><input id='sq' placeholder='Search name or text'><button id='sb'>Search</button></div>
<table><thead><tr><th>Name</th><th>Category</th><th>Lifecycle</th><th>Actions</th></tr></thead><tbody id='rows'></tbody></table></section>
<section id='cases' hidden><div class='bar'><input id='ct' placeholder='Case title'><button id='cc'>Create case</button></div><div id='cl'></div><div id='cd'></div></section>
<section id='quality' hidden><div class='bar'><select id='et'><option>capa</option><option>deviation</option><option>change_control</option><option>audit</option><option>complaint</option></select><input id='etl' placeholder='Event title'><button id='ce'>Create event</button></div><div id='ql'></div></section>
<section id='audit' hidden><div id='al'></div></section></main>
<script>
let T=localStorage.t||'',cur='repo',path=[{id:null,name:'Root'}],M={};
const $=id=>document.getElementById(id),esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
const q=o=>new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([k,v])=>v!==null&&v!==undefined&&v!==''))).toString();
const pid=()=>path[path.length-1].id;
function msg(t,bad){$('msg').className=bad?'err':'';$('msg').textContent=t||''}
async function api(url,o={}){o.headers=Object.assign({Authorization:'Bearer '+T},o.headers||{});const r=await fetch(url,o);if(r.status==401){out();throw 'Please log in again'}const d=await r.json().catch(()=>({}));if(!r.ok)throw (typeof d.detail=='string'?d.detail:JSON.stringify(d.detail||r.statusText));return d}
function out(){T='';localStorage.t='';render()}
async function auth(reg){try{const e=$('em').value,p=$('pw').value;if(reg){const r=await fetch('/auth/register?'+q({email:e,password:p}),{method:'POST'});if(!r.ok&&r.status!=400)throw 'Register failed'}const r=await fetch('/auth/token',{method:'POST',body:new URLSearchParams({username:e,password:p})});const d=await r.json();if(!r.ok)throw d.detail;T=d.access_token;localStorage.t=T;msg('');render()}catch(x){msg(x,1)}}
function render(){const a=!!T;$('login').hidden=a;$('nav').hidden=!a;['repo','cases','quality','audit'].forEach(x=>$(x).hidden=true);if(a)show(cur)}
function show(t){cur=t;['repo','cases','quality','audit'].forEach(x=>$(x).hidden=x!=t);msg('');({repo:loadRepo,cases:loadCases,quality:loadQ,audit:loadAudit})[t]()}
function rows(L){M={};L.forEach(n=>M[n.id]=n);return L.map(n=>'<tr><td>'+(n.kind=='folder'?'&#128193; <span class=link data-open='+n.id+'>'+esc(n.name)+'</span>':'&#128196; '+esc(n.name))+'</td><td>'+esc(n.category)+'</td><td>'+esc(n.lifecycle)+'</td><td>'+(n.kind=='document'?'<a class=link data-dl='+n.id+'>download</a> ':'')+'<a class=link data-del='+n.id+'>delete</a></td></tr>').join('')||'<tr><td colspan=4>Empty</td></tr>'}
async function loadRepo(){try{const L=await api('/nodes?'+q({parent_id:pid()}));$('crumbs').innerHTML=path.map((p,i)=>'<span class=link data-i='+i+'>'+esc(p.name)+'</span>').join(' / ');$('rows').innerHTML=rows(L)}catch(x){msg(x,1)}}
async function loadCases(){try{const L=await api('/cases');$('cd').innerHTML='';$('cl').innerHTML='<table><tr><th>Title</th><th>Status</th><th>Priority</th></tr>'+L.map(c=>'<tr><td><span class=link data-case='+c.id+'>'+esc(c.title)+'</span></td><td>'+esc(c.status)+'</td><td>'+esc(c.priority)+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
async function loadCase(id){try{const c=await api('/cases/'+id);$('cd').innerHTML='<h3>'+esc(c.title)+'</h3><div class=bar><input id=nt placeholder="Add note"><button data-note='+id+'>Add note</button></div><b>Notes</b><ul>'+c.notes.map(n=>'<li>'+esc(n.note)+' <small>('+esc(n.author)+')</small></li>').join('')+'</ul>'}catch(x){msg(x,1)}}
async function loadQ(){try{const L=await api('/quality/events');$('ql').innerHTML='<table><tr><th>Type</th><th>Title</th><th>Status</th><th></th></tr>'+L.map(e=>'<tr><td>'+esc(e.event_type)+'</td><td>'+esc(e.title)+'</td><td>'+esc(e.status)+'</td><td>'+(e.status=='open'?'<a class=link data-close='+e.id+'>close</a>':'')+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
async function loadAudit(){try{const L=await api('/audit');$('al').innerHTML='<table><tr><th>When</th><th>Who</th><th>Action</th></tr>'+L.map(a=>'<tr><td>'+esc(a.created_at)+'</td><td>'+esc(a.actor)+'</td><td>'+esc(a.action)+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
async function dl(id){try{const r=await fetch('/nodes/'+id+'/download',{headers:{Authorization:'Bearer '+T}});if(!r.ok)throw 'Download failed';const b=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=(M[id]||{}).name||'file';a.click()}catch(x){msg(x,1)}}
document.addEventListener('click',async e=>{const d=e.target.dataset,id=e.target.id;try{
if(d.t)show(d.t);else if(d.i!==undefined){path=path.slice(0,+d.i+1);loadRepo()}
else if(d.open){path.push({id:d.open,name:M[d.open].name});loadRepo()}
else if(d.del){if(confirm('Delete this item?')){await api('/nodes/'+d.del,{method:'DELETE'});loadRepo()}}
else if(d.dl)dl(d.dl);else if(d.case)loadCase(d.case);
else if(d.note){await api('/cases/'+d.note+'/notes?'+q({note:$('nt').value}),{method:'POST'});loadCase(d.note)}
else if(d.close){await api('/quality/events/'+d.close+'/close',{method:'PATCH'});loadQ()}
else if(id=='bl')auth(0);else if(id=='br')auth(1);else if(id=='lo')out();
else if(id=='mk'){await api('/nodes/folders?'+q({name:$('fn').value,parent_id:pid()}),{method:'POST'});$('fn').value='';msg('Folder created');loadRepo()}
else if(id=='up'){const f=$('fl').files[0];if(!f)throw 'Choose a file first';const fd=new FormData();fd.append('name',f.name);if(pid())fd.append('parent_id',pid());if($('cat').value)fd.append('category',$('cat').value);fd.append('file',f);await api('/nodes/documents',{method:'POST',body:fd});$('fl').value='';msg('Uploaded');loadRepo()}
else if(id=='sb'){$('rows').innerHTML=rows(await api('/search?'+q({q:$('sq').value})))}
else if(id=='cc'){await api('/cases?'+q({title:$('ct').value}),{method:'POST'});$('ct').value='';loadCases()}
else if(id=='ce'){await api('/quality/events?'+q({event_type:$('et').value,title:$('etl').value}),{method:'POST'});$('etl').value='';loadQ()}
}catch(x){msg(x,1)}});
render();
</script></body></html>
'''


@app.get('/', response_class=HTMLResponse, include_in_schema=False)
def ui():
    return PAGE
