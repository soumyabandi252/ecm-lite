import os, hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from jsonschema import validate, ValidationError
from app.db import Session, User, Node, Version, Record
from app.security import hash_pw, check_pw, make_token, current_user

app = FastAPI(title="ECM-Lite", version="0.3.0",
    description="Content Server-style ECM with case management (Newgen-style), QMS/e-signatures (Veeva-style), e-forms and retention (OnBase-style).")
STORE = os.getenv("STORE_DIR", "./store")
os.makedirs(STORE, exist_ok=True)
U = Depends(current_user)

def rec(s, type_, ref=None, **data):
    r = Record(type=type_, ref=ref, data=data)
    s.add(r); s.commit(); s.refresh(r)
    return r

def audit(s, user, action, ref=None, **d):
    rec(s, "audit", ref, actor=user.email, action=action, **d)

def out(r):
    return {"id": r.id, "ref": r.ref, "created_at": r.created_at.isoformat(), **r.data}

@app.get("/", response_class=HTMLResponse)
def home():
    return """<html><body style="font-family:sans-serif;max-width:640px;margin:60px auto">
    <h1>ECM-Lite</h1><p>Content management platform is running.</p>
    <p><a href="/docs">Open the interactive app (Swagger UI)</a></p>
    <ol><li>POST /auth/register</li><li>Click Authorize, log in with email + password</li>
    <li>Try /nodes, /cases, /quality, /forms, /retention</li></ol></body></html>"""

@app.get("/health")
def health(): return {"status": "ok"}

# ---------- auth (form-based so the Swagger Authorize button works) ----------
@app.post("/auth/register", tags=["auth"])
def register(email: str, password: str):
    with Session() as s:
        if s.query(User).filter_by(email=email).first(): raise HTTPException(400, "Email already registered")
        u = User(email=email, pw=hash_pw(password)); s.add(u); s.commit()
        return {"id": u.id, "email": u.email}

@app.post("/auth/token", tags=["auth"])
def login(f: OAuth2PasswordRequestForm = Depends()):
    with Session() as s:
        u = s.query(User).filter_by(email=f.username).first()
        if not u or not check_pw(f.password, u.pw): raise HTTPException(401, "Invalid credentials")
        return {"access_token": make_token(u.id), "token_type": "bearer"}

# ---------- repository (OpenText-style) ----------
@app.post("/nodes/folders", tags=["repository"])
def create_folder(name: str, parent_id: str | None = None, user=U):
    with Session() as s:
        n = Node(name=name, kind="folder", parent_id=parent_id, owner_id=user.id); s.add(n); s.commit()
        audit(s, user, "create_folder", n.id); return {"id": n.id, "name": n.name}

def save_version(s, n, file: UploadFile, data: bytes):
    num = s.query(Version).filter_by(node_id=n.id).count() + 1
    path = os.path.join(STORE, f"{n.id}_v{num}")
    open(path, "wb").write(data)
    s.add(Version(node_id=n.id, number=num, path=path, filename=file.filename, mime=file.content_type, size=len(data)))
    if (file.content_type or "").startswith("text/"):
        n.text = data.decode("utf-8", "ignore")[:200000]
    s.commit(); return num

@app.post("/nodes/documents", tags=["repository"])
def upload(name: str = Form(...), parent_id: str | None = Form(None), category: str | None = Form(None),
           file: UploadFile = File(...), user=U):
    data = file.file.read()
    with Session() as s:
        n = Node(name=name, kind="document", parent_id=parent_id, category=category, owner_id=user.id)
        s.add(n); s.commit()
        v = save_version(s, n, file, data); audit(s, user, "upload", n.id, version=v)
        return {"id": n.id, "name": n.name, "version": v}

@app.post("/nodes/{node_id}/versions", tags=["repository"])
def new_version(node_id: str, file: UploadFile = File(...), user=U):
    with Session() as s:
        n = s.get(Node, node_id)
        if not n or n.kind != "document": raise HTTPException(404, "Document not found")
        if n.legal_hold: raise HTTPException(423, "Under legal hold")
        v = save_version(s, n, file, file.file.read()); audit(s, user, "new_version", n.id, version=v)
        return {"id": n.id, "version": v}

@app.get("/nodes", tags=["repository"])
def list_nodes(parent_id: str | None = None, user=U):
    with Session() as s:
        q = s.query(Node).filter_by(deleted=False, parent_id=parent_id)
        return [{"id": n.id, "name": n.name, "kind": n.kind, "category": n.category, "lifecycle": n.lifecycle} for n in q]

@app.get("/nodes/{node_id}", tags=["repository"])
def get_node(node_id: str, user=U):
    with Session() as s:
        n = s.get(Node, node_id)
        if not n or n.deleted: raise HTTPException(404, "Not found")
        vs = s.query(Version).filter_by(node_id=n.id).order_by(Version.number).all()
        return {"id": n.id, "name": n.name, "kind": n.kind, "category": n.category, "lifecycle": n.lifecycle,
                "legal_hold": n.legal_hold, "retention_until": n.retention_until,
                "versions": [{"version": v.number, "filename": v.filename, "size": v.size} for v in vs]}

