import os, sqlite3, hmac
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

ROOT=Path(__file__).resolve().parent
DB=os.getenv("DATABASE_PATH", str(ROOT/"falka.db"))

# KEZDETI ADMIN JELSZO – ITT TUDOD MEGVALTOZTATNI:
ADMIN_PASSWORD="ABC123ABC123"
SESSION_SECRET=os.getenv("SESSION_SECRET","CSERELD-LE-EGY-HOSSZU-VELETLEN-TITKOS-KULCSRA-123456789")
COOKIE_SECURE=os.getenv("COOKIE_SECURE","false").lower()=="true"

app=FastAPI(title="Falka Championship")
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET,session_cookie="falka_admin_session",same_site="lax",https_only=COOKIE_SECURE,max_age=28800)
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c

def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS players(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL COLLATE NOCASE UNIQUE,battles INTEGER NOT NULL DEFAULT 0,wins INTEGER NOT NULL DEFAULT 0,eliminations INTEGER NOT NULL DEFAULT 0,executioner INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS awards(id INTEGER PRIMARY KEY AUTOINCREMENT,battle_label TEXT NOT NULL,battle_date TEXT DEFAULT '',category TEXT NOT NULL CHECK(category IN ('executioner','wolf_elimination','wolf_savior')),player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,note TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_awards_category ON awards(category);
        """)
init()

def admin(req: Request):
    if req.session.get("admin") is not True: raise HTTPException(401,"Admin bejelentkezés szükséges.")

def clean_name(n):
    n=" ".join(str(n or "").strip().split())
    if not n or len(n)>40: raise HTTPException(400,"A név 1–40 karakter lehet.")
    return n

@app.get("/",response_class=HTMLResponse)
def home(): return (ROOT/"static/index.html").read_text(encoding="utf-8")
@app.get("/admin",response_class=HTMLResponse)
def admin_page(): return (ROOT/"static/admin.html").read_text(encoding="utf-8")

@app.get("/api/public")
def public():
    with conn() as c:
        players=[dict(x) for x in c.execute("SELECT id,name,battles,wins,eliminations,executioner FROM players ORDER BY name COLLATE NOCASE")]
        rankings={}
        for cat in ("executioner","wolf_elimination","wolf_savior"):
            rankings[cat]=[dict(x) for x in c.execute("SELECT p.id,p.name,COUNT(a.id) points FROM awards a JOIN players p ON p.id=a.player_id WHERE a.category=? GROUP BY p.id,p.name ORDER BY points DESC,p.name COLLATE NOCASE",(cat,))]
        count=c.execute("SELECT COUNT(DISTINCT battle_label) n FROM awards").fetchone()["n"]
        labels=c.execute("SELECT battle_label,MAX(id) last_id FROM awards GROUP BY battle_label ORDER BY last_id DESC LIMIT 30").fetchall()
        history=[]
        for x in labels:
            rows=c.execute("SELECT a.category,p.name FROM awards a JOIN players p ON p.id=a.player_id WHERE a.battle_label=? ORDER BY a.id",(x["battle_label"],)).fetchall()
            history.append({"battle_label":x["battle_label"],"items":[dict(r) for r in rows]})
    return {"players":players,"rankings":rankings,"battle_count":count,"history":history}

@app.get("/api/player/{pid}")
def profile(pid:int):
    with conn() as c: x=c.execute("SELECT id,name,battles,wins,eliminations,executioner FROM players WHERE id=?",(pid,)).fetchone()
    if not x: raise HTTPException(404,"Játékos nem található.")
    return dict(x)

@app.post("/api/login")
def login(req:Request,password:str=Form(...)):
    if not hmac.compare_digest(password,ADMIN_PASSWORD): raise HTTPException(401,"Hibás jelszó.")
    req.session.clear(); req.session["admin"]=True; return {"ok":True}
@app.get("/api/admin/status")
def status(req:Request): return {"logged_in":req.session.get("admin") is True}
@app.post("/api/logout")
def logout(req:Request,_=Depends(admin)): req.session.clear(); return {"ok":True}

@app.get("/api/admin/players")
def players_admin(_=Depends(admin)):
    with conn() as c: return [dict(x) for x in c.execute("SELECT * FROM players ORDER BY name COLLATE NOCASE")]
@app.post("/api/admin/players")
def add_player(name:str=Form(...),_=Depends(admin)):
    try:
        with conn() as c: cur=c.execute("INSERT INTO players(name) VALUES(?)",(clean_name(name),)); pid=cur.lastrowid
        return {"ok":True,"id":pid}
    except sqlite3.IntegrityError: raise HTTPException(409,"Ez a játékos már létezik.")
@app.put("/api/admin/players/{pid}")
async def update_player(pid:int,req:Request,_=Depends(admin)):
    d=await req.json(); name=clean_name(d.get("name")); vals=[]
    for k in ("battles","wins","eliminations","executioner"):
        try: v=int(d.get(k,0))
        except: raise HTTPException(400,f"Hibás érték: {k}")
        if v<0: raise HTTPException(400,"Negatív érték nem engedélyezett.")
        vals.append(v)
    if vals[1]>vals[0]: raise HTTPException(400,"A nyert csaták száma nem lehet több a csaták számánál.")
    try:
        with conn() as c:
            cur=c.execute("UPDATE players SET name=?,battles=?,wins=?,eliminations=?,executioner=? WHERE id=?",(name,*vals,pid))
            if not cur.rowcount: raise HTTPException(404,"Játékos nem található.")
        return {"ok":True}
    except sqlite3.IntegrityError: raise HTTPException(409,"Ez a név már foglalt.")
@app.delete("/api/admin/players/{pid}")
def delete_player(pid:int,_=Depends(admin)):
    with conn() as c:
        cur=c.execute("DELETE FROM players WHERE id=?",(pid,))
        if not cur.rowcount: raise HTTPException(404,"Játékos nem található.")
    return {"ok":True}

@app.get("/api/admin/awards")
def awards_admin(_=Depends(admin)):
    with conn() as c: return [dict(x) for x in c.execute("SELECT a.*,p.name FROM awards a JOIN players p ON p.id=a.player_id ORDER BY a.id DESC LIMIT 300")]
@app.post("/api/admin/awards")
async def add_award(req:Request,_=Depends(admin)):
    d=await req.json(); label=str(d.get("battle_label","")).strip()[:80]; cat=d.get("category"); ids=d.get("player_ids",[])
    if not label: raise HTTPException(400,"Add meg a csata azonosítóját.")
    if cat not in ("executioner","wolf_elimination","wolf_savior"): raise HTTPException(400,"Hibás kitüntetés.")
    try: ids=list(dict.fromkeys(int(i) for i in ids))
    except: raise HTTPException(400,"Hibás játékosválasztás.")
    if not ids: raise HTTPException(400,"Válassz játékost.")
    if cat!="wolf_savior" and len(ids)!=1: raise HTTPException(400,"Ebben a kategóriában csatánként csak egy játékos lehet.")
    with conn() as c:
        q=",".join("?" for _ in ids)
        if len(c.execute(f"SELECT id FROM players WHERE id IN ({q})",ids).fetchall())!=len(ids): raise HTTPException(400,"Érvénytelen játékos.")
        old={r["player_id"] for r in c.execute("SELECT player_id FROM awards WHERE battle_label=? AND category=?",(label,cat))}
        if cat!="wolf_savior" and old: raise HTTPException(409,"Ehhez a csatához már van ilyen kitüntetés.")
        if old.intersection(ids): raise HTTPException(409,"Ez a játékos már kapott ilyen kitüntetést ebben a csatában.")
        c.executemany("INSERT INTO awards(battle_label,battle_date,category,player_id,note) VALUES(?,?,?,?,?)",[(label,str(d.get("battle_date","")),cat,i,str(d.get("note",""))[:250]) for i in ids])
    return {"ok":True}
@app.delete("/api/admin/awards/{aid}")
def delete_award(aid:int,_=Depends(admin)):
    with conn() as c:
        cur=c.execute("DELETE FROM awards WHERE id=?",(aid,))
        if not cur.rowcount: raise HTTPException(404,"Kitüntetés nem található.")
    return {"ok":True}
