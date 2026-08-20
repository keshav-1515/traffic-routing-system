import 'package:latlong2/latlong.dart';

enum PoiType { metro, school, hospital }

class MapPoi {
  final String id;
  final String name;
  final LatLng location;
  final PoiType type;
  final String? network;
  final String? operator;

  const MapPoi({
    required this.id,
    required this.name,
    required this.location,
    required this.type,
    this.network,
    this.operator,
  });

  factory MapPoi.fromOverpass(Map<String, dynamic> element) {
    final tags = (element['tags'] as Map?)?.cast<String, dynamic>() ?? {};
    final lat = (element['lat'] ?? (element['center'] as Map?)?['lat']) as num;
    final lon = (element['lon'] ?? (element['center'] as Map?)?['lon']) as num;
    final id = '${element['type']}-${element['id']}';
    final railway = '${tags['railway'] ?? ''}';
    final transit = '${tags['public_transport'] ?? ''}';
    final station = '${tags['station'] ?? ''}';
    final amenity = '${tags['amenity'] ?? ''}';
    final healthcare = '${tags['healthcare'] ?? ''}';

    PoiType type;
    if (railway == 'subway' || (transit == 'station' && station == 'subway')) {
      type = PoiType.metro;
    } else if (amenity == 'school' || tags['school'] != null) {
      type = PoiType.school;
    } else {
      type = PoiType.hospital;
      if (healthcare != 'hospital' && amenity != 'hospital') {
        type = PoiType.hospital;
      }
    }

    return MapPoi(
      id: id,
      name: '${tags['name'] ?? _fallbackName(type)}'.trim(),
      location: LatLng(lat.toDouble(), lon.toDouble()),
      type: type,
      network: tags['network']?.toString(),
      operator: tags['operator']?.toString(),
    );
  }

  static String _fallbackName(PoiType type) {
    switch (type) {
      case PoiType.metro:
        return 'Metro station';
      case PoiType.school:
        return 'School';
      case PoiType.hospital:
        return 'Hospital';
    }
  }
}
