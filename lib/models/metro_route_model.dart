import 'package:latlong2/latlong.dart';

class MetroRoutePlan {
  final String originStationName;
  final LatLng originStation;
  final String destinationStationName;
  final LatLng destinationStation;
  final List<LatLng> walkToStation;
  final List<LatLng> metroLeg;
  final List<LatLng> walkFromStation;
  final double walkToStationMeters;
  final double metroMeters;
  final double walkFromStationMeters;
  final double totalMeters;
  final double walkToMinutes;
  final double metroMinutes;
  final double walkFromMinutes;
  final double totalMinutes;
  final int credits;

  const MetroRoutePlan({
    required this.originStationName,
    required this.originStation,
    required this.destinationStationName,
    required this.destinationStation,
    required this.walkToStation,
    required this.metroLeg,
    required this.walkFromStation,
    required this.walkToStationMeters,
    required this.metroMeters,
    required this.walkFromStationMeters,
    required this.totalMeters,
    required this.walkToMinutes,
    required this.metroMinutes,
    required this.walkFromMinutes,
    required this.totalMinutes,
    required this.credits,
  });
}