@app.get("/nodes/{node_id}/download", tags=["repository"])
def download(node_id: str, version: int | None = None, user=U):
    with Session() as s:
        q = s.query(Version).filter_by(node_id=node_id)
        v = q.filter_by(number=version).first() if version else q.order_by(Version.number.desc()).first()
        if not v: raise HTTPException(404, "No such version")
        audit(s, user, "download", node_id, version=v.number)
        return FileResponse(v.path, filename=v.filename, media_type=v.mime)

@app.delete("/nodes/{node_id}", tags=["repository"])
def delete(node_id: str, user=U):
    with Session() as s:
        n = s.get(Node, node_id)
        if not n: raise HTTPException(404, "Not found")
        if n.legal_hold: raise HTTPException(423, "Legal hold active")
        n.deleted = True; s.commit(); audit(s, user, "delete", node_id); return {"status": "deleted"}

@app.get("/search", tags=["repository"])
def search(q: str, user=U):
    with Session() as s:
        like = f"%{q}%"
        rows = s.query(Node).filter(Node.deleted == False, (Node.name.ilike(like)) | (Node.text.ilike(like))
                                     | (Node.category.ilike(like))).limit(50).all()
        return [{"id": n.id, "name": n.name, "kind": n.kind, "category": n.category} for n in rows]

@app.post("/nodes/{node_id}/comments", tags=["repository"])
def comment(node_id: str, body: str, user=U):
    with Session() as s: return out(rec(s, "comment", node_id, author=user.email, body=body))

@app.get("/nodes/{node_id}/comments", tags=["repository"])
def comments(node_id: str, user=U):
    with Session() as s: return [out(r) for r in s.query(Record).filter_by(type="comment", ref=node_id)]

@app.get("/audit", tags=["repository"])
def audit_log(node_id: str | None = None, user=U):
    with Session() as s:
        q = s.query(Record).filter_by(type="audit")
        if node_id: q = q.filter_by(ref=node_id)
        return [out(r) for r in q.order_by(Record.created_at.desc()).limit(200)]

# ---------- workflow ----------
@app.post("/workflows/definitions", tags=["workflow"])
def wf_def(name: str, steps: list[str], user=U):
    with Session() as s: return out(rec(s, "wf_def", None, name=name, steps=steps))

@app.post("/workflows/instances", tags=["workflow"])
def wf_start(definition_id: str, node_id: str, user=U):
    with Session() as s:
        if not s.get(Record, definition_id): raise HTTPException(404, "Definition not found")
        return out(rec(s, "wf_inst", node_id, definition_id=definition_id, step=0, status="running"))

@app.post("/workflows/instances/{iid}/advance", tags=["workflow"])
def wf_advance(iid: str, approve: bool = True, user=U):
    with Session() as s:
        r = s.get(Record, iid); d = s.get(Record, r.data["definition_id"])
        data = dict(r.data)
        if not approve: data["status"] = "rejected"
        else:
            data["step"] += 1
            if data["step"] >= len(d.data["steps"]): data["status"] = "approved"
        r.data = data; s.commit(); audit(s, user, "workflow_advance", r.ref, **{"status": data["status"]})
        return out(r)

# ---------- cases (Newgen-style) ----------
@app.post("/cases", tags=["cases"])
def case_create(title: str, case_type: str | None = None, priority: str = "normal", user=U):
    with Session() as s:
        return out(rec(s, "case", None, title=title, case_type=case_type, priority=priority, status="open", linked_cases=[], linked_nodes=[]))

@app.get("/cases", tags=["cases"])
def case_list(user=U):
    with Session() as s: return [out(r) for r in s.query(Record).filter_by(type="case")]

@app.get("/cases/{cid}", tags=["cases"])
def case_get(cid: str, user=U):
    with Session() as s:
        c = s.get(Record, cid)
        if not c or c.type != "case": raise HTTPException(404, "Case not found")
        kids = s.query(Record).filter(Record.ref == cid, Record.type.in_(["case_note", "task"])).all()
        return {**out(c), "notes": [out(k) for k in kids if k.type == "case_note"], "tasks": [out(k) for k in kids if k.type == "task"]}

@app.post("/cases/{cid}/notes", tags=["cases"])
def case_note(cid: str, note: str, user=U):
    with Session() as s: return out(rec(s, "case_note", cid, author=user.email, note=note))

@app.post("/cases/{cid}/tasks", tags=["cases"])
def case_task(cid: str, title: str, assignee: str, user=U):
    with Session() as s: return out(rec(s, "task", cid, title=title, assignee=assignee, status="pending"))

@app.post("/cases/{cid}/link", tags=["cases"])
def case_link(cid: str, other_case_id: str | None = None, node_id: str | None = None, user=U):
    with Session() as s:
        c = s.get(Record, cid); d = dict(c.data)
        if other_case_id: d["linked_cases"] = sorted(set(d["linked_cases"]) | {other_case_id})
        if node_id: d["linked_nodes"] = sorted(set(d["linked_nodes"]) | {node_id})
        c.data = d; s.commit(); return out(c)

