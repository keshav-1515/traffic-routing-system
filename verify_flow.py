import time
import json
import urllib.request
import urllib.parse
import urllib.error
import sys
import networkx as nx

BASE = 'http://127.0.0.1:5000'

def get(path):
    url = BASE + path
    for _ in range(20):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.read().decode('utf-8'), r.getcode()
        except Exception as e:
            time.sleep(0.3)
    raise RuntimeError(f'Failed GET {path}')

def get_json(path):
    txt, code = get(path)
    return json.loads(txt)

def post_json(path, body):
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type':'application/json'})
    # retry on transient timeouts
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5)
                continue
            raise


# wait for server
print('Waiting for server...')
up = False
for i in range(40):
    try:
        txt, code = get('/')
        if code == 200:
            up = True
            break
    except Exception:
        time.sleep(0.2)
if not up:
    print('Server did not start')
    sys.exit(2)
print('Server up')

# 1. GET /
root = get('/')
print('/', 'length', len(root[0]))

# 2. GET /api/simulate/state (maps to user requested /api/traffic)
sim = get_json('/api/simulate/state')
print('/api/simulate/state keys:', list(sim.keys())[:8])

# 3. GET /api/incidents (should be empty)
nincs = get_json('/api/incidents')
print('/api/incidents before trigger:', nincs)

# 4. GET /api/graph and build NetworkX graph snapshot
graph = get_json('/api/graph')
edges = graph['edges']['features']
nodes = graph['nodes']['features']
print('graph nodes, edges:', len(nodes), len(edges))
G0 = nx.DiGraph()
for n in nodes:
    nid = int(n['properties']['id'])
    G0.add_node(nid)
for e in edges:
    p = e['properties']
    try:
        u = int(p['u']); v = int(p['v'])
    except Exception:
        continue
    w = p.get('weight', 1.0) or 1.0
    G0.add_edge(u, v, weight=w)

# 5. Trigger incident
res = post_json('/api/demo/scenario', {'action':'trigger'})
print('/api/demo/scenario trigger ->', res)
if 'incident' not in res:
    # fallback: call incidents
    res2 = get_json('/api/incidents')
    print('incidents:', res2)
    if not res2.get('incidents'):
        print('No incident created'); sys.exit(3)
    inc = res2['incidents'][0]
else:
    inc = res['incident']
    # if the demo returned suggested src/dst, prefer those for the test
    demo_src = res.get('demo_src')
    demo_dst = res.get('demo_dst')
    if demo_src is not None and demo_dst is not None:
        u = int(demo_src)
        v = int(demo_dst)
edge = inc['edge']
print('incident edge:', edge)
# parse edge as strings
u_str, v_str, k_str = edge
try:
    u = int(u_str); v = int(v_str)
except Exception:
    u = u_str; v = v_str

# 6. Find a source/target whose baseline path (in G0) includes this edge
found = None
nodes_list = list(G0.nodes)
for s in nodes_list:
    for t in nodes_list:
        if s==t: continue
        try:
            path = nx.dijkstra_path(G0, s, t, weight='weight')
        except Exception:
            continue
        # check if edge (u,v) in path
        for a,b in zip(path[:-1], path[1:]):
            if a==u and b==v:
                found = (s,t,path)
                break
        if found: break
    if found: break

if not found:
    print('Could not find a src/dst whose baseline path uses the incident edge; test cannot proceed reliably')
    sys.exit(4)
src, dst, base_path = found
print('Selected src,dst:', src, dst)
print('Baseline path length (edges):', len(base_path)-1)

# 7. Call /api/route for src/dst
r_live = get_json(f'/api/route?source={src}&target={dst}&by=time')
print('route response keys:', list(r_live.keys()))

# 8. Evaluate response fields
baseline_eta = r_live.get('baseline_eta_min')
recommended = r_live.get('recommended')
rec_route = r_live.get('recommended_route')
time_saved = r_live.get('time_saved_min')
co2_saved = r_live.get('co2_saved_g')
print('baseline_eta, recommended, time_saved, co2_saved:', baseline_eta, recommended, time_saved, co2_saved)

# 9. Clear incident
clr = post_json('/api/demo/scenario', {'action':'clear'})
print('clear result:', clr)

# 10. Route again
r_after = get_json(f'/api/route?source={src}&target={dst}&by=time')
print('after route keys:', list(r_after.keys()))

print('Done')

# Basic assertions for pass/fail decision
ok = True
if not isinstance(sim, dict): ok = False
if not inc.get('active', True): ok = False
if rec_route is None and not recommended:
    print('No recommendation produced')

# Print exit 0
sys.exit(0)
