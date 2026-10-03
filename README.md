# ECM-Lite (Render-ready)

Single-service version of ECM-Lite: auth, versioned repository, search, workflow, cases (Newgen-style),
QMS/e-signatures (Veeva-style), e-forms and retention (OnBase-style).

Run locally: `pip install -r requirements.txt && uvicorn app.main:app --reload` then open http://localhost:8000/docs

Deploy: Render -> New -> Blueprint -> pick this repo (render.yaml is included).
Free tier uses ephemeral disk: data resets on restart. Set DATABASE_URL (Postgres) to persist metadata.
