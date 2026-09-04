"""Loopback-only web interface for the internal Mise Company Fleet."""
from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime
from typing import Any, Literal, Mapping

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from psycopg.conninfo import conninfo_to_dict

from fleet_kernel.company_workspace import (
    CompanyWorkspace,
    CompanyWorkspaceConflict,
    CompanyWorkspaceError,
)


class ObjectiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    owner_role: str
    priority: int = Field(default=50, ge=0, le=100)
    due_at: datetime | None = None


class ObjectiveTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_state: str
    expected_version: int = Field(gt=0)
    work_subject_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_kind: Literal["schedule", "handoff", "status", "decision", "performance"]
    payload: dict[str, Any] = Field(default_factory=dict)


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mise Company Workspace</title><style>
:root{--navy:#071d36;--cream:#fbf5e8;--red:#d83a32;--ink:#17212b;--muted:#647181}
*{box-sizing:border-box}body{margin:0;background:var(--cream);color:var(--ink);font:15px/1.45 Inter,system-ui,sans-serif}
header{background:var(--navy);color:white;padding:26px 5vw;display:flex;justify-content:space-between;align-items:center}
h1{margin:0;font-size:24px}main{max-width:1180px;margin:30px auto;padding:0 20px}.gate,.card,.create{background:white;border:1px solid #dfd8ca;border-radius:14px;padding:18px;box-shadow:0 5px 18px #071d3610}
.gate{display:flex;gap:10px;margin-bottom:18px}.create{display:none;margin-bottom:18px}.create-row{display:grid;grid-template-columns:2fr 1fr 110px auto;gap:10px}input,select,button{font:inherit;padding:10px 12px;border:1px solid #c9c3b8;border-radius:8px}input{min-width:0}.gate input{flex:1}button{background:var(--navy);color:white;border:0;cursor:pointer}.actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px}.actions button{padding:7px 9px;font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:14px}.meta{color:var(--muted);font-size:13px}.state{color:var(--red);font-weight:700;text-transform:uppercase;font-size:12px}.empty{color:var(--muted);padding:35px;text-align:center}.counts{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 20px}.count{background:var(--navy);color:white;border-radius:999px;padding:7px 12px}code{font-size:12px;overflow-wrap:anywhere}#error{color:var(--red);margin:8px 0}@media(max-width:700px){.create-row{grid-template-columns:1fr}.gate{flex-direction:column}}
</style></head><body><header><h1>Mise Company Workspace</h1><span>Company Fleet daily driver</span></header>
<main><div class="gate"><input id="token" type="password" autocomplete="off" placeholder="Company workspace access token"><button onclick="load()">Open workspace</button></div><div id="create" class="create"><div class="create-row"><input id="title" placeholder="New company objective"><select id="owner"><option>scribe</option><option>cos</option><option>ccto</option><option>ccpo</option><option>ccde</option><option>ccro</option><option>ccfo</option><option>ccmo</option><option>cclo</option><option>ccgo</option><option>ccco</option><option>utility</option></select><input id="priority" type="number" min="0" max="100" value="50"><button onclick="createObjective()">Issue objective</button></div></div><div id="error"></div><div id="counts" class="counts"></div><div id="objectives" class="grid"><div class="empty">Enter the loopback access token to load Company Fleet state.</div></div></main>
<script>
const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options={}){const token=document.getElementById('token').value;options.headers={...(options.headers||{}),Authorization:'Bearer '+token};if(options.body)options.headers['Content-Type']='application/json';const r=await fetch(path,options);if(!r.ok)throw new Error((await r.json()).detail||r.statusText);return r.json()}
const next={queued:['active','held','cancelled'],active:['needs_input','ready_for_review','held','cancelled'],needs_input:['active','held','cancelled'],ready_for_review:['active','completed','held','cancelled'],held:['active','cancelled']};
function card(o){const buttons=(next[o.state]||[]).map(s=>`<button onclick="transition('${o.id}','${s}',${o.version})">${esc(s.replaceAll('_',' '))}</button>`).join('');return `<article class="card"><div class="state">${esc(o.state)}</div><h2>${esc(o.title)}</h2><p>${esc(o.description)}</p><div class="meta">Owner: ${esc(o.owner_role)} · Priority ${o.priority} · v${o.version}</div>${o.work_subject_sha256?`<p><code>${esc(o.work_subject_sha256)}</code></p>`:''}<div class="actions">${buttons}<button onclick="detail('${o.id}')">history & reviews</button></div><div id="detail-${o.id}" class="meta"></div></article>`}
async function load(){try{document.getElementById('error').textContent='';const [dash,items]=await Promise.all([api('/api/dashboard'),api('/api/objectives')]);document.getElementById('create').style.display='block';document.getElementById('counts').innerHTML=Object.entries(dash.objective_states).map(([k,v])=>`<span class="count">${esc(k)}: ${v}</span>`).join('');document.getElementById('objectives').innerHTML=items.length?items.map(card).join(''):'<div class="empty">No company objectives yet.</div>'}catch(e){document.getElementById('error').textContent=e.message}}
async function createObjective(){try{const title=document.getElementById('title').value.trim();if(!title)throw new Error('Objective title is required');await api('/api/objectives',{method:'POST',body:JSON.stringify({title,owner_role:document.getElementById('owner').value,priority:Number(document.getElementById('priority').value)})});document.getElementById('title').value='';await load()}catch(e){document.getElementById('error').textContent=e.message}}
async function transition(id,state,version){try{let subject=null;if(state==='ready_for_review'){subject=prompt('Exact SHA-256 subject for Fable review:');if(!subject)return}await api(`/api/objectives/${id}/transition`,{method:'POST',body:JSON.stringify({to_state:state,expected_version:version,work_subject_sha256:subject})});await load()}catch(e){document.getElementById('error').textContent=e.message}}
async function detail(id){try{const d=await api(`/api/objectives/${id}`);const reviews=d.reviews.length?d.reviews.map(r=>`${esc(r.verdict)} · ${esc(r.subject_sha256.slice(0,12))}`).join('<br>'):'No formal Fable review yet';document.getElementById(`detail-${id}`).innerHTML=`<p>${d.events.length} durable events</p><p>${reviews}</p>`}catch(e){document.getElementById('error').textContent=e.message}}
</script></body></html>"""


def create_company_workspace_app(
    *, service: CompanyWorkspace | None = None, access_token: str | None = None,
) -> FastAPI:
    token = access_token if access_token is not None else os.environ.get("COMPANY_WORKSPACE_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("COMPANY_WORKSPACE_ACCESS_TOKEN is required")
    if service is None:
        database_url = os.environ.get("COMPANY_WORKSPACE_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("COMPANY_WORKSPACE_DATABASE_URL is required")
        service = CompanyWorkspace(conninfo_to_dict(database_url))
    workspace = service
    bearer = HTTPBearer(auto_error=False)
    app = FastAPI(
        title="Mise Company Workspace", docs_url=None, redoc_url=None,
        openapi_url=None,
    )

    def authorize(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if (
            credentials is None or credentials.scheme.lower() != "bearer"
            or not hmac.compare_digest(credentials.credentials, token)
        ):
            raise HTTPException(status_code=401, detail="invalid Company Workspace token")

    def call(method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(workspace, method)(*args, **kwargs)
        except CompanyWorkspaceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CompanyWorkspaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return _HTML

    @app.get("/api/objectives", dependencies=[Depends(authorize)])
    def objectives(state: str | None = None) -> list[Mapping[str, Any]]:
        return [item.to_dict() for item in call("list_objectives", state=state)]

    @app.post("/api/objectives", dependencies=[Depends(authorize)], status_code=201)
    def create_objective(request: ObjectiveCreate) -> Mapping[str, Any]:
        return call(
            "create_objective", **request.model_dump(), actor_role="founder",
        ).to_dict()

    @app.get("/api/objectives/{objective_id}", dependencies=[Depends(authorize)])
    def objective_detail(objective_id: uuid.UUID) -> Mapping[str, Any]:
        return call("objective_detail", objective_id)

    @app.post("/api/objectives/{objective_id}/transition", dependencies=[Depends(authorize)])
    def transition(objective_id: uuid.UUID, request: ObjectiveTransition) -> Mapping[str, Any]:
        return call(
            "transition", objective_id, **request.model_dump(), actor_role="founder",
        ).to_dict()

    @app.post("/api/objectives/{objective_id}/events", dependencies=[Depends(authorize)], status_code=201)
    def record_event(objective_id: uuid.UUID, request: EventCreate) -> Mapping[str, Any]:
        return call(
            "record_event", objective_id, **request.model_dump(), actor_role="founder",
        )

    @app.get("/api/dashboard", dependencies=[Depends(authorize)])
    def dashboard() -> Mapping[str, Any]:
        return call("dashboard")

    return app
