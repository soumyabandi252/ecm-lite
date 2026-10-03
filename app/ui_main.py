import json, os, shutil
from fastapi import HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from app.main import app, Session, Node, Version, Record, rec, audit, out, save_version, U, STORE

# drop the open legacy node routes and the placeholder home page; /v2 routes below enforce permissions
app.router.routes = [r for r in app.router.routes if not (getattr(r, 'path', '') in ('/', '/search') or getattr(r, 'path', '').startswith('/nodes'))]

RANK = {'viewer': 1, 'editor': 2, 'admin': 3}


def role(s, n, user):
    if n.owner_id == user.id:
        return 3
    cur = n
    while cur is not None:
        acl = s.query(Record).filter_by(type='acl', ref=cur.id).all()
        if acl:
            return max([RANK[a.data['role']] for a in acl if a.data['email'] == user.email] or [0])
        cur = s.get(Node, cur.parent_id) if cur.parent_id else None
    return 2


def need(s, n, user, lvl):
    if not n or n.deleted:
        raise HTTPException(404, 'Not found')
    if role(s, n, user) < lvl:
        raise HTTPException(403, 'You do not have permission for this')
    return n


def brief(s, n, user):
    return {'id': n.id, 'name': n.name, 'kind': n.kind, 'category': n.category, 'lifecycle': n.lifecycle,
            'versions': s.query(Version).filter_by(node_id=n.id).count(), 'role': role(s, n, user), 'hold': n.legal_hold}


def inside(s, dest, n):
    c = dest
    while c is not None:
        if c.id == n.id:
            return True
        c = s.get(Node, c.parent_id) if c.parent_id else None
    return False


def check_attrs(s, category, attrs):
    d = s.query(Record).filter_by(type='category', ref=category).first() if category else None
    if not d:
        return attrs
    clean = {}
    for f in d.data['fields']:
        v = attrs.get(f['name'])
        if v in (None, ''):
            if f.get('required'):
                raise HTTPException(422, 'Missing required field: ' + f['name'])
            continue
        if f['type'] == 'number':
            try:
                v = float(v)
            except Exception:
                raise HTTPException(422, f['name'] + ' must be a number')
        clean[f['name']] = v
    return clean


def clone(s, n, parent_id, user, suffix=''):
    c = Node(name=n.name + suffix, kind=n.kind, parent_id=parent_id, category=n.category,
             attributes=dict(n.attributes or {}), owner_id=user.id, text=n.text)
    s.add(c)
    s.commit()
    if n.kind == 'document':
        v = s.query(Version).filter_by(node_id=n.id).order_by(Version.number.desc()).first()
        if v:
            p = os.path.join(STORE, c.id + '_v1')
            shutil.copyfile(v.path, p)
            s.add(Version(node_id=c.id, number=1, path=p, filename=v.filename, mime=v.mime, size=v.size))
            s.commit()
    else:
        for k in s.query(Node).filter_by(parent_id=n.id, deleted=False).all():
            if role(s, k, user) >= 1:
                clone(s, k, c.id, user)
    return c


@app.get('/v2/nodes', tags=['v2'])
def v2_list(parent_id: str | None = None, user=U):
    with Session() as s:
        if parent_id:
            need(s, s.get(Node, parent_id), user, 1)
        rows = s.query(Node).filter_by(deleted=False, parent_id=parent_id).order_by(Node.kind.desc(), Node.name).all()
        return [brief(s, n, user) for n in rows if role(s, n, user) >= 1]


@app.post('/v2/folders', tags=['v2'])
def v2_folder(name: str, parent_id: str | None = None, user=U):
    with Session() as s:
        if parent_id:
            need(s, s.get(Node, parent_id), user, 2)
        n = Node(name=name, kind='folder', parent_id=parent_id, owner_id=user.id)
        s.add(n)
        s.commit()
        audit(s, user, 'create_folder', n.id)
        return brief(s, n, user)


