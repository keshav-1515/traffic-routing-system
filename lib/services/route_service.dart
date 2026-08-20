import '../models/graph_node.dart';

class RouteService {
  GraphNode? nearestNode(List<GraphNode> nodes, double lat, double lng) {
    if (nodes.isEmpty) return null;
    GraphNode best = nodes.first;
    double bestDistance = double.infinity;
    for (final node in nodes) {
      final d = (node.lat-lat)*(node.lat-lat)+(node.lng-lng)*(node.lng-lng);
      if (d < bestDistance) { bestDistance=d; best=node; }
    }
    return best;
  }
}
