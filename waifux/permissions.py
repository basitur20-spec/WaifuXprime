from __future__ import annotations
import json
from pathlib import Path
from .config import OWNER_ID,SUDO_USERS,UPLOADER_USERS,DATA_DIR
from .database import db

def ids_from_file(name):
    path=DATA_DIR.parent/name
    if not path.exists(): path=DATA_DIR/name
    try:
        raw=json.loads(path.read_text(encoding='utf-8'))
        return {int(x) for x in raw if str(x).isdigit()} if isinstance(raw,list) else set()
    except (OSError,json.JSONDecodeError,TypeError,ValueError): return set()

def role_ids(role):
    role=role.lower()
    base={'owner':{OWNER_ID},'sudo':set(SUDO_USERS)|ids_from_file('sudo_users.json'),'uploader':set(UPLOADER_USERS)|ids_from_file('uploader_users.json')}.get(role,set())
    try:
        with db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS roles(user_id INTEGER NOT NULL,role TEXT NOT NULL,PRIMARY KEY(user_id,role))')
            base.update(int(r['user_id']) for r in con.execute('SELECT user_id FROM roles WHERE role=?',(role,)))
    except Exception: pass
    return base

def has_role(uid,*roles): return any(int(uid) in role_ids(r) for r in roles)
def is_owner(uid): return int(uid)==OWNER_ID
