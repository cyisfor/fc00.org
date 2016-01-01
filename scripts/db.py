import sqlite3,os,time

from contextlib import closing
import threading
l = threading.local()

cache = os.path.expanduser("~/.cache")

def conn():
    try: return l.conn
    except AttributeError: pass
    l.conn = sqlite3.Connection(os.path.join(cache,"fc00.sqlite"))
    return l.conn
conn()
l.conn.execute('CREATE TABLE IF NOT EXISTS versions (latest INTEGER PRIMARY KEY)')
with l.conn,closing(l.conn.cursor()) as c:
    c.execute('SELECT latest FROM versions')
    latest = c.fetchone()
    if latest:
        latest = latest[0]
    else:
        latest = 0
        c.execute('INSERT INTO versions (latest) VALUES (0)')

def version(n):
    def deco(f):
        if n > latest:
            f()
            with l.conn,closing(l.conn.cursor()) as c:
                c.execute('UPDATE versions SET latest = ?',(n,))
    return deco

@version(1)
def _():
    with closing(l.conn.cursor()) as c:
        c.execute('CREATE TABLE nodes (id INTEGER PRIMARY KEY, key TEXT UNIQUE, checked TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        c.execute('CREATE INDEX byChecked ON nodes(checked)')
        c.execute('CREATE TABLE links (id INTEGER PRIMARY KEY, red INTEGER REFERENCES nodes(id) NOT NULL, blue INTEGER REFERENCES nodes(id) NOT NULL, UNIQUE(red,blue))')
        
def retry_on_locked(s):
    def deco(f):
        def wrapper(*a,**kw):
            while True:
                try:
                    return f(*a,**kw)
                except sqlite3.OperationalError as e:
                    if e.error_code != 5:
                        raise
                    print(e.args)
                    time.sleep(s)
        return wrapper
    return deco
        
@retry_on_locked(1)
def get_peers(key):
    with conn(),closing(l.conn.cursor()) as c:
        ident = peer2node(key,c)
        c.execute("SELECT checked > datetime('now','-1 day') FROM nodes WHERE id = ?",(ident,))
        ok = c.fetchone()
        if not ok or not ok[0]:
            return ()
        c.execute("""SELECT key FROM nodes
WHERE id IN (
  SELECT blue FROM links WHERE red = (
    SELECT id FROM nodes WHERE id = ?))
""",(ident,))
        return [row[0] for row in c.fetchall()]
    
@retry_on_locked(1)
def set_peers(key,peers):
    with conn(), closing(l.conn.cursor()) as c:
        peers = [peer2node(peer,c) for peer in peers]
        ident = peer2node(key,c)
        for p in peers:
            c.execute('INSERT OR REPLACE INTO links (red,blue) VALUES (?,?)',
                      (ident,p))
        c.execute("UPDATE nodes SET checked = datetime('now') WHERE id = ?",(ident,))

def peer2node(key,c):
    c.execute('SELECT id FROM nodes WHERE key = ?',(key,))
    ident = c.fetchone()
    if ident:
        return ident[0]
    c.execute('INSERT INTO nodes (key) VALUES (?)',(key,))
    return c.lastrowid
