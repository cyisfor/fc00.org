import sqlite3
import threading
l = threading.local()

cache = os.path.expanduser("~/.cache")

l.conn = sqlite3.Connection(os.path.join(cache,"fc00.sqlite"))

l.conn.execute('CREATE TABLE IF NOT EXISTS versions (latest INTEGER PRIMARY KEY)')
with l.conn,closing(conn.cursor()) as c:
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
            with closing(l.conn.cursor()) as c:
                c.execute('UPDATE versions SET latest = ?',(n,))
    return deco

@version(1)
def _():
    with closing(l.conn.cursor()) as c:
        c.execute('CREATE TABLE nodes (id INTEGER PRIMARY KEY, ip TEXT UNIQUE, checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL')
        c.execute('CREATE INDEX byChecked ON nodes(checked)')
        c.execute('CREATE TABLE links (id INTEGER PRIMARY KEY, red INTEGER REFERENCES nodes(id), blue INTEGER REFERENCES nodes(id), UNIQUE(red,blue))')

def get_peers(ip):
    with closing(l.conn.cursor()) as c:
        ident = peer2node(ip,c)
        c.execute("SELECT checked < datetime('now','-1 day') FROM nodes WHERE id = ?",(ident,))
        ok = c.fetchone()
        if not ok or not ok[0]:
            return ()
        c.execute("""SELECT ip FROM nodes
WHERE id IN (
  SELECT blue FROM links WHERE red = (
    SELECT id FROM nodes WHERE id = ?))
""",(ident,))
        return [row[0] for row in c.fetchall()]

def set_peers(ip,peers):
    with l.conn, closing(l.conn.cursor()) as c:
        peers = [peer2node(peer,c) for peer in peers]
        ident = peer2node(ip,c)
            for p in peers:
                c.execute('INSERT OR REPLACE INTO links (red,blue) VALUES (?,?)',
                          (ident,p))

def peer2node(ip,c):
    c.execute('SELECT id FROM nodes WHERE ip = ?',(ip,))
    ident = c.fetchone()
    if ident:
        return ident[0]
    c.execute('INSERT INTO nodes (ip)',(ip,))
