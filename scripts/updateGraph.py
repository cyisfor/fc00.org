#!/usr/bin/env python3

# Based on Kyrias' sendGraph script. Requires Python 3, requests and cjdns.
# You can install them using pip: pip3 install cjdns requests
###############################################################################
# CONFIG

# URL where data is sent
#	www.fc00.org			  for clearnet access
#	h.fc00.org				for hyperboria
#	[fc53:dcc5:e89d:9082:4097:6622:5e82:c654] for DNS-less access
url = 'http://www.fc00.org/sendGraph'

# update your email address, so I can contact you in case something goes wrong
your_mail = 'your@email.here'


# ----------------------
# RPC connection details
# ----------------------

# If this is set to True connection details will be loaded from ~/.cjdnsadmin
cjdns_use_default = True

# otherwise these are used.
cjdns_ip	 = '127.0.0.1'
cjdns_port	   = 11234
cjdns_password   = 'NONE'

###############################################################################
import db
from pprint import pprint
import sys
import traceback
import json
import argparse

import requests

import cjdns
from cjdns import key_utils
from cjdns import admin_tools

import queue
from concurrent.futures import ThreadPoolExecutor
import threading

def addpeersto(d,n,ip,peers=set()):
    if n in d:
        d[n]['peers'].update(peers)
    else:
        d[n] = {
                        'ip': ip,
            'peers': set(peers)
        }

def main():
    db.fixkeys(key_utils.to_ipv6)
    parser = argparse.ArgumentParser(description='Submit nodes and links to fc00')
    parser.add_argument('-v', '--verbose', help='increase output verbosity',
                        dest='verbose', action='store_true')
    parser.set_defaults(verbose=False)
    args = parser.parse_args()

    con = connect()

    nodes = dump_node_store(con)
    edges = {}

    get_peer_queue = queue.Queue(0)
    result_queue = queue.Queue(0)
    e = ThreadPoolExecutor(max_workers=4)
    def args():
        for ip,node in nodes.items():
            yield ip,keyFromAddr(node['addr']),node['path'],node['version']
    args = zip(*args())
    dbnodes = {}
    for peers, node_id, ip in e.map(get_peers_derp, *args):
        get_edges_for_peers(edges, peers, node_id)
        addpeersto(dbnodes,node_id,ip,peers)

        for ip, id in peers:
            addpeersto(dbnodes,id,ip)
    print('otay!')
    send_graph(dbnodes, edges)
    sys.exit(0)

local = threading.local()
    
def con():
    try: return local.con
    except AttributeError: pass
    con = connect()
    local.con = con
    return con
    
def get_peers_derp(ip,key,path,version):
    print('check',ip,version)
    ident,peers = db.get_peers(key,version)
    if not peers:
        peers = get_all_peers(con(), path)
        print(('adding peers to db',len(peers)))
        ident,peers = db.set_peers(key,peers,version)
    else:
        print(('got db peers!',len(peers)))
    return peers,ident,ip
def connect():
    try:
        if cjdns_use_default:
            print('Connecting using default or ~/.cjdnsadmin credentials...')
            con = cjdns.connectWithAdminInfo()
        else:
            print('Connecting to port {:d}...'.format(cjdns_port))
            con = cjdns.connect(cjdns_ip, cjdns_port, cjdns_password)

        return con

    except:
        print('Connection failed!')
        print(traceback.format_exc())
        sys.exit(1)


def dump_node_store(con):
    nodes = dict()

    i = 0
    while True:
        res = con.NodeStore_dumpTable(i)

        if not 'routingTable' in res:
            break

        for n in res['routingTable']:
            if not all(key in n for key in ('addr', 'path', 'ip')):
                continue

            ip = n['ip']
            path = n['path']
            addr = n['addr']
            version = None
            if 'version' in n:
                version = n['version']

            nodes[ip] = {'ip': ip, 'path': path, 'addr': addr, 'version': version}

        if not 'more' in res or res['more'] != 1:
            break

        i += 1

    return nodes


def get_peers(con, path, nearbyPath=''):
    formatted_path = path
    if nearbyPath:
        formatted_path = '{:s} (nearby {:s})'.format(path, nearbyPath)

    i = 1
    retry = 2
    while i < retry + 1:
        if nearbyPath:
            res = con.RouterModule_getPeers(path, nearbyPath=nearbyPath)
        else:
            res = con.RouterModule_getPeers(path)

        if res['error'] == 'not_found':
            print('get_peers: node with path {:s} not found, skipping.'
                  .format(formatted_path))
            return []

        elif res['error'] != 'none':
            print('get_peers: failed with error `{:s}` on {:s}, trying again. {:d} tries remaining.'
                  .format(res['error'], formatted_path, retry-i))
        elif res['result'] == 'timeout':
            print('get_peers: timed out on {:s}, trying again. {:d} tries remaining.'
                  .format(formatted_path, retry-i))
        else:
            return res['peers']

        i += 1

    print('get_peers: failed on final try, skipping {:s}'
          .format(formatted_path))
    return []


def get_all_peers(con, path):
    peers = set()
    keys = set()

    res = get_peers(con, path)
    peers.update(res)

    if not res:
        return keys

    last_peer = res[-1]
    checked_paths = set()
    while len(res) > 1:
        last_path = (last_peer.split('.', 1)[1]
                              .rsplit('.', 2)[0])

        if last_path in checked_paths:
            break
        else:
            checked_paths.add(last_path)

        res = get_peers(con, path, last_path)
        if res:
            last_peer = res[-1]
        else:
            break

        peers.update(res)

    for peer in peers:
        key = keyFromAddr(peer)
        keys |= {key}

    return keys

def keyFromAddr(addr):
    return addr.split('.', 5)[-1]

def get_edges_for_peers(edges, peers, node_key):
    for derp in peers:
        try: ip,peer_key = derp
        except:
            pprint(peers)
            raise
        if node_key > peer_key:
            A = node_key
            B = peer_key
        else:
            A = peer_key
            B = node_key

        edge = { 'a': A,
                 'b': B }

        if A not in edges:
            edges[A] = []
        edges[A] = B

def send_graph(nodes, edges):
    print('Nodes: {:d}\nEdges: {:d}\n'.format(len(nodes), len(edges)))

    with open('out.dot','wt') as out:
        out.write('digraph cjdns {\n')
        out.write("  overlap=false;\n");
        for ident,node in nodes.items():
            out.write('  n{} [label="{}"];\n'.format(
                ident,
                node['ip'].rsplit(':',1)[-1]))
        for node,peer in edges.items():
            out.write('  n{} -> n{}\n'.format(
                node,
                peer));
        out.write('}\n')

    graph = {
        'nodes':
                    [],
        'edges': [{'a': nodes[A]['ip'],
                   'b': nodes[B]['ip']} for A,B in edges.items()]
    }
    for ident,node in nodes.items():
        version = db.get_version(ident)
        if version is None:
            continue
        graph['nodes'].append({
            'ip': node['ip'],
          'version': version		
        } )
    json_graph = json.dumps(graph)
    print(json_graph)
    return
    print('Sending data to {:s}...'.format(url))
    
    payload = {'data': json_graph, 'mail': your_mail, 'version': 2}
    r = requests.post(url, data=payload)

    if r.text == 'OK':
        print('Done!')
    else:
        print('{:s}'.format(r.text))

if __name__ == '__main__':
    main()
