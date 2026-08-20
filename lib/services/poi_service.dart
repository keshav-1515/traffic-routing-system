import 'dart:convert';
import 'dart:math' as math;

import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import '../models/poi_model.dart';

class PoiService {
  static const _endpoints = <String>[
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
  ];

  Future<List<MapPoi>> fetchNearby(
    LatLng center, {
    double radiusMeters = 6000,
    Set<PoiType> types = const {
      PoiType.metro,
      PoiType.school,
      PoiType.hospital,
    },
  }) async {
    if (types.isEmpty) return [];

    final filters = <String>[];
    if (types.contains(PoiType.metro)) {
      filters.add('node["railway"="subway"](around:$radiusMeters,${center.latitude},${center.longitude});');
      filters.add('node["public_transport"="station"]["station"="subway"](around:$radiusMeters,${center.latitude},${center.longitude});');
    }
    if (types.contains(PoiType.school)) {
      filters.add('node["amenity"="school"](around:$radiusMeters,${center.latitude},${center.longitude});');
      filters.add('way["amenity"="school"](around:$radiusMeters,${center.latitude},${center.longitude});');
    }
    if (types.contains(PoiType.hospital)) {
      filters.add('node["amenity"="hospital"](around:$radiusMeters,${center.latitude},${center.longitude});');
      filters.add('way["amenity"="hospital"](around:$radiusMeters,${center.latitude},${center.longitude});');
    }

    final query = '[out:json][timeout:20];(${filters.join()});out center tags;';

    for (final endpoint in _endpoints) {
      try {
        final response = await http.post(
          Uri.parse(endpoint),
          headers: const {'Content-Type': 'application/x-www-form-urlencoded'},
          body: {'data': query},
        ).timeout(const Duration(seconds: 7));
        if (response.statusCode != 200) continue;

        final decoded = jsonDecode(response.body) as Map<String, dynamic>;
        final raw = (decoded['elements'] as List?) ?? const [];
        final pois = <MapPoi>[];
        for (final item in raw.whereType<Map>()) {
          try {
            pois.add(MapPoi.fromOverpass(Map<String, dynamic>.from(item)));
          } catch (_) {
            // Ignore malformed OSM elements instead of failing the whole map.
          }
        }
        return _dedupeAndSort(pois, center);
      } catch (_) {
        // Try the second public Overpass instance.
      }
    }
    return [];
  }

  List<MapPoi> _dedupeAndSort(List<MapPoi> input, LatLng center) {
    final seen = <String>{};
    final result = <MapPoi>[];
    for (final poi in input) {
      final key =
          '${poi.name.toLowerCase()}-${poi.location.latitude.toStringAsFixed(4)}-${poi.location.longitude.toStringAsFixed(4)}';
      if (seen.add(key)) result.add(poi);
    }
    result.sort((a, b) =>
        _distanceMeters(center, a.location).compareTo(_distanceMeters(center, b.location)));
    return result.take(120).toList();
  }

  double _distanceMeters(LatLng a, LatLng b) {
    const earthRadius = 6371000.0;
    final lat1 = a.latitude * math.pi / 180.0;
    final lat2 = b.latitude * math.pi / 180.0;
    final dLat = (b.latitude - a.latitude) * math.pi / 180.0;
    final dLon = (b.longitude - a.longitude) * math.pi / 180.0;
    final h = math.sin(dLat / 2) * math.sin(dLat / 2) +
        math.cos(lat1) *
            math.cos(lat2) *
            math.sin(dLon / 2) *
            math.sin(dLon / 2);
    return earthRadius *
        2 *
        math.atan2(math.sqrt(h.clamp(0.0, 1.0)),
            math.sqrt((1 - h).clamp(0.0, 1.0)));
  }
}
