from __future__ import annotations
import json, sqlite3, time
from contextlib import contextmanager
from .config import DATA_DIR, STATE_DB, BALANCE_DB, COLLECTION_DB, USERS_JSON, BOT_DATA_JSON

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters(id TEXT PRIMARY KEY,name TEXT NOT NULL,anime TEXT NOT NULL,rarity TEXT NOT NULL,image TEXT NOT NULL,price INTEGER NOT NULL DEFAULT 0,aliases TEXT NOT NULL DEFAULT '[]',uploader_id INTEGER,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS user_state(user_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,created_at INTEGER NOT NULL DEFAULT 0,last_waifu INTEGER NOT NULL DEFAULT 0,last_marriage INTEGER NOT NULL DEFAULT 0,last_propose INTEGER NOT NULL DEFAULT 0,last_pay INTEGER NOT NULL DEFAULT 0,last_sell INTEGER NOT NULL DEFAULT 0,sell_day TEXT NOT NULL DEFAULT '',sell_count INTEGER NOT NULL DEFAULT 0,dmode TEXT NOT NULL DEFAULT 'default',dmode_value TEXT NOT NULL DEFAULT '',profile_photo TEXT NOT NULL DEFAULT '',profile_waifu TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS groups_state(group_id INTEGER PRIMARY KEY,messages INTEGER NOT NULL DEFAULT 0,interval_seconds INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS active_spawns(group_id INTEGER PRIMARY KEY,character_id TEXT NOT NULL,expires_at INTEGER NOT NULL,message_id INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rarity_switch(rarity TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pending_actions(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payment_log(id TEXT PRIMARY KEY,payer INTEGER NOT NULL,receiver INTEGER NOT NULL,amount INTEGER NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS codes(code TEXT PRIMARY KEY,kind TEXT NOT NULL,reward TEXT NOT NULL,quantity INTEGER NOT NULL,used_by TEXT NOT NULL DEFAULT '[]',creator INTEGER NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS marketplace(user_id INTEGER NOT NULL,slot INTEGER NOT NULL,day TEXT NOT NULL,character_id TEXT NOT NULL,price INTEGER NOT NULL,discount INTEGER NOT NULL,bought INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,slot,day));
CREATE TABLE IF NOT EXISTS marketplace_state(user_id INTEGER PRIMARY KEY,day TEXT NOT NULL,refreshes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS auctions(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id TEXT NOT NULL,starting_bid INTEGER NOT NULL,high_bid INTEGER NOT NULL,high_bidder INTEGER,ends_at INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_by INTEGER NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bids(auction_id INTEGER NOT NULL,user_id INTEGER NOT NULL,amount INTEGER NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(auction_id,user_id));
CREATE TABLE IF NOT EXISTS filters(chat_id INTEGER NOT NULL,name TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(chat_id,name));
CREATE TABLE IF NOT EXISTS stopped_commands(command TEXT PRIMARY KEY,stopped_at INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS claim_events(id INTEGER PRIMARY KEY AUTOINCREMENT,reward_type TEXT NOT NULL,reward_value TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS user_claims(user_id INTEGER NOT NULL,event_id INTEGER NOT NULL,claimed_at INTEGER NOT NULL,PRIMARY KEY(user_id,event_id));
CREATE TABLE IF NOT EXISTS gifts(id INTEGER PRIMARY KEY AUTOINCREMENT,giver INTEGER NOT NULL,receiver INTEGER NOT NULL,character_id TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY,initiator INTEGER NOT NULL,partner INTEGER NOT NULL,offered_id TEXT NOT NULL,wanted_id TEXT NOT NULL,expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,actor INTEGER NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS user_mutes(chat_id INTEGER NOT NULL,user_id INTEGER NOT NULL,until_at INTEGER NOT NULL,PRIMARY KEY(chat_id,user_id));
"""


def _connect(path):
    con=sqlite3.connect(str(path),timeout=30,isolation_level=None)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con

@contextmanager
def db():
    con=_connect(STATE_DB)
    con.execute("ATTACH DATABASE ? AS balance",(str(BALANCE_DB),))
    con.execute("ATTACH DATABASE ? AS collection",(str(COLLECTION_DB),))
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback(); raise
    finally:
        con.close()

def init_db():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    con=_connect(STATE_DB)
    con.executescript(SCHEMA)
    con.close()
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS balance.user_balance(user_id INTEGER PRIMARY KEY,cash INTEGER DEFAULT 0,diamonds INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS collection.collections(user_id INTEGER,username TEXT,waifu_id TEXT,name TEXT,anime TEXT,rarity TEXT,image TEXT,favorite INTEGER DEFAULT 0)")
        _add_column(con,"collection","collections","favorite","INTEGER NOT NULL DEFAULT 0")
        _migrate_legacy(con)

def _add_column(con,schema,table,column,definition):
    cols={r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")}
    if column not in cols: con.execute(f"ALTER TABLE {schema}.{table} ADD COLUMN {column} {definition}")

def _migrate_legacy(con):
    # Import character metadata found in the old collection table without changing source rows.
    rows=con.execute("SELECT waifu_id,name,anime,rarity,image FROM collection.collections WHERE COALESCE(name,'')<>'' AND COALESCE(image,'')<>''").fetchall()
    now=int(time.time())
    for r in rows:
        con.execute("INSERT OR IGNORE INTO characters(id,name,anime,rarity,image,price,created_at) VALUES(?,?,?,?,?,?,?)",(str(r['waifu_id']),r['name'],r['anime'] or '',r['rarity'] or 'Starter',r['image'],0,now))
    if BOT_DATA_JSON.exists():
        try: data=json.loads(BOT_DATA_JSON.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError): data={}
        source=data.get('characters',data.get('waifus',data)) if isinstance(data,dict) else data
        items=source.values() if isinstance(source,dict) else source if isinstance(source,list) else []
        for item in items:
            if not isinstance(item,dict): continue
            cid=str(item.get('id') or item.get('waifu_id') or '')
            name=item.get('name') or item.get('character')
            image=item.get('image') or item.get('file_id') or item.get('photo')
            if cid and name and image:
                rarity=item.get('rarity','Starter'); anime=item.get('anime','')
                con.execute("INSERT OR IGNORE INTO characters(id,name,anime,rarity,image,price,created_at) VALUES(?,?,?,?,?,?,?)",(cid,str(name),str(anime),str(rarity),str(image),int(item.get('price') or 0),now))

def get_character(cid,con=None):
    own=con is None
    if own:
        with db() as con: return get_character(cid,con)
    return con.execute("SELECT * FROM characters WHERE id=?",(str(cid),)).fetchone()

def list_characters(rarities=None,con=None):
    own=con is None
    if own:
        with db() as con: return list_characters(rarities,con)
    if rarities:
        marks=','.join('?' for _ in rarities)
        return con.execute(f"SELECT * FROM characters WHERE rarity IN ({marks}) ORDER BY created_at,id",tuple(rarities)).fetchall()
    return con.execute("SELECT * FROM characters ORDER BY created_at,id").fetchall()

def add_character(data,con=None):
    if con is None:
        with db() as con: return add_character(data,con)
    con.execute("INSERT INTO characters(id,name,anime,rarity,image,price,aliases,uploader_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(str(data['id']),data['name'],data['anime'],data['rarity'],data['image'],int(data['price']),json.dumps(data.get('aliases',[])),data.get('uploader_id'),int(time.time())))

def remove_character(cid):
    with db() as con:
        con.execute("DELETE FROM collection.collections WHERE waifu_id=?",(str(cid),))
        return con.execute("DELETE FROM characters WHERE id=?",(str(cid),)).rowcount

def get_balance(uid,con=None):
    if con is None:
        with db() as con: return get_balance(uid,con)
    con.execute("INSERT OR IGNORE INTO balance.user_balance(user_id,cash,diamonds) VALUES(?,0,0)",(int(uid),))
    r=con.execute("SELECT cash,diamonds FROM balance.user_balance WHERE user_id=?",(int(uid),)).fetchone()
    return int(r['cash']),int(r['diamonds'])

def adjust_balance(uid,cash=0,diamonds=0,con=None):
    if con is None:
        with db() as con: return adjust_balance(uid,cash,diamonds,con)
    old_cash,old_dia=get_balance(uid,con)
    if old_cash+cash<0 or old_dia+diamonds<0: raise ValueError("insufficient balance")
    con.execute("UPDATE balance.user_balance SET cash=?,diamonds=? WHERE user_id=?",(old_cash+int(cash),old_dia+int(diamonds),int(uid)))
    return old_cash+cash,old_dia+diamonds

def add_to_collection(uid,cid,username='',con=None):
    if con is None:
        with db() as con: return add_to_collection(uid,cid,username,con)
    ch=get_character(cid,con)
    if not ch: raise ValueError("character not found")
    con.execute("INSERT INTO collection.collections(user_id,username,waifu_id,name,anime,rarity,image,favorite) VALUES(?,?,?,?,?,?,?,0)",(int(uid),username,str(ch['id']),ch['name'],ch['anime'],ch['rarity'],ch['image']))

def collection_rows(uid,con=None):
    if con is None:
        with db() as con: return collection_rows(uid,con)
    return con.execute("SELECT rowid AS item_rowid,* FROM collection.collections WHERE user_id=? ORDER BY favorite DESC,anime COLLATE NOCASE,name COLLATE NOCASE",(int(uid),)).fetchall()

def remove_from_collection(uid,cid,con=None):
    if con is None:
        with db() as con: return remove_from_collection(uid,cid,con)
    row=con.execute("SELECT rowid FROM collection.collections WHERE user_id=? AND waifu_id=? ORDER BY rowid LIMIT 1",(int(uid),str(cid))).fetchone()
    if not row: return False
    con.execute("DELETE FROM collection.collections WHERE rowid=?",(row['rowid'],)); return True

def set_favorite(uid,cid,con=None):
    if con is None:
        with db() as con: return set_favorite(uid,cid,con)
    row=con.execute("SELECT rowid FROM collection.collections WHERE user_id=? AND waifu_id=? ORDER BY rowid LIMIT 1",(int(uid),str(cid))).fetchone()
    if not row: return False
    con.execute("UPDATE collection.collections SET favorite=0 WHERE user_id=?",(int(uid),))
    con.execute("UPDATE collection.collections SET favorite=1 WHERE rowid=?",(row['rowid'],)); return True

def user_state(uid,username='',first_name='',con=None):
    if con is None:
        with db() as con: return user_state(uid,username,first_name,con)
    con.execute("INSERT OR IGNORE INTO user_state(user_id,username,first_name,created_at) VALUES(?,?,?,?)",(int(uid),username,first_name,int(time.time())))
    con.execute("UPDATE user_state SET username=?,first_name=? WHERE user_id=?",(username,first_name,int(uid)))
    return con.execute("SELECT * FROM user_state WHERE user_id=?",(int(uid),)).fetchone()

def get_setting(key,default='',con=None):
    if con is None:
        with db() as con: return get_setting(key,default,con)
    r=con.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone() if _table_exists(con,'settings') else None
    return r['value'] if r else default

def _table_exists(con,name): return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None

def set_setting(key,value,con=None):
    if con is None:
        with db() as con: return set_setting(key,value,con)
    con.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))

def audit(actor,action,details,con=None):
    if con is None:
        with db() as con: return audit(actor,action,details,con)
    con.execute("INSERT INTO audit_log(actor,action,details,created_at) VALUES(?,?,?,?)",(int(actor),action,str(details),int(time.time())))

init_db()
