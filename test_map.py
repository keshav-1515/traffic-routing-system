import osmnx as ox
import networkx as nx

# Replace with the exact name of your downloaded file in the folder
osm_file = "bengaluru_map.osm"  # or "map.osm"

print(f"Loading {osm_file}...")
G = ox.graph_from_xml(osm_file, simplify=True)

print(f"Successfully loaded!")
print(f"Total Nodes (Intersections): {len(G.nodes)}")
print(f"Total Edges (Roads): {len(G.edges)}")

# Optional: Display the graph visual in a pop-up window
fig, ax = ox.plot_graph(G)