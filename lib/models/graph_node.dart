class GraphNode {
  final String id;
  final double lat;
  final double lng;
  final bool isMetroStation;
  final String name;

  const GraphNode({
    required this.id,
    required this.lat,
    required this.lng,
    this.isMetroStation = false,
    this.name = '',
  });

  factory GraphNode.fromJson(Map<String, dynamic> json) {
    final geometry = json['geometry'] as Map<String, dynamic>? ?? {};
    final properties = json['properties'] as Map<String, dynamic>? ?? {};
    final coords = (geometry['coordinates'] as List?) ?? const [0, 0];
    return GraphNode(
      id: (properties['id'] ?? json['id'] ?? '').toString(),
      lng: coords.isNotEmpty ? (coords[0] as num).toDouble() : 0.0,
      lat: coords.length > 1 ? (coords[1] as num).toDouble() : 0.0,
      isMetroStation: properties['is_metro'] == true,
      name: (properties['name'] ?? '').toString(),
    );
  }
}
