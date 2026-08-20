import 'package:latlong2/latlong.dart';

class RoadSegment {
  final String id;
  final List<LatLng> points;
  final double congestion;
  final double lengthMeters;
  final double capacity;
  final double freeFlowTimeMinutes;
  final double currentSpeedKmh;
  final String roadType;

  const RoadSegment({
    required this.id,
    required this.points,
    required this.congestion,
    required this.lengthMeters,
    required this.capacity,
    required this.freeFlowTimeMinutes,
    required this.currentSpeedKmh,
    required this.roadType,
  });

  factory RoadSegment.fromJson(Map<String, dynamic> json) {
    final p = (json['properties'] as Map<String, dynamic>?) ?? {};
    final geometry = (json['geometry'] as Map<String, dynamic>?) ?? {};
    final coords = (geometry['coordinates'] as List?) ?? const [];
    final points = coords.whereType<List>().where((c) => c.length >= 2).map(
      (c) => LatLng((c[1] as num).toDouble(), (c[0] as num).toDouble()),
    ).toList();
    final congestion = ((p['congestion_score'] ?? 0) as num).toDouble().clamp(0.0, 1.0).toDouble();
    final ff = ((p['free_flow_speed_kmh'] ?? 30) as num).toDouble();
    return RoadSegment(
      id: '${p['u'] ?? ''}-${p['v'] ?? ''}',
      points: points,
      congestion: congestion,
      lengthMeters: ((p['length_m'] ?? 0) as num).toDouble(),
      capacity: ((p['capacity_veh_per_hr'] ?? 0) as num).toDouble(),
      freeFlowTimeMinutes: ((p['free_flow_time_min'] ?? 0) as num).toDouble(),
      currentSpeedKmh: ff * (1 - congestion * .65),
      roadType: (p['highway'] ?? 'road').toString(),
    );
  }
}

class TrafficPrediction {
  final String roadId;
  final double currentCongestion;
  final double predictedCongestion;
  final int horizonMinutes;
  final bool isFallback;

  const TrafficPrediction({
    required this.roadId,
    required this.currentCongestion,
    required this.predictedCongestion,
    required this.horizonMinutes,
    this.isFallback = false,
  });

  factory TrafficPrediction.fromJson(Map<String, dynamic> json) {
    return TrafficPrediction(
      roadId: (json['road_id'] ?? '').toString(),
      currentCongestion: ((json['current_congestion'] ?? 0) as num).toDouble(),
      predictedCongestion: ((json['predicted_congestion'] ?? 0) as num).toDouble(),
      horizonMinutes: ((json['horizon_minutes'] ?? 15) as num).toInt(),
      isFallback: json['fallback'] == true,
    );
  }
}

class TrafficRoute {
  final List<String> pathNodes;
  final double distanceMeters;
  final double timeMinutes;
  final List<LatLng> coordinates;
  final double congestion;
  final double predictedCongestion;
  final double co2SavedKg;
  final bool isEcoRoute;
  final bool isFallback;

  const TrafficRoute({
    required this.pathNodes,
    required this.distanceMeters,
    required this.timeMinutes,
    required this.coordinates,
    this.congestion = 0,
    this.predictedCongestion = 0,
    this.co2SavedKg = 0,
    this.isEcoRoute = false,
    this.isFallback = false,
  });

  factory TrafficRoute.fromJson(Map<String, dynamic> json, {bool isEco = false}) {
    final geometry = json['geometry'] as Map<String, dynamic>?;
    final coordsJson = (geometry?['coordinates'] as List?) ?? (json['route'] as List?) ?? const [];
    final points = coordsJson.whereType<List>().where((c) => c.length >= 2).map(
      (c) => LatLng((c[1] as num).toDouble(), (c[0] as num).toDouble()),
    ).toList();
    final dist = ((json['distance_m'] ?? ((json['distance_km'] ?? 0) as num) * 1000) as num).toDouble();
    return TrafficRoute(
      pathNodes: List<String>.from((json['path'] as List?)?.map((e) => e.toString()) ?? const []),
      distanceMeters: dist,
      timeMinutes: ((json['time_min'] ?? json['duration_min'] ?? 0) as num).toDouble(),
      coordinates: points,
      congestion: ((json['congestion'] ?? 0) as num).toDouble(),
      predictedCongestion: ((json['predicted_congestion'] ?? 0) as num).toDouble(),
      co2SavedKg: ((json['carbon_saved_kg'] ?? json['co2_saved_kg'] ?? 0) as num).toDouble(),
      isEcoRoute: isEco || json['is_eco'] == true,
      isFallback: json['fallback'] == true,
    );
  }
}