@app.post('/v2/documents', tags=['v2'])
def v2_upload(name: str = Form(...), parent_id: str | None = Form(None), category: str | None = Form(None),
              attrs: str | None = Form(None), file: UploadFile = File(...), user=U):
    data = file.file.read()
    with Session() as s:
        if parent_id:
            need(s, s.get(Node, parent_id), user, 2)
        a = check_attrs(s, category, json.loads(attrs) if attrs else {})
        n = Node(name=name, kind='document', parent_id=parent_id or None, category=category or None,
                 attributes=a, owner_id=user.id)
        s.add(n)
        s.commit()
        v = save_version(s, n, file, data)
        audit(s, user, 'upload', n.id, version=v)
        return brief(s, n, user)


@app.post('/v2/nodes/{nid}/versions', tags=['v2'])
def v2_version(nid: str, file: UploadFile = File(...), user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 2)
        if n.kind != 'document':
            raise HTTPException(422, 'Only documents have versions')
        if n.legal_hold:
            raise HTTPException(423, 'Under legal hold')
        v = save_version(s, n, file, file.file.read())
        audit(s, user, 'new_version', n.id, version=v)
        return {'version': v}


@app.get('/v2/nodes/{nid}', tags=['v2'])
def v2_get(nid: str, user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 1)
        vs = s.query(Version).filter_by(node_id=nid).order_by(Version.number).all()
        d = brief(s, n, user)
        d.update(attributes=n.attributes or {}, legal_hold=n.legal_hold,
                 retention_until=n.retention_until.isoformat() if n.retention_until else None,
                 versions=[{'version': v.number, 'filename': v.filename, 'size': v.size} for v in vs],
                 comments=[out(c) for c in s.query(Record).filter_by(type='comment', ref=nid)],
                 signatures=[out(c) for c in s.query(Record).filter_by(type='esign', ref=nid)],
                 acl=[out(a) for a in s.query(Record).filter_by(type='acl', ref=nid)] if d['role'] >= 3 else [])
        return d


@app.get('/v2/nodes/{nid}/download', tags=['v2'])
def v2_download(nid: str, version: int | None = None, user=U):
    with Session() as s:
        need(s, s.get(Node, nid), user, 1)
        q = s.query(Version).filter_by(node_id=nid)
        v = q.filter_by(number=version).first() if version else q.order_by(Version.number.desc()).first()
        if not v:
            raise HTTPException(404, 'No such version')
        audit(s, user, 'download', nid, version=v.number)
        return FileResponse(v.path, filename=v.filename, media_type=v.mime)


@app.delete('/v2/nodes/{nid}', tags=['v2'])
def v2_delete(nid: str, user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 3)
        if n.legal_hold:
            raise HTTPException(423, 'Legal hold active')
        n.deleted = True
        s.commit()
        audit(s, user, 'delete', nid)
        return {'status': 'deleted'}


@app.post('/v2/nodes/{nid}/rename', tags=['v2'])
def v2_rename(nid: str, name: str, user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 2)
        n.name = name
        s.commit()
        audit(s, user, 'rename', nid, name=name)
        return brief(s, n, user)


@app.post('/v2/nodes/{nid}/move', tags=['v2'])
def v2_move(nid: str, parent_id: str | None = None, user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 2)
        if parent_id:
            dest = need(s, s.get(Node, parent_id), user, 2)
            if dest.kind != 'folder':
                raise HTTPException(422, 'Destination must be a folder')
            if inside(s, dest, n):
                raise HTTPException(422, 'Cannot move a folder into itself')
        n.parent_id = parent_id or None
        s.commit()
        audit(s, user, 'move', nid, to=parent_id)
        return brief(s, n, user)


@app.post('/v2/nodes/{nid}/copy', tags=['v2'])
def v2_copy(nid: str, parent_id: str | None = None, user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 1)
        if parent_id:
            dest = need(s, s.get(Node, parent_id), user, 2)
            if dest.kind != 'folder':
                raise HTTPException(422, 'Destination must be a folder')
            if inside(s, dest, n):
                raise HTTPException(422, 'Cannot copy a folder into itself')
        c = clone(s, n, parent_id or None, user, ' (copy)')
        audit(s, user, 'copy', nid, new_id=c.id)
        return brief(s, c, user)