@app.patch("/cases/{cid}/status", tags=["cases"])
def case_status(cid: str, status: str, user=U):
    with Session() as s:
        c = s.get(Record, cid); c.data = {**c.data, "status": status}; s.commit(); return out(c)

# ---------- quality (Veeva-style) ----------
@app.patch("/quality/nodes/{node_id}/lifecycle", tags=["quality"])
def lifecycle(node_id: str, state: str, user=U):
    if state not in ("draft", "in_review", "effective", "superseded", "obsolete"): raise HTTPException(422, "Bad state")
    with Session() as s:
        n = s.get(Node, node_id); n.lifecycle = state; s.commit(); audit(s, user, "lifecycle", node_id, state=state)
        return {"id": n.id, "lifecycle": n.lifecycle}

@app.post("/quality/sign", tags=["quality"])
def sign(node_id: str, meaning: str, password: str, reason: str | None = None, user=U):
    if not check_pw(password, user.pw): raise HTTPException(401, "Password re-entry failed")
    with Session() as s:
        n = s.get(Node, node_id)
        if not n: raise HTTPException(404, "Not found")
        v = s.query(Version).filter_by(node_id=node_id).order_by(Version.number.desc()).first()
        r = rec(s, "esign", node_id, signer=user.email, meaning=meaning, reason=reason, version=v.number if v else None)
        audit(s, user, "e_signature", node_id, meaning=meaning); return out(r)

@app.post("/quality/events", tags=["quality"])
def q_event(event_type: str, title: str, description: str | None = None, node_id: str | None = None, user=U):
    if event_type not in ("capa", "deviation", "change_control", "audit", "complaint"): raise HTTPException(422, "Bad type")
    with Session() as s: return out(rec(s, "quality_event", node_id, event_type=event_type, title=title, description=description, status="open", owner=user.email))

@app.get("/quality/events", tags=["quality"])
def q_events(user=U):
    with Session() as s: return [out(r) for r in s.query(Record).filter_by(type="quality_event")]

@app.patch("/quality/events/{eid}/close", tags=["quality"])
def q_close(eid: str, user=U):
    with Session() as s:
        r = s.get(Record, eid); r.data = {**r.data, "status": "closed", "closed_at": datetime.utcnow().isoformat()}; s.commit(); return out(r)

@app.post("/quality/training", tags=["quality"])
def training(node_id: str, assignee: str, user=U):
    with Session() as s: return out(rec(s, "training", node_id, assignee=assignee, status="assigned"))

# ---------- forms (OnBase-style) ----------
@app.post("/forms/definitions", tags=["forms"])
def form_def(name: str, json_schema: dict, user=U):
    with Session() as s: return out(rec(s, "form_def", None, name=name, schema=json_schema))

@app.post("/forms/{form_id}/submissions", tags=["forms"])
def form_submit(form_id: str, data: dict, node_id: str | None = None, user=U):
    with Session() as s:
        f = s.get(Record, form_id)
        if not f or f.type != "form_def": raise HTTPException(404, "Form not found")
        try: validate(data, f.data["schema"])
        except ValidationError as e: raise HTTPException(422, e.message)
        return out(rec(s, "form_sub", form_id, data=data, node_id=node_id, submitted_by=user.email))

@app.get("/forms/{form_id}/submissions", tags=["forms"])
def form_subs(form_id: str, user=U):
    with Session() as s: return [out(r) for r in s.query(Record).filter_by(type="form_sub", ref=form_id)]

# ---------- retention ----------
@app.post("/retention/policies", tags=["retention"])
def policy(category: str, years: int, action: str = "flag_for_review", user=U):
    with Session() as s: return out(rec(s, "policy", category, category=category, years=years, action=action))

@app.post("/retention/nodes/{node_id}/apply", tags=["retention"])
def apply_ret(node_id: str, user=U):
    with Session() as s:
        n = s.get(Node, node_id)
        p = s.query(Record).filter_by(type="policy", ref=n.category).order_by(Record.created_at.desc()).first()
        if not p: raise HTTPException(404, "No policy for category")
        n.retention_until = datetime.utcnow() + timedelta(days=365 * p.data["years"]); s.commit()
        return {"id": n.id, "retention_until": n.retention_until}

@app.post("/retention/nodes/{node_id}/legal-hold", tags=["retention"])
def hold(node_id: str, hold: bool, user=U):
    with Session() as s:
        n = s.get(Node, node_id); n.legal_hold = hold; s.commit(); audit(s, user, "legal_hold", node_id, hold=hold)
        return {"id": n.id, "legal_hold": hold}

@app.post("/retention/sweep", tags=["retention"])
def sweep(user=U):
    with Session() as s:
        due = s.query(Node).filter(Node.retention_until != None, Node.retention_until <= datetime.utcnow(),
                                    Node.legal_hold == False, Node.deleted == False).all()
        for n in due:
            p = s.query(Record).filter_by(type="policy", ref=n.category).first()
            if p and p.data["action"] == "auto_delete": n.deleted = True
            else: n.attributes = {**(n.attributes or {}), "retention_flag": "review_required"}
            audit(s, user, "retention_sweep", n.id)
        s.commit(); return {"processed": len(due)}
