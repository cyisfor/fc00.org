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

@version(2)
def _():
    with closing(l.conn.cursor()) as c:
        c.execute("ALTER TABLE nodes ADD COLUMN ip TEXT");

key2ip = None

def redoLinks(c):
    c.execute('ALTER TABLE links RENAME TO oldlinks')
    c.execute('''CREATE TABLE links (
id INTEGER PRIMARY KEY, 
red INTEGER REFERENCES nodes(id) NOT NULL, 
blue INTEGER REFERENCES nodes(id) NOT NULL, 
UNIQUE(red,blue))
                ''')
    c.execute('INSERT INTO links SELECT id,red,blue FROM oldlinks')
    c.execute('DROP TABLE oldlinks')

def fixkeys(derp):
    global key2ip
    key2ip = derp
    @version(3)
    def _():
        conn()
        l.conn.create_function("key2ip", 1, key2ip)
        with closing(l.conn.cursor()) as c:
            c.execute('ALTER TABLE nodes RENAME TO oldnodes')
            c.execute('''CREATE TABLE nodes (
id INTEGER PRIMARY KEY, 
key TEXT NOT NULL UNIQUE, 
ip TEXT NOT NULL,
checked TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('INSERT INTO nodes SELECT id,key,key2ip(key) as ip,checked FROM oldnodes')
            redoLinks(c)
            c.execute('DROP TABLE oldnodes')
            c.execute('VACUUM ANALYZE')

@version(4)
def _():
    l.conn.execute("ALTER TABLE nodes ADD COLUMN lastVersion INT")

@version(5)
def _():
    with closing(l.conn.cursor()) as c:	
        c.execute('ALTER TABLE nodes RENAME TO oldnodes')
        c.execute('''CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    ip TEXT NOT NULL,
  lastVersion INTEGER,
checked TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        #c.execute('DROP INDEX byChecked;')
        c.execute('CREATE INDEX byChecked ON nodes(checked)')
        c.execute('INSERT INTO nodes (id,key,checked) SELECT id,key,checked FROM oldnodes')
        redoLinks(c)
        c.execute('DROP TABLE oldnodes')
        
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

def get_version(ident):
    with conn(),closing(l.conn.cursor()) as c:
        c.execute('SELECT lastVersion FROM nodes WHERE id = ?',(ident,))
        row = c.fetchone()
        if row: return row[0]

@retry_on_locked(1)
def get_peers(key,lastVersion):
    with conn(),closing(l.conn.cursor()) as c:
        ident = peer2node(key,c,lastVersion)
        c.execute("SELECT checked > datetime('now','-1 hour') FROM nodes WHERE id = ?",(ident,))
        ok = c.fetchone()
        if not ok or not ok[0]:
            return ident,()
        c.execute("SELECT (SELECT ip FROM nodes WHERE id = blue),blue FROM links WHERE red = ?",(ident,))
        return ident,c.fetchall()
    
@retry_on_locked(1)
def set_peers(key,peers,lastVersion):
    with conn(), closing(l.conn.cursor()) as c:
        # getPeers doesn't return the peer's versions, only their key.
        peers = [peer2node(peer,c) for peer in peers]
        ident = peer2node(key,c,lastVersion)
        peers = [peer for peer in peers if peer != ident]
        for p in peers:
            c.execute('INSERT OR REPLACE INTO links (red,blue) VALUES (?,?)',
                      (ident,p))
        c.execute("UPDATE nodes SET checked = datetime('now') WHERE id = ?",(ident,))
    return get_peers(key,lastVersion)

def peer2node(key,c,lastVersion=None):
    c.execute('SELECT id FROM nodes WHERE key = ?',(key,))
    ident = c.fetchone()
    if ident:
        ident = ident[0]
        if lastVersion:
            c.execute('SELECT lastVersion FROM nodes WHERE id = ?',
                      (ident,))
            test = c.fetchone()
            if test and test[0] is not None and test[0] != lastVersion:
                c.execute('UPDATE nodes SET lastVersion = ? WHERE id = ?',
                          (lastVersion,ident))
        return ident
    c.execute('INSERT INTO nodes (key,ip,lastVersion) VALUES (?,?,?)',
              (key,key2ip(key),lastVersion))
    return c.lastrowid