@app.post('/v2/nodes/{nid}/attributes', tags=['v2'])
def v2_attrs(nid: str, attrs: dict, user=U):
    with Session() as s:
        n = need(s, s.get(Node, nid), user, 2)
        n.attributes = check_attrs(s, n.category, attrs)
        s.commit()
        audit(s, user, 'update_attributes', nid)
        return n.attributes


@app.post('/v2/nodes/{nid}/comments', tags=['v2'])
def v2_comment(nid: str, body: str, user=U):
    with Session() as s:
        need(s, s.get(Node, nid), user, 1)
        return out(rec(s, 'comment', nid, author=user.email, body=body))


@app.put('/v2/nodes/{nid}/acl', tags=['v2'])
def v2_acl_set(nid: str, email: str, role_name: str, user=U):
    if role_name not in RANK:
        raise HTTPException(422, 'Role must be viewer, editor or admin')
    with Session() as s:
        need(s, s.get(Node, nid), user, 3)
        for a in s.query(Record).filter_by(type='acl', ref=nid, ).all():
            if a.data['email'] == email:
                s.delete(a)
        s.commit()
        audit(s, user, 'acl_grant', nid, email=email, role=role_name)
        return out(rec(s, 'acl', nid, email=email, role=role_name))


@app.delete('/v2/nodes/{nid}/acl', tags=['v2'])
def v2_acl_del(nid: str, email: str, user=U):
    with Session() as s:
        need(s, s.get(Node, nid), user, 3)
        for a in s.query(Record).filter_by(type='acl', ref=nid).all():
            if a.data['email'] == email:
                s.delete(a)
        s.commit()
        audit(s, user, 'acl_remove', nid, email=email)
        return {'status': 'removed'}


@app.post('/v2/categories', tags=['v2'])
def v2_cat(name: str, fields: str, user=U):
    fs = []
    for part in fields.split(','):
        bits = [b.strip() for b in part.split(':')]
        if not bits[0]:
            continue
        t = bits[1] if len(bits) > 1 and bits[1] in ('text', 'number', 'date') else 'text'
        fs.append({'name': bits[0], 'type': t, 'required': len(bits) > 2 and bits[2] == 'required'})
    with Session() as s:
        old = s.query(Record).filter_by(type='category', ref=name).first()
        if old:
            old.data = {'fields': fs}
            s.commit()
            return out(old)
        return out(rec(s, 'category', name, fields=fs))


@app.get('/v2/categories', tags=['v2'])
def v2_cats(user=U):
    with Session() as s:
        return [{'name': r.ref, 'fields': r.data['fields']} for r in s.query(Record).filter_by(type='category')]


@app.get('/v2/search', tags=['v2'])
def v2_search(q: str, user=U):
    with Session() as s:
        like = '%' + q + '%'
        rows = s.query(Node).filter(Node.deleted == False, (Node.name.ilike(like)) | (Node.text.ilike(like))
                                     | (Node.category.ilike(like))).limit(100).all()
        return [brief(s, n, user) for n in rows if role(s, n, user) >= 1]


