import os
import pickle
import sys
import types

import networkx as nx

import virtual_world

if 'osmnx' not in sys.modules:
    osmnx_stub = types.ModuleType('osmnx')
    osmnx_stub.settings = types.SimpleNamespace(use_cache=False, cache_folder=None)
    osmnx_stub.config = lambda **kwargs: None
    osmnx_stub.graph_from_place = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('graph_from_place should not be called when cached graph exists'))
    osmnx_stub.graph_from_address = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('graph_from_address should not be called when cached graph exists'))

    def _save_graphml(G, filepath):
        with open(filepath, 'wb') as fh:
            pickle.dump(G, fh)

    def _load_graphml(filepath):
        with open(filepath, 'rb') as fh:
            return pickle.load(fh)

    osmnx_stub.load_graphml = _load_graphml
    osmnx_stub.save_graphml = _save_graphml
    sys.modules['osmnx'] = osmnx_stub


class _DummyMonkeyPatch:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


def test_prepare_osm_graph_adds_required_traffic_attributes():
    G = nx.MultiDiGraph()
    G.add_node('n1', x=77.5946, y=12.9716)
    G.add_node('n2', x=77.5950, y=12.9720)
    G.add_edge('n1', 'n2', highway='residential', length=120.0, oneway=False)
    G.add_edge('n2', 'n1', highway='residential', length=120.0, oneway=False)

    prepared = virtual_world.prepare_graph_for_routing(G, source_name='OSM test')

    assert prepared is G
    assert 'x' in G.nodes['n1'] and 'y' in G.nodes['n1']
    data = G['n1']['n2'][0]
    assert data['length'] > 0
    assert 'free_flow_speed_kmh' in data
    assert 'free_flow_time_min' in data
    assert 'capacity_veh_per_hr' in data
    assert 'congestion_score' in data
    assert 'weight' in data
    assert data['incident'] is False


def test_init_graph_uses_mock_online_graph():
    G = nx.MultiDiGraph()
    G.add_node(1, x=77.5946, y=12.9716)
    G.add_node(2, x=77.5950, y=12.9720)
    G.add_edge(1, 2, highway='residential', length=120.0)
    G.add_edge(2, 1, highway='residential', length=120.0)

    mp = _DummyMonkeyPatch()
    mp.setattr(virtual_world, 'build_graph_from_place', lambda *args, **kwargs: G)
    mp.setattr(virtual_world, 'build_graph_from_bbox', lambda *args, **kwargs: G)

    virtual_world.init_graph(place='mock-place', autostart=False)

    assert virtual_world.USING_SYNTHETIC is False
    assert virtual_world.ROAD_GRAPH is G
    assert virtual_world.SIM is not None


def test_init_graph_falls_back_to_synthetic_when_osm_load_fails():
    def boom(*args, **kwargs):
        raise RuntimeError('offline')

    mp = _DummyMonkeyPatch()
    mp.setattr(virtual_world, 'build_graph_from_place', boom)
    mp.setattr(virtual_world, 'build_graph_from_bbox', boom)

    virtual_world.init_graph(place='bad-place', autostart=False)

    assert virtual_world.USING_SYNTHETIC is True
    assert virtual_world.ROAD_GRAPH is not None
    assert virtual_world.ROAD_GRAPH.number_of_nodes() > 0
    assert virtual_world.ROAD_GRAPH.number_of_edges() > 0


def test_build_graph_from_place_uses_cached_osm_graph_first():
    import osmnx as ox

    G = nx.MultiDiGraph()
    G.add_node('n1', x=77.5946, y=12.9716)
    G.add_node('n2', x=77.5950, y=12.9720)
    G.add_edge('n1', 'n2', highway='residential', length=120.0, oneway=False)
    G.add_edge('n2', 'n1', highway='residential', length=120.0, oneway=False)

    cache_dir = os.path.join(os.path.dirname(virtual_world.__file__), 'cache', 'osmnx')
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, 'koramangala_osm.graphml')
    if os.path.exists(cache_path):
        os.remove(cache_path)
    ox.save_graphml(G, filepath=cache_path)

    def boom(*args, **kwargs):
        raise RuntimeError('network should not be used when cached graph exists')

    mp = _DummyMonkeyPatch()
    mp.setattr(ox, 'graph_from_place', boom)
    mp.setattr(ox, 'graph_from_address', boom)

    loaded = virtual_world.build_graph_from_place('cached test place')

    assert loaded is not None
    assert loaded.number_of_nodes() == 2
    assert loaded.number_of_edges() >= 2
    assert loaded.graph.get('source') == 'OSM'
    assert loaded.graph.get('crs') == 'epsg:4326'
    assert loaded.number_of_nodes() > 1


def test_invalid_cached_graphml_is_deleted_and_rebuilt():
    cache_path = virtual_world.DEFAULT_OSM_GRAPH_CACHE
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as fh:
        fh.write('this is not a valid graphml file')

    G = nx.MultiDiGraph()
    G.add_node('a', x=77.5946, y=12.9716)
    G.add_node('b', x=77.5950, y=12.9720)
    G.add_edge('a', 'b', highway='residential', length=120.0, oneway=False)
    G.add_edge('b', 'a', highway='residential', length=120.0, oneway=False)

    import osmnx as ox
    saved = virtual_world._save_cached_osm_graph(G, cache_path)

    assert saved is not None
    assert os.path.exists(cache_path)
    loaded = virtual_world._load_cached_osm_graph(cache_path)
    assert loaded is not None
    assert loaded.number_of_nodes() == 2
    assert loaded.graph.get('source') == 'OSM'
