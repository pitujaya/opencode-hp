"""FastAPI + SQLite untuk service-hp-management.
- Serve frontend index.html di /
- REST API di /api/* (services, spareparts, toko, promo, auth, lacak)
- Persist ke servis.db (SQLite, stdlib saja)
- Otomatis migrasi dari db.json bila file itu masih ada
"""
import hashlib
import json
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).parent
DB_SQLITE = BASE_DIR / "servis.db"
DB_JSON = BASE_DIR / "db.json"
INDEX_PATH = BASE_DIR / "index.html"

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_SQLITE), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_sqlite() -> None:
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS services(
            id TEXT PRIMARY KEY, nota TEXT UNIQUE, nama TEXT, telepon TEXT,
            alamat TEXT, merk TEXT, tipe TEXT, imei TEXT, warna TEXT,
            sandi TEXT, lengkap TEXT, keluhan TEXT, tgl TEXT, estimasi TEXT,
            teknisi TEXT, status TEXT, biaya REAL DEFAULT 0, bayar TEXT,
            catatan TEXT, created_at INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS spareparts(
            id TEXT PRIMARY KEY, nama TEXT, kategori TEXT, merk TEXT,
            sku TEXT, hpp REAL DEFAULT 0, jual REAL DEFAULT 0,
            pasang REAL DEFAULT 0, stok REAL DEFAULT 0, min REAL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS toko(
            id INTEGER PRIMARY KEY CHECK(id=1),
            nama TEXT, alamat TEXT, telp TEXT, logo TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS promo(
            id INTEGER PRIMARY KEY CHECK(id=1),
            aktif INTEGER DEFAULT 0, dari TEXT DEFAULT '',
            sampai TEXT DEFAULT '', idx INTEGER DEFAULT 0,
            slides TEXT DEFAULT '[]')""")
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            hp TEXT PRIMARY KEY, nama TEXT, pw TEXT, tgl TEXT,
            role TEXT DEFAULT 'user')""")
        # migrasi DB lama: tambah kolom role bila belum ada
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
            if "role" not in cols:
                c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
        except Exception:
            pass
        # seed default superadmin (login: superadmin / 0000000000)
        try:
            cols2 = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
            if "requested_role" not in cols2:
                c.execute("ALTER TABLE users ADD COLUMN requested_role TEXT DEFAULT 'karyawan'")
            if "status" not in cols2:
                c.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'approved'")
            c.execute("UPDATE users SET status='approved' WHERE status IS NULL OR status=''")
            c.execute(
                "INSERT OR IGNORE INTO users(hp,nama,pw,tgl,role,requested_role,status) VALUES(?,?,?,?,?,?,?)",
                ("0000000000", "superadmin", hash_pw("@ngawi2026"), today_str(),
                 "superadmin", "superadmin", "approved"),
            )
            c.execute("UPDATE users SET role='superadmin', status='approved' WHERE hp='0000000000'")
        except Exception:
            pass
        c.execute(
            "INSERT OR IGNORE INTO toko(id,nama,alamat,telp,logo) VALUES(1,?,?,?,?)",
            ("Servis HP Berkah", "Jl. Merdeka No.10", "0812-xxxx-xxxx", ""),
        )
        c.execute(
            "INSERT OR IGNORE INTO promo(id,aktif,dari,sampai,idx,slides) VALUES(1,0,'','',0,'[]')"
        )
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        for k, v in SETTING_DEFAULTS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        c.commit()


def migrate_json_to_sqlite() -> dict:
    """Pindahkan isi db.json ke SQLite sekali saja. Return {'migrated': n}."""
    if not DB_JSON.exists():
        return {"migrated": 0, "note": "db.json tidak ada, pakai SQLite kosong/default"}
    try:
        data = json.loads(DB_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        return {"migrated": 0, "note": f"db.json rusak: {e}"}
    counts = {"services": 0, "spareparts": 0, "users": 0}
    with _lock, _conn() as c:
        for d in data.get("services", []):
            try:
                c.execute(
                    """INSERT OR IGNORE INTO services(id,nota,nama,telepon,alamat,merk,tipe,
                    imei,warna,sandi,lengkap,keluhan,tgl,estimasi,teknisi,status,biaya,bayar,catatan,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        d.get("id") or f"SRV-{int(time.time()*1000)}",
                        d.get("nota"), d.get("nama", ""), d.get("telepon", ""),
                        d.get("alamat", ""), d.get("merk", ""), d.get("tipe", ""),
                        d.get("imei", ""), d.get("warna", ""), d.get("sandi", ""),
                        d.get("lengkap", ""), d.get("keluhan", ""), d.get("tgl") or today_str(),
                        d.get("estimasi", ""), d.get("teknisi", "-"), d.get("status", "Antri"),
                        float(d.get("biaya") or 0), d.get("bayar", "Belum Lunas"),
                        d.get("catatan", ""), int(time.time()),
                    ),
                )
                if c.total_changes:
                    counts["services"] += 1
            except Exception:
                pass
        for d in data.get("spareparts", []):
            try:
                c.execute(
                    """INSERT OR IGNORE INTO spareparts(id,nama,kategori,merk,sku,hpp,jual,pasang,stok,min)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        d.get("id") or f"PRT-{int(time.time()*1000)}",
                        d.get("nama", ""), d.get("kategori", "Lainnya"), d.get("merk", "Universal"),
                        d.get("sku", ""), float(d.get("hpp") or 0), float(d.get("jual") or 0),
                        float(d.get("pasang") or 0), float(d.get("stok") or 0), float(d.get("min") or 0),
                    ),
                )
                if c.total_changes:
                    counts["users"] += 0
                    counts["spareparts"] += 1
            except Exception:
                pass
        for u in data.get("users", []):
            try:
                c.execute(
                    "INSERT OR IGNORE INTO users(hp,nama,pw,tgl,role,requested_role,status) VALUES(?,?,?,?,?,?,?)",
                    (u.get("hp"), u.get("nama"), u.get("pw"), u.get("tgl") or today_str(),
                     u.get("role") or "user", u.get("requested_role") or "karyawan",
                     u.get("status") or "approved"),
                )
                if c.total_changes:
                    counts["users"] += 1
            except Exception:
                pass
        t = data.get("toko")
        if isinstance(t, dict) and t.get("nama"):
            c.execute(
                "UPDATE toko SET nama=?,alamat=?,telp=?,logo=? WHERE id=1",
                (t.get("nama", ""), t.get("alamat", ""), t.get("telp", ""), t.get("logo", "")),
            )
        p = data.get("promo")
        if isinstance(p, dict):
            c.execute(
                "UPDATE promo SET aktif=?,dari=?,sampai=?,idx=?,slides=? WHERE id=1",
                (1 if p.get("aktif") else 0, p.get("dari", ""), p.get("sampai", ""),
                 int(p.get("idx") or 0), json.dumps(p.get("slides") or [], ensure_ascii=False)),
            )
        c.commit()
    try:
        DB_JSON.rename(DB_JSON.with_suffix(".json.bak"))
    except Exception:
        pass
    return {"migrated": counts, "note": "db.json -> servis.db, file lama jadi db.json.bak"}


def norm_hp(h: str) -> str:
    d = "".join(c for c in str(h or "") if c.isdigit())
    if d.startswith("62"):
        d = "0" + d[2:]
    return d


def hash_pw(p: str) -> str:
    return hashlib.sha256(("hp$" + str(p)).encode("utf-8")).hexdigest()


def today_str() -> str:
    return date.today().isoformat()


SETTING_DEFAULTS = {"nota_prefix": "NT", "stok_min_default": "2", "auto_preview": "1"}
SETTING_KEYS = tuple(SETTING_DEFAULTS.keys())


def get_setting(c: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if r and r[0] is not None:
            return str(r[0])
    except Exception:
        pass
    return default


def row_dict(r: sqlite3.Row) -> dict:
    return dict(r) if r is not None else {}


def gen_nota(c: sqlite3.Connection, tgl: str) -> str:
    nprefix = get_setting(c, "nota_prefix", "NT").strip() or "NT"
    prefix = nprefix + "-" + (tgl or today_str())[:10].replace("-", "") + "-"
    cur = c.execute("SELECT nota FROM services WHERE nota LIKE ?", (prefix + "%",))
    mx = 0
    for (n,) in cur.fetchall():
        try:
            mx = max(mx, int(str(n)[-3:]))
        except ValueError:
            pass
    cand = mx + 1
    while True:
        nota = prefix + str(cand).zfill(3)
        exists = c.execute("SELECT 1 FROM services WHERE nota=?", (nota,)).fetchone()
        if not exists:
            return nota
        cand += 1


init_sqlite()

app = FastAPI(title="service-hp-management (SQLite)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ServiceIn(BaseModel):
    id: Optional[str] = None
    nota: Optional[str] = None
    nama: str = ""
    telepon: str = ""
    alamat: str = ""
    merk: str = ""
    tipe: str = ""
    imei: str = ""
    warna: str = ""
    sandi: str = ""
    lengkap: str = ""
    keluhan: str = ""
    tgl: Optional[str] = None
    estimasi: str = ""
    teknisi: str = "-"
    status: str = "Antri"
    biaya: float = 0
    bayar: str = "Belum Lunas"
    catatan: str = ""


class PartIn(BaseModel):
    id: Optional[str] = None
    nama: str = ""
    kategori: str = "Lainnya"
    merk: str = "Universal"
    sku: str = ""
    hpp: float = 0
    jual: float = 0
    pasang: float = 0
    stok: float = 0
    min: float = 0


class TokoIn(BaseModel):
    nama: str = ""
    alamat: str = ""
    telp: str = ""
    logo: str = ""


class PromoIn(BaseModel):
    aktif: bool = False
    dari: str = ""
    sampai: str = ""
    idx: int = 0
    slides: list[Any] = Field(default_factory=list)


class RegisterIn(BaseModel):
    nama: str
    hp: str
    password: str
    role: str = "karyawan"


class LoginIn(BaseModel):
    hp: str
    password: str


@app.get("/api/health")
def health():
    with _lock, _conn() as c:
        ns = c.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        np = c.execute("SELECT COUNT(*) FROM spareparts").fetchone()[0]
    return {"status": "ok", "db": "sqlite", "services": ns, "spareparts": np}


@app.post("/api/migrate")
def migrate(actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
    return migrate_json_to_sqlite()


@app.get("/api/services")
def list_services(q: str = Query(default=""), status: str = Query(default=""),
                  bayar: str = Query(default="")):
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM services ORDER BY created_at DESC").fetchall()
    ql = (q or "").lower()
    out = []
    for r in rows:
        d = row_dict(r)
        d.pop("created_at", None)
        hay = " ".join(str(d.get(k) or "") for k in
                       ("nota", "id", "nama", "telepon", "merk", "tipe", "keluhan", "teknisi", "bayar")).lower()
        if ql and ql not in hay:
            continue
        if status and d.get("status") != status:
            continue
        if bayar and (d.get("bayar") or "Belum Lunas") != bayar:
            continue
        out.append(d)
    return out


@app.post("/api/services", status_code=201)
def create_service(payload: ServiceIn, actor: str = Header(default="", alias="X-Actor-Hp")):
    data = payload.model_dump()
    if not data.get("tgl"):
        data["tgl"] = today_str()
    if not data.get("id"):
        data["id"] = "SRV-" + str(int(time.time() * 1000))[-6:]
    if not data.get("nama") or not data.get("telepon") or not data.get("merk"):
        raise HTTPException(status_code=422, detail="nama/telepon/merk wajib diisi")
    with _lock, _conn() as c:
        require_roles(c, actor, "kasir", "admin", "superadmin")
        if not data.get("nota"):
            data["nota"] = gen_nota(c, data["tgl"])
        if c.execute("SELECT 1 FROM services WHERE nota=?", (data["nota"],)).fetchone():
            data["nota"] = gen_nota(c, data["tgl"])
        try:
            c.execute(
                """INSERT INTO services(id,nota,nama,telepon,alamat,merk,tipe,imei,warna,sandi,
                lengkap,keluhan,tgl,estimasi,teknisi,status,biaya,bayar,catatan,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (data["id"], data["nota"], data["nama"], data["telepon"], data["alamat"],
                 data["merk"], data["tipe"], data["imei"], data["warna"], data["sandi"],
                 data["lengkap"], data["keluhan"], data["tgl"], data["estimasi"],
                 data["teknisi"], data["status"], float(data["biaya"] or 0),
                 data["bayar"], data["catatan"], int(time.time())),
            )
            c.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="id/nota sudah ada")
    return data


@app.get("/api/services/{sid}")
def get_service(sid: str):
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if not r:
            r = c.execute("SELECT * FROM services WHERE nota=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="service tidak ditemukan")
    d = row_dict(r)
    d.pop("created_at", None)
    return d


@app.put("/api/services/{sid}")
def update_service(sid: str, payload: ServiceIn,
                   actor: str = Header(default="", alias="X-Actor-Hp")):
    data = payload.model_dump()
    with _lock, _conn() as c:
        require_roles(c, actor, "kasir", "admin", "superadmin")
        old = c.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="service tidak ditemukan")
        old_d = row_dict(old)
        data["id"] = sid
        if not data.get("nota"):
            data["nota"] = old_d.get("nota") or gen_nota(c, data.get("tgl") or today_str())
        if not data.get("tgl"):
            data["tgl"] = old_d.get("tgl") or today_str()
        c.execute(
            """UPDATE services SET nota=?,nama=?,telepon=?,alamat=?,merk=?,tipe=?,imei=?,warna=?,
            sandi=?,lengkap=?,keluhan=?,tgl=?,estimasi=?,teknisi=?,status=?,biaya=?,bayar=?,catatan=?
            WHERE id=?""",
            (data["nota"], data["nama"], data["telepon"], data["alamat"], data["merk"],
             data["tipe"], data["imei"], data["warna"], data["sandi"], data["lengkap"],
             data["keluhan"], data["tgl"], data["estimasi"], data["teknisi"], data["status"],
             float(data["biaya"] or 0), data["bayar"], data["catatan"], sid),
        )
        c.commit()
    return data


@app.delete("/api/services/{sid}")
def delete_service(sid: str, actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_roles(c, actor, "admin", "superadmin")
        cur = c.execute("DELETE FROM services WHERE id=?", (sid,))
        c.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="service tidak ditemukan")
    return {"deleted": sid}


@app.get("/api/lacak")
def lacak(q: str = Query(default="")):
    ql = (q or "").strip()
    if not ql:
        return []
    like = f"%{ql}%"
    with _lock, _conn() as c:
        rows = c.execute(
            """SELECT * FROM services WHERE nota LIKE ? OR nama LIKE ? OR id LIKE ?
            ORDER BY created_at DESC LIMIT 20""", (like, like, like)).fetchall()
    return [row_dict(r) for r in rows]


@app.get("/api/spareparts")
def list_parts(q: str = Query(default="")):
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM spareparts").fetchall()
    ql = (q or "").lower()
    out = [row_dict(r) for r in rows]
    if ql:
        out = [d for d in out if ql in " ".join(
            str(d.get(k) or "") for k in ("nama", "sku", "id", "merk", "kategori")).lower()]
    return out


@app.post("/api/spareparts", status_code=201)
def create_part(payload: PartIn, actor: str = Header(default="", alias="X-Actor-Hp")):
    data = payload.model_dump()
    if not data.get("id"):
        data["id"] = "PRT-" + str(int(time.time() * 1000))[-6:]
    if not data.get("nama"):
        raise HTTPException(status_code=422, detail="nama sparepart wajib")
    with _lock, _conn() as c:
        require_roles(c, actor, "kasir", "admin", "superadmin")
        try:
            c.execute(
                """INSERT INTO spareparts(id,nama,kategori,merk,sku,hpp,jual,pasang,stok,min)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (data["id"], data["nama"], data["kategori"], data["merk"], data["sku"],
                 float(data["hpp"] or 0), float(data["jual"] or 0), float(data["pasang"] or 0),
                 float(data["stok"] or 0), float(data["min"] or 0)),
            )
            c.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="id sudah ada")
    return data


@app.put("/api/spareparts/{pid}")
def update_part(pid: str, payload: PartIn, actor: str = Header(default="", alias="X-Actor-Hp")):
    data = payload.model_dump()
    with _lock, _conn() as c:
        require_roles(c, actor, "kasir", "admin", "superadmin")
        if not c.execute("SELECT 1 FROM spareparts WHERE id=?", (pid,)).fetchone():
            raise HTTPException(status_code=404, detail="sparepart tidak ditemukan")
        c.execute(
            """UPDATE spareparts SET nama=?,kategori=?,merk=?,sku=?,hpp=?,jual=?,pasang=?,stok=?,min=?
            WHERE id=?""",
            (data["nama"], data["kategori"], data["merk"], data["sku"],
             float(data["hpp"] or 0), float(data["jual"] or 0), float(data["pasang"] or 0),
             float(data["stok"] or 0), float(data["min"] or 0), pid),
        )
        c.commit()
    data["id"] = pid
    return data


@app.delete("/api/spareparts/{pid}")
def delete_part(pid: str, actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_roles(c, actor, "admin", "superadmin")
        cur = c.execute("DELETE FROM spareparts WHERE id=?", (pid,))
        c.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="sparepart tidak ditemukan")
    return {"deleted": pid}


@app.get("/api/toko")
def get_toko():
    with _lock, _conn() as c:
        r = c.execute("SELECT nama,alamat,telp,logo FROM toko WHERE id=1").fetchone()
    return row_dict(r)


@app.put("/api/toko")
def put_toko(payload: TokoIn, actor: str = Header(default="", alias="X-Actor-Hp")):
    d = payload.model_dump()
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        c.execute("UPDATE toko SET nama=?,alamat=?,telp=?,logo=? WHERE id=1",
                  (d["nama"], d["alamat"], d["telp"], d["logo"]))
        c.commit()
    return d


@app.get("/api/promo")
def get_promo():
    with _lock, _conn() as c:
        r = c.execute("SELECT aktif,dari,sampai,idx,slides FROM promo WHERE id=1").fetchone()
    d = row_dict(r)
    try:
        d["slides"] = json.loads(d.get("slides") or "[]")
    except Exception:
        d["slides"] = []
    d["aktif"] = bool(d.get("aktif"))
    return d


@app.put("/api/promo")
def put_promo(payload: PromoIn, actor: str = Header(default="", alias="X-Actor-Hp")):
    d = payload.model_dump()
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        c.execute("UPDATE promo SET aktif=?,dari=?,sampai=?,idx=?,slides=? WHERE id=1",
                  (1 if d["aktif"] else 0, d["dari"], d["sampai"],
                   int(d["idx"] or 0), json.dumps(d["slides"] or [], ensure_ascii=False)))
        c.commit()
    return d


@app.post("/api/auth/register")
def register(payload: RegisterIn):
    nama = (payload.nama or "").strip()
    hp = norm_hp(payload.hp)
    pw = payload.password or ""
    role_req = (payload.role or "karyawan").strip().lower()
    if role_req not in ("admin", "kasir", "karyawan"):
        role_req = "karyawan"
    if not nama:
        raise HTTPException(status_code=422, detail="nama wajib")
    if len(hp) < 9 or len(hp) > 15:
        raise HTTPException(status_code=422, detail="No HP tidak valid")
    if len(pw) < 4:
        raise HTTPException(status_code=422, detail="password min 4")
    with _lock, _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE hp=?", (hp,)).fetchone():
            raise HTTPException(status_code=409, detail="No HP sudah terdaftar")
        # akun baru berstatus pending sampai disetujui superadmin
        c.execute(
            "INSERT INTO users(hp,nama,pw,tgl,role,requested_role,status) VALUES(?,?,?,?,?,?,?)",
            (hp, nama, hash_pw(pw), today_str(), "user", role_req, "pending"),
        )
        c.commit()
    return {"nama": nama, "hp": hp, "requested_role": role_req,
            "status": "pending", "message": "menunggu persetujuan superadmin"}


class PasswordIn(BaseModel):
    hp: str
    old_password: str = ""
    new_password: str = ""


def find_user(c: sqlite3.Connection, ident: str):
    """Cari user by No HP atau nama (case-insensitive)."""
    ident = (ident or "").strip()
    hp = norm_hp(ident)
    r = c.execute("SELECT hp,nama,pw,role,requested_role,status FROM users WHERE hp=?", (hp,)).fetchone() if hp else None
    if not r:
        r = c.execute(
            "SELECT hp,nama,pw,role,requested_role,status FROM users WHERE lower(nama)=lower(?)", (ident,)).fetchone()
    return r


def actor_role(c: sqlite3.Connection, actor: str) -> str:
    """Peran aktor dari header X-Actor-Hp. '' = tamu / sesi tak valid."""
    a = find_user(c, actor or "")
    if not a or (a["status"] or "approved") != "approved":
        return ""
    return a["role"] or "user"


def require_roles(c: sqlite3.Connection, actor: str, *roles: str) -> str:
    r = actor_role(c, actor)
    if r not in roles:
        raise HTTPException(status_code=403, detail="peran tidak diizinkan")
    return r


@app.get("/api/auth/me")
def me(actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        r = find_user(c, actor)
    if not r or (r["status"] or "approved") != "approved":
        raise HTTPException(status_code=401, detail="sesi tidak valid")
    return {"nama": r["nama"], "hp": r["hp"], "role": r["role"] or "user",
            "status": r["status"] or "approved"}


@app.post("/api/auth/login")
def login(payload: LoginIn):
    with _lock, _conn() as c:
        r = find_user(c, payload.hp)
    if not r:
        raise HTTPException(status_code=404, detail="Akun belum terdaftar")
    if (r["status"] or "approved") == "pending":
        raise HTTPException(status_code=403, detail="Akun menunggu persetujuan superadmin")
    if (r["status"] or "") == "rejected":
        raise HTTPException(status_code=403, detail="Pendaftaran ditolak superadmin")
    if r["pw"] != hash_pw(payload.password or ""):
        raise HTTPException(status_code=401, detail="password salah")
    return {"nama": r["nama"], "hp": r["hp"], "role": r["role"] or "user",
            "status": r["status"] or "approved"}


@app.put("/api/auth/password")
def change_password(payload: PasswordIn):
    if len(payload.new_password or "") < 4:
        raise HTTPException(status_code=422, detail="password baru min 4")
    with _lock, _conn() as c:
        r = find_user(c, payload.hp)
        if not r:
            raise HTTPException(status_code=404, detail="Akun belum terdaftar")
        if r["pw"] != hash_pw(payload.old_password or ""):
            raise HTTPException(status_code=401, detail="password lama salah")
        c.execute("UPDATE users SET pw=? WHERE hp=?", (hash_pw(payload.new_password), r["hp"]))
        c.commit()
    return {"hp": r["hp"], "updated": True}


def require_superadmin(c: sqlite3.Connection, actor: str):
    a = find_user(c, actor or "")
    if not a or (a["role"] or "") != "superadmin":
        raise HTTPException(status_code=403, detail="khusus superadmin")
    return a


class ApproveIn(BaseModel):
    role: Optional[str] = None


VALID_ROLES = ("admin", "kasir", "karyawan")


@app.get("/api/users")
def list_users(status: str = Query(default=""),
               actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        rows = c.execute(
            "SELECT hp,nama,tgl,role,requested_role,status FROM users ORDER BY tgl DESC").fetchall()
    out = [row_dict(r) for r in rows]
    if status:
        out = [u for u in out if (u.get("status") or "") == status]
    return out


@app.post("/api/users/{hp}/approve")
def approve_user(hp: str, payload: ApproveIn,
                 actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        t = find_user(c, hp)
        if not t:
            raise HTTPException(status_code=404, detail="user tidak ditemukan")
        role = (payload.role or t["requested_role"] or "karyawan").strip().lower()
        if role not in VALID_ROLES and role != "superadmin":
            role = "karyawan"
        c.execute("UPDATE users SET status='approved', role=? WHERE hp=?", (role, t["hp"]))
        c.commit()
    return {"hp": t["hp"], "nama": t["nama"], "role": role, "status": "approved"}


@app.post("/api/users/{hp}/reject")
def reject_user(hp: str, actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        t = find_user(c, hp)
        if not t:
            raise HTTPException(status_code=404, detail="user tidak ditemukan")
        if t["hp"] == "0000000000":
            raise HTTPException(status_code=403, detail="superadmin tidak bisa ditolak")
        c.execute("UPDATE users SET status='rejected' WHERE hp=?", (t["hp"],))
        c.commit()
    return {"hp": t["hp"], "status": "rejected"}


@app.delete("/api/users/{hp}")
def delete_user(hp: str, actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        t = find_user(c, hp)
        if not t:
            raise HTTPException(status_code=404, detail="user tidak ditemukan")
        if t["hp"] == "0000000000":
            raise HTTPException(status_code=403, detail="superadmin tidak bisa dihapus")
        c.execute("DELETE FROM users WHERE hp=?", (t["hp"],))
        c.commit()
    return {"deleted": t["hp"]}


@app.get("/api/settings")
def get_settings(actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        rows = c.execute("SELECT key,value FROM settings").fetchall()
    out = dict(SETTING_DEFAULTS)
    for r in rows:
        if r[0] in SETTING_KEYS:
            out[r[0]] = str(r[1])
    return out


@app.put("/api/settings")
def put_settings(payload: dict, actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        for k, v in (payload or {}).items():
            if k in SETTING_KEYS:
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                          (k, str(v)))
        c.commit()
        rows = c.execute("SELECT key,value FROM settings").fetchall()
    out = dict(SETTING_DEFAULTS)
    for r in rows:
        if r[0] in SETTING_KEYS:
            out[r[0]] = str(r[1])
    return out


@app.get("/api/backup")
def backup_db(actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
    snap = BASE_DIR / f"backup-servis-{date.today().isoformat()}.db"
    with _lock:
        src = _conn()
        dst = sqlite3.connect(str(snap))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    return FileResponse(str(snap), filename=snap.name, media_type="application/x-sqlite3")


@app.post("/api/restore")
async def restore_db(file: UploadFile = File(...),
                     actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
    data = await file.read()
    if len(data) < 100 or not data.startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=400, detail="bukan file SQLite")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file terlalu besar (maks 50MB)")
    tmp = DB_SQLITE.with_suffix(".upload.tmp")
    with _lock:
        tmp.write_bytes(data)
        try:
            chk = sqlite3.connect(str(tmp))
            try:
                tables = {r[0] for r in
                          chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                chk.close()
        except Exception:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="file SQLite rusak")
        if not {"services", "spareparts", "users"} <= tables:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="bukan file DB servis")
        # salin isi ke DB live via backup API (aman di Windows, tanpa replace file)
        src = sqlite3.connect(str(tmp))
        dst = _conn()
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        tmp.unlink(missing_ok=True)
    init_sqlite()
    return health()


@app.delete("/api/data/wipe")
def wipe_data(actor: str = Header(default="", alias="X-Actor-Hp")):
    with _lock, _conn() as c:
        require_superadmin(c, actor)
        ns = c.execute("DELETE FROM services").rowcount
        npart = c.execute("DELETE FROM spareparts").rowcount
        c.commit()
    return {"deleted_services": ns, "deleted_spareparts": npart}


@app.get("/", include_in_schema=False)
def serve_index():
    if INDEX_PATH.exists():
        return FileResponse(str(INDEX_PATH), media_type="text/html")
    return JSONResponse({"detail": "index.html belum ada", "docs": "/docs"})


@app.get("/{fname}", include_in_schema=False)
def serve_static(fname: str):
    if fname in ("script.js", "style.css", "favicon.ico", "manifest.json", "icon.svg"):
        p = BASE_DIR / fname
        if p.exists():
            return FileResponse(str(p))
    return JSONResponse({"detail": "not found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