PAGE = r'''<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ECM-Lite</title>
<style>body{font-family:sans-serif;margin:0;background:#f4f6f8}header{background:#1f3a5f;color:#fff;padding:10px 20px;display:flex;gap:18px;align-items:center}header a{color:#fff;cursor:pointer}main{max-width:1000px;margin:20px auto;background:#fff;padding:20px;border-radius:8px}table{width:100%;border-collapse:collapse;margin-top:10px}td,th{padding:6px;border-bottom:1px solid #eee;text-align:left}input,select,button{padding:6px;margin:2px}button{cursor:pointer}.link{color:#1f3a5f;cursor:pointer;text-decoration:underline}.err{color:#b00}.bar{background:#f0f3f7;padding:8px;border-radius:6px;margin:8px 0}h4{margin:14px 0 4px}</style></head><body>
<header><b>ECM-Lite</b><span id='nav' hidden><a data-t='repo'>Repository</a> <a data-t='cases'>Cases</a> <a data-t='quality'>Quality</a> <a data-t='types'>Types</a> <a data-t='audit'>Audit</a> <a id='lo'>Logout</a></span><a href='/docs' style='margin-left:auto'>API docs</a></header>
<main><div id='msg'></div>
<section id='login'><h2>Sign in</h2><input id='em' placeholder='email'> <input id='pw' type='password' placeholder='password'> <button id='bl'>Login</button> <button id='br'>Register + login</button></section>
<section id='repo' hidden><div id='crumbs'></div>
<div class='bar'><input id='fn' placeholder='New folder name'><button id='mk'>Create folder</button> <span id='clip'></span></div>
<div class='bar'><b>Upload:</b> <input id='fl' type='file'> <select id='cat'></select> <span id='attrs'></span> <button id='up'>Upload</button></div>
<div class='bar'><input id='sq' placeholder='Search name or text'><button id='sb'>Search</button> <button id='sx'>Clear</button></div>
<table><thead><tr><th>Name</th><th>Type</th><th>State</th><th>Actions</th></tr></thead><tbody id='rows'></tbody></table><div id='detail'></div></section>
<section id='cases' hidden><div class='bar'><input id='ct' placeholder='Case title'><button id='cc'>Create case</button></div><div id='cl'></div><div id='cd'></div></section>
<section id='quality' hidden><div class='bar'><select id='et'><option>capa</option><option>deviation</option><option>change_control</option><option>audit</option><option>complaint</option></select><input id='etl' placeholder='Event title'><button id='ce'>Create event</button></div><div id='ql'></div></section>
<section id='types' hidden><div class='bar'><input id='cn' placeholder='Type name e.g. Contract'><input id='cf' size='45' placeholder='Fields e.g. Contract date:date:required, Amount:number, Dept:text'><button id='cb'>Save type</button></div><div id='tl'></div></section>
<section id='audit' hidden><div id='al'></div></section></main>
<script>
const SEC=['repo','cases','quality','types','audit'];
let T=localStorage.t||'',cur='repo',path=[{id:null,name:'Root'}],M={},CATS=[],clip=null,ACLS=[];
const $=id=>document.getElementById(id),esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
const q=o=>new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([k,v])=>v!==null&&v!==undefined&&v!==''))).toString();
const pid=()=>path[path.length-1].id;
function msg(t,bad){$('msg').className=bad?'err':'';$('msg').textContent=t||''}
async function api(url,o={}){o.headers=Object.assign({Authorization:'Bearer '+T},o.headers||{});const r=await fetch(url,o);if(r.status==401){out();throw 'Please log in again'}const d=await r.json().catch(()=>({}));if(!r.ok)throw (typeof d.detail=='string'?d.detail:JSON.stringify(d.detail||r.statusText));return d}
function out(){T='';localStorage.t='';render()}
async function auth(reg){try{const e=$('em').value,p=$('pw').value;if(reg){const r=await fetch('/auth/register?'+q({email:e,password:p}),{method:'POST'});if(!r.ok&&r.status!=400)throw 'Register failed'}const r=await fetch('/auth/token',{method:'POST',body:new URLSearchParams({username:e,password:p})});const d=await r.json();if(!r.ok)throw d.detail;T=d.access_token;localStorage.t=T;msg('');render()}catch(x){msg(x,1)}}
function render(){const a=!!T;$('login').hidden=a;$('nav').hidden=!a;SEC.forEach(x=>$(x).hidden=true);if(a)show(cur)}
function show(t){cur=t;SEC.forEach(x=>$(x).hidden=x!=t);msg('');({repo:loadRepo,cases:loadCases,quality:loadQ,types:loadTypes,audit:loadAudit})[t]()}
async function loadCats(){CATS=await api('/v2/categories');$('cat').innerHTML='<option value>(no type)</option>'+CATS.map(c=>'<option>'+esc(c.name)+'</option>').join('');$('attrs').innerHTML=''}
function attrInputs(cname,box,vals){box.innerHTML='';const c=CATS.find(x=>x.name==cname);if(!c)return;c.fields.forEach(f=>{const i=document.createElement('input');i.placeholder=f.name+(f.required?' *':'');i.type=f.type=='date'?'date':f.type=='number'?'number':'text';i.dataset.name=f.name;i.value=(vals&&vals[f.name])||'';box.appendChild(i)})}
function readAttrs(box){const o={};box.querySelectorAll('input').forEach(i=>{if(i.value!=='')o[i.dataset.name]=i.value});return o}
function showClip(){$('clip').innerHTML=clip?'Clipboard: '+esc(clip.name)+' ('+clip.mode+') <button data-paste=1>Paste here</button> <a class=link id=cx>clear</a>':''}
function rows(L){M={};L.forEach(n=>M[n.id]=n);return L.map(n=>{const w=n.role>=2;return '<tr><td>'+(n.kind=='folder'?'&#128193; <span class=link data-open='+n.id+'>'+esc(n.name)+'</span>':'&#128196; <span class=link data-det='+n.id+'>'+esc(n.name)+'</span>')+(n.hold?' &#128274;':'')+'</td><td>'+esc(n.category||n.kind)+'</td><td>'+esc(n.lifecycle)+'</td><td><a class=link data-det='+n.id+'>details</a> '+(n.kind=='document'?'<a class=link data-dl='+n.id+'>download</a> ':'')+(w?'<a class=link data-ren='+n.id+'>rename</a> <a class=link data-cut='+n.id+'>cut</a> ':'')+'<a class=link data-cp='+n.id+'>copy</a>'+(n.role>=3?' <a class=link data-del='+n.id+'>delete</a>':'')+'</td></tr>'}).join('')||'<tr><td colspan=4>Empty</td></tr>'}
async function loadRepo(){try{await loadCats();const L=await api('/v2/nodes?'+q({parent_id:pid()}));$('crumbs').innerHTML=path.map((p,i)=>'<span class=link data-i='+i+'>'+esc(p.name)+'</span>').join(' / ');$('rows').innerHTML=rows(L);showClip()}catch(x){msg(x,1)}}
async function loadDet(id){try{const d=await api('/v2/nodes/'+id),doc=d.kind=='document',can=d.role>=2;ACLS=d.acl;
let h='<hr><h3>'+(doc?'&#128196; ':'&#128193; ')+esc(d.name)+' <small>('+esc(d.lifecycle)+(d.legal_hold?', LEGAL HOLD':'')+')</small></h3><div><b>Type:</b> '+esc(d.category||'-')+'</div><div id=dattr></div>';
if(can&&d.category)h+='<button data-sattr='+id+'>Save attributes</button>';
if(doc){h+='<h4>Versions</h4><ul>'+d.versions.map(v=>'<li>v'+v.version+' '+esc(v.filename)+' ('+v.size+' bytes) <a class=link data-dl='+id+' data-v='+v.version+'>download</a></li>').join('')+'</ul>';
if(can)h+='<input type=file id=nv><button data-nver='+id+'>Upload new version</button>';
h+='<h4>Lifecycle</h4><select id=lc>'+['draft','in_review','effective','superseded','obsolete'].map(s=>'<option'+(s==d.lifecycle?' selected':'')+'>'+s+'</option>').join('')+'</select><button data-life='+id+'>Set state</button>';
h+='<h4>E-signatures</h4><ul>'+d.signatures.map(s=>'<li>'+esc(s.meaning)+' by '+esc(s.signer)+' (v'+esc(s.version)+', '+esc(s.created_at)+')</li>').join('')+'</ul><select id=sm><option>Approved</option><option>Reviewed</option><option>Authored</option></select><button data-sign='+id+'>Sign</button>'}
h+='<h4>Comments</h4><ul>'+d.comments.map(c=>'<li>'+esc(c.body)+' <small>('+esc(c.author)+')</small></li>').join('')+'</ul><input id=cm placeholder=Comment><button data-cmt='+id+'>Add</button>';
if(d.role>=3)h+='<h4>Permissions</h4><small>No entries means everyone can edit. Once you grant someone access, only listed people (and you) can open this item.</small><ul>'+d.acl.map((a,i)=>'<li>'+esc(a.email)+' - '+esc(a.role)+' <a class=link data-racl='+id+' data-ai='+i+'>remove</a></li>').join('')+'</ul><input id=ae placeholder=Email><select id=ar><option>viewer</option><option>editor</option><option>admin</option></select><button data-aacl='+id+'>Grant</button>';
$('detail').innerHTML=h;attrInputs(d.category,$('dattr'),d.attributes)}catch(x){msg(x,1)}}
async function dl(id,v){try{const d=await api('/v2/nodes/'+id),ver=v?d.versions.find(x=>x.version==v):d.versions[d.versions.length-1];if(!ver)throw 'No file';const r=await fetch('/v2/nodes/'+id+'/download?'+q({version:ver.version}),{headers:{Authorization:'Bearer '+T}});if(!r.ok)throw 'Download failed';const b=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=ver.filename||'file';a.click()}catch(x){msg(x,1)}}
async function loadCases(){try{const L=await api('/cases');$('cd').innerHTML='';$('cl').innerHTML='<table><tr><th>Title</th><th>Status</th><th>Priority</th></tr>'+L.map(c=>'<tr><td><span class=link data-case='+c.id+'>'+esc(c.title)+'</span></td><td>'+esc(c.status)+'</td><td>'+esc(c.priority)+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
async function loadCase(id){try{const c=await api('/cases/'+id);$('cd').innerHTML='<h3>'+esc(c.title)+'</h3><div class=bar><input id=nt placeholder=Note><button data-note='+id+'>Add note</button></div><b>Notes</b><ul>'+c.notes.map(n=>'<li>'+esc(n.note)+' <small>('+esc(n.author)+')</small></li>').join('')+'</ul>'}catch(x){msg(x,1)}}
async function loadQ(){try{const L=await api('/quality/events');$('ql').innerHTML='<table><tr><th>Type</th><th>Title</th><th>Status</th><th></th></tr>'+L.map(e=>'<tr><td>'+esc(e.event_type)+'</td><td>'+esc(e.title)+'</td><td>'+esc(e.status)+'</td><td>'+(e.status=='open'?'<a class=link data-close='+e.id+'>close</a>':'')+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
async function loadTypes(){try{const L=await api('/v2/categories');$('tl').innerHTML='<table><tr><th>Type</th><th>Fields</th></tr>'+L.map(c=>'<tr><td>'+esc(c.name)+'</td><td>'+c.fields.map(f=>esc(f.name)+' ('+f.type+(f.required?', required':'')+')').join('; ')+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
async function loadAudit(){try{const L=await api('/audit');$('al').innerHTML='<table><tr><th>When</th><th>Who</th><th>Action</th></tr>'+L.map(a=>'<tr><td>'+esc(a.created_at)+'</td><td>'+esc(a.actor)+'</td><td>'+esc(a.action)+'</td></tr>').join('')+'</table>'}catch(x){msg(x,1)}}
document.addEventListener('change',e=>{if(e.target.id=='cat')attrInputs(e.target.value,$('attrs'))});
document.addEventListener('click',async e=>{const d=e.target.dataset,id=e.target.id;try{
if(d.t)show(d.t);else if(d.i!==undefined){path=path.slice(0,+d.i+1);$('detail').innerHTML='';loadRepo()}
else if(d.open){path.push({id:d.open,name:M[d.open].name});$('detail').innerHTML='';loadRepo()}
else if(d.det)loadDet(d.det);
else if(d.ren){const n=prompt('New name',M[d.ren].name);if(n){await api('/v2/nodes/'+d.ren+'/rename?'+q({name:n}),{method:'POST'});loadRepo()}}
else if(d.cut||d.cp){const i=d.cut||d.cp;clip={id:i,mode:d.cut?'move':'copy',name:M[i].name};showClip()}
else if(d.paste){await api('/v2/nodes/'+clip.id+'/'+clip.mode+'?'+q({parent_id:pid()}),{method:'POST'});clip=null;msg('Done');loadRepo()}
else if(d.del){if(confirm('Delete this item?')){await api('/v2/nodes/'+d.del,{method:'DELETE'});$('detail').innerHTML='';loadRepo()}}
else if(d.dl)dl(d.dl,d.v);
else if(d.sattr){await api('/v2/nodes/'+d.sattr+'/attributes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readAttrs($('dattr')))});msg('Saved');loadDet(d.sattr)}
else if(d.nver){const f=$('nv').files[0];if(!f)throw 'Choose a file first';const fd=new FormData();fd.append('file',f);await api('/v2/nodes/'+d.nver+'/versions',{method:'POST',body:fd});msg('New version uploaded');loadDet(d.nver);loadRepo()}
else if(d.life){await api('/quality/nodes/'+d.life+'/lifecycle?'+q({state:$('lc').value}),{method:'PATCH'});msg('State updated');loadDet(d.life);loadRepo()}
else if(d.sign){const pw=prompt('Re-enter your password to sign');if(pw){await api('/quality/sign?'+q({node_id:d.sign,meaning:$('sm').value,password:pw}),{method:'POST'});msg('Signed');loadDet(d.sign)}}
else if(d.cmt){await api('/v2/nodes/'+d.cmt+'/comments?'+q({body:$('cm').value}),{method:'POST'});loadDet(d.cmt)}
else if(d.aacl){await api('/v2/nodes/'+d.aacl+'/acl?'+q({email:$('ae').value,role_name:$('ar').value}),{method:'PUT'});loadDet(d.aacl)}
else if(d.racl){await api('/v2/nodes/'+d.racl+'/acl?'+q({email:ACLS[+d.ai].email}),{method:'DELETE'});loadDet(d.racl)}
else if(d.case)loadCase(d.case);
else if(d.note){await api('/cases/'+d.note+'/notes?'+q({note:$('nt').value}),{method:'POST'});loadCase(d.note)}
else if(d.close){await api('/quality/events/'+d.close+'/close',{method:'PATCH'});loadQ()}
else if(id=='cx'){clip=null;showClip()}
else if(id=='bl')auth(0);else if(id=='br')auth(1);else if(id=='lo')out();
else if(id=='mk'){await api('/v2/folders?'+q({name:$('fn').value,parent_id:pid()}),{method:'POST'});$('fn').value='';msg('Folder created');loadRepo()}
else if(id=='up'){const f=$('fl').files[0];if(!f)throw 'Choose a file first';const fd=new FormData();fd.append('name',f.name);if(pid())fd.append('parent_id',pid());if($('cat').value){fd.append('category',$('cat').value);fd.append('attrs',JSON.stringify(readAttrs($('attrs'))))}fd.append('file',f);await api('/v2/documents',{method:'POST',body:fd});$('fl').value='';msg('Uploaded');loadRepo()}
else if(id=='sb'){$('rows').innerHTML=rows(await api('/v2/search?'+q({q:$('sq').value})))}
else if(id=='sx'){$('sq').value='';loadRepo()}
else if(id=='cc'){await api('/cases?'+q({title:$('ct').value}),{method:'POST'});$('ct').value='';loadCases()}
else if(id=='ce'){await api('/quality/events?'+q({event_type:$('et').value,title:$('etl').value}),{method:'POST'});$('etl').value='';loadQ()}
else if(id=='cb'){await api('/v2/categories?'+q({name:$('cn').value,fields:$('cf').value}),{method:'POST'});msg('Type saved');loadTypes()}
}catch(x){msg(x,1)}});
render();
</script></body></html>
'''


@app.get('/', response_class=HTMLResponse, include_in_schema=False)
def ui():
    return PAGE
