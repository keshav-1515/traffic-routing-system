import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';

import '../models/graph_node.dart';
import '../models/metro_route_model.dart';
import '../models/poi_model.dart';
import '../models/route_model.dart';
import 'eco_credits_provider.dart';
import '../services/api_service.dart';
import '../services/poi_service.dart';

class TrafficProvider extends ChangeNotifier {
  final ApiService _apiService = ApiService();
  final EcoCreditsProvider? _creditsProvider;
  final PoiService _poiService = PoiService();
  StreamSubscription<Position>? _positionSubscription;

  List<GraphNode> _nodes = [];
  List<RoadSegment> _roads = [];
  List<MapPoi> _pois = [];
  String? _startNodeId;
  String? _endNodeId;
  String _startText = '';
  String _endText = '';
  LatLng? _startLocation;
  LatLng? _endLocation;
  TrafficRoute? _currentRoute;
  MetroRoutePlan? _metroPlan;
  TrafficPrediction? _prediction;
  Position? _currentPosition;
  Position? _previousPosition;
  bool _isLoading = false;
  bool _isEcoSaverActive = false;
  bool _demoMode = false;
  bool _isJourneyActive = false;
  bool _hasActualMovement = false;
  bool _metroBoardReady = false;
  bool _metroBoarded = false;
  bool _metroArrivalReached = false;
  bool _metroCreditsAwarded = false;
  bool _tripRecorded = false;
  String _travelMode = 'car';
  String? _error;

  List<GraphNode> get nodes => _nodes;
  List<RoadSegment> get roads => _roads;
  List<MapPoi> get pois => List.unmodifiable(_pois);
  String? get startNodeId => _startNodeId;
  String? get endNodeId => _endNodeId;
  String get startText => _startText;
  String get endText => _endText;
  LatLng? get startLocation => _startLocation;
  LatLng? get endLocation => _endLocation;
  TrafficRoute? get currentRoute => _currentRoute;
  MetroRoutePlan? get metroPlan => _metroPlan;
  TrafficPrediction? get prediction => _prediction;
  Position? get currentPosition => _currentPosition;
  bool get isLoading => _isLoading;
  bool get isEcoSaverActive => _isEcoSaverActive;
  bool get demoMode => _demoMode;
  bool get isJourneyActive => _isJourneyActive;
  bool get hasActualMovement => _hasActualMovement;
  bool get metroBoardReady => _metroBoardReady;
  bool get metroBoarded => _metroBoarded;
  bool get metroArrivalReached => _metroArrivalReached;
  bool get metroCreditsAwarded => _metroCreditsAwarded;
  String get travelMode => _travelMode;
  String? get error => _error;

  double get liveCongestion => _currentRoute?.congestion ?? _averageCongestion;
  double get predictedCongestion =>
      _currentRoute?.predictedCongestion ?? _prediction?.predictedCongestion ?? liveCongestion;
  double get _averageCongestion => _roads.isEmpty
      ? .63
      : _roads.map((r) => r.congestion).reduce((a, b) => a + b) / _roads.length;

  TrafficProvider([this._creditsProvider]) {
    loadGraph();
  }

  @override
  void dispose() {
    _positionSubscription?.cancel();
    super.dispose();
  }

  Future<void> loadGraph() async {
    _setLoading(true);
    _error = null;
    final result = await _apiService.fetchGraph();
    _nodes = result.nodes;
    _roads = result.roads;
    _demoMode = result.demoMode;
    _setLoading(false);
  }

  Future<bool> locateMe({bool useAsStart = true}) async {
    _error = null;
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        _error = 'Location services are disabled.';
        notifyListeners();
        return false;
      }
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied || permission == LocationPermission.deniedForever) {
        _error = 'Location permission was not granted.';
        notifyListeners();
        return false;
      }
      _currentPosition = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      );
      if (useAsStart) {
        _startLocation = LatLng(_currentPosition!.latitude, _currentPosition!.longitude);
        _startText = 'Current location';
        _startNodeId = null;
        await loadNearbyPois(_startLocation!);
      }
      notifyListeners();
      return true;
    } catch (_) {
      _error = 'Unable to obtain your current location.';
      notifyListeners();
      return false;
    }
  }

  void setStartText(String value) {
    _startText = value;
    _startNodeId = null;
    _startLocation = null;
    _clearRoutes();
    notifyListeners();
  }

  void setEndPlace(String name, LatLng location) {
    _endText = name;
    _endLocation = location;
    _endNodeId = null;
    _clearRoutes();
    notifyListeners();
  }

  void setEndText(String value) {
    _endText = value;
    _endNodeId = null;
    _endLocation = null;
    _clearRoutes();
    notifyListeners();
  }

  void setStartNode(String? id) {
    _startNodeId = id;
    _startLocation = _nodeToLatLng(id);
    _startText = _nodeName(id);
    _clearRoutes();
    notifyListeners();
  }

  void setEndNode(String? id) {
    _endNodeId = id;
    _endLocation = _nodeToLatLng(id);
    _endText = _nodeName(id);
    _clearRoutes();
    notifyListeners();
  }

  void swapLocations() {
    final text = _startText;
    _startText = _endText;
    _endText = text;
    final loc = _startLocation;
    _startLocation = _endLocation;
    _endLocation = loc;
    final node = _startNodeId;
    _startNodeId = _endNodeId;
    _endNodeId = node;
    _clearRoutes();
    notifyListeners();
  }

  Future<void> setTravelMode(String mode) async {
    if (mode != 'car' && mode != 'walking') return;
    if (_isJourneyActive || _travelMode == mode) return;
    _travelMode = mode;
    notifyListeners();
    if (_startLocation != null && _endLocation != null) await calculateRoute();
  }

  Future<void> toggleEcoSaver(bool enabled) async {
    _isEcoSaverActive = enabled;
    _metroPlan = null;
    _metroBoardReady = false;
    _metroBoarded = false;
    _metroArrivalReached = false;
    _metroCreditsAwarded = false;
    notifyListeners();
    if (_startText.trim().isNotEmpty && _endText.trim().isNotEmpty) await calculateRoute();
  }

  Future<void> calculateRoute() async {
    _error = null;
    if (_startText.trim().isEmpty || _endText.trim().isEmpty) {
      _error = 'Enter both a start and destination.';
      notifyListeners();
      return;
    }

    _setLoading(true);

    // Resolve both endpoints in parallel. Do not make routing wait for POI
    // discovery: place/POI APIs are auxiliary data, not route prerequisites.
    final geocodes = await Future.wait([
      _startLocation == null ? _apiService.geocode(_startText) : Future.value(_startLocation),
      _endLocation == null ? _apiService.geocode(_endText) : Future.value(_endLocation),
    ]);
    _startLocation ??= geocodes[0];
    _endLocation ??= geocodes[1];

    if (_startLocation == null) {
      _error = 'Could not find the start location.';
      _setLoading(false);
      return;
    }
    if (_endLocation == null) {
      _error = 'Could not find the destination.';
      _setLoading(false);
      return;
    }

    // Routing is the critical path. Start it immediately; POI discovery is
    // intentionally non-blocking so the route appears as soon as the router
    // responds.
    final standardRouteFuture = _apiService.fetchMapRoute(
      _startLocation!,
      _endLocation!,
      mode: _travelMode,
    );

    final poiFuture = _isEcoSaverActive
        ? Future.wait([
            _apiService.fetchNearbyPois(_startLocation!, radiusMeters: 6000),
            _apiService.fetchNearbyPois(_endLocation!, radiusMeters: 6000),
          ])
        : Future.value(const <List<MapPoi>>[]);

    final standardRoute = await standardRouteFuture;

  

    if (standardRoute == null) {
      _currentRoute = null;
      _error = _travelMode == 'walking'
          ? 'A walking route is unavailable for these locations.'
          : 'A driving route could not be calculated. Check the places and network.';
      _setLoading(false);
      return;
    }

    // Show the ordinary route immediately. EcoSaver/POI enrichment is a
    // second-stage operation and must not block first paint of the route.
    _metroPlan = null;
    _currentRoute = standardRoute;
    _setLoading(false);

    // Finish auxiliary POI work in the background.
    poiFuture.then((poiResults) {
      for (final list in poiResults) {
        _mergePois(list);
      }
      notifyListeners();
    }).catchError((_) {});

    if (_isEcoSaverActive) {
      try {
        final plan = await _apiService.buildMetroPlan(_startLocation!, _endLocation!);
        if (plan != null && _isEcoSaverActive) {
          _metroPlan = plan;
          final combined = <LatLng>[
            ...plan.walkToStation,
            ...plan.metroLeg,
            ...plan.walkFromStation,
          ];
          _currentRoute = TrafficRoute(
            pathNodes: const [],
            distanceMeters: plan.totalMeters,
            timeMinutes: plan.totalMinutes,
            coordinates: combined,
            congestion: standardRoute.congestion,
            predictedCongestion: standardRoute.predictedCongestion,
            co2SavedKg: plan.metroMeters / 1000.0 * 0.16,
            isEcoRoute: true,
            isFallback: standardRoute.isFallback,
          );
          notifyListeners();
        }
      } catch (_) {
        _metroPlan = null;
      }
    }
  }

  Future<void> startJourney() async {
    _error = null;
    final permissionOk = await locateMe(useAsStart: false);
    if (!permissionOk) return;

    _startLocation = LatLng(_currentPosition!.latitude, _currentPosition!.longitude);
    _startText = 'Current location';
    _startNodeId = null;
    await calculateRoute();
    if (_currentRoute == null) return;

    _previousPosition = _currentPosition;
    _hasActualMovement = false;
    _metroBoardReady = false;
    _metroBoarded = false;
    _metroArrivalReached = false;
    _metroCreditsAwarded = false;
    _tripRecorded = false;
    _isJourneyActive = true;
    notifyListeners();

    await _positionSubscription?.cancel();
    const settings = LocationSettings(accuracy: LocationAccuracy.best, distanceFilter: 5);
    _positionSubscription = Geolocator.getPositionStream(locationSettings: settings).listen(
      _onPosition,
      onError: (_) {
        _error = 'Location tracking stopped unexpectedly.';
        notifyListeners();
      },
    );
  }

Future<void> stopJourney() async {
  final creditsProvider = _creditsProvider;

  if (!_tripRecorded && _currentRoute != null && creditsProvider != null) {
    creditsProvider.recordTrip(
      start: _startText,
      destination: _endText,
      mode: _isEcoSaverActive && _metroPlan != null ? 'Metro / Eco' : _travelMode,
      distanceMeters: _currentRoute!.distanceMeters,
    );

    _tripRecorded = true;
  }

  await _positionSubscription?.cancel();
  _positionSubscription = null;
  _isJourneyActive = false;
  _hasActualMovement = false;
  _previousPosition = null;
  notifyListeners();
}

  void boardMetro(EcoCreditsProvider credits) {
    if (!_metroBoardReady || _metroPlan == null || _metroBoarded) return;
    _metroBoarded = true;
    if (!_metroCreditsAwarded) {
      final plan = _metroPlan!;
      credits.addCredits(
        plan.credits,
        plan.metroMeters / 1000.0 * 0.16,
        title: '${plan.originStationName} → ${plan.destinationStationName} metro',
      );
      _metroCreditsAwarded = true;
    }
    notifyListeners();
  }

  void _onPosition(Position position) {
    final previous = _currentPosition;
    _previousPosition = previous;
    _currentPosition = position;
    if (previous != null) {
      final moved = Geolocator.distanceBetween(
        previous.latitude,
        previous.longitude,
        position.latitude,
        position.longitude,
      );
      if (moved >= 3) _hasActualMovement = true;
    }

    final plan = _metroPlan;
    if (_isJourneyActive && _isEcoSaverActive && plan != null) {
      final here = LatLng(position.latitude, position.longitude);
      final toStation = _distanceMeters(here, plan.originStation);
      final toDestinationStation = _distanceMeters(here, plan.destinationStation);
      if (!_metroBoarded && toStation <= 120) _metroBoardReady = true;
      if (_metroBoarded && toDestinationStation <= 150) _metroArrivalReached = true;
    }
    notifyListeners();
  }

  Future<void> loadNearbyPois(LatLng center, {Set<PoiType>? types}) async {
    final result = await _poiService.fetchNearby(
      center,
      types: types ?? const {PoiType.metro, PoiType.school, PoiType.hospital},
      radiusMeters: 12000,
    );
    _mergePois(result);
    notifyListeners();
  }

  void _mergePois(List<MapPoi> incoming) {
    final map = {for (final p in _pois) p.id: p};
    for (final poi in incoming) map[poi.id] = poi;
    _pois = map.values.toList();
  }

  Future<bool> refreshTraffic() async {
    final result = await _apiService.fetchTraffic();
    if (result.isNotEmpty) {
      _roads = result;
      _demoMode = false;
      notifyListeners();
      return true;
    }
    return false;
  }

  LatLng? _nodeToLatLng(String? id) {
    if (id == null) return null;
    for (final n in _nodes) {
      if (n.id == id) return LatLng(n.lat, n.lng);
    }
    return null;
  }

  String _nodeName(String? id) {
    if (id == null) return '';
    for (final node in _nodes) {
      if (node.id == id) return node.name.isNotEmpty ? node.name : 'Intersection $id';
    }
    return '';
  }

  double _distanceMeters(LatLng a, LatLng b) {
    const earthRadius = 6371000.0;
    final lat1 = a.latitude * pi / 180.0;
    final lat2 = b.latitude * pi / 180.0;
    final dLat = (b.latitude - a.latitude) * pi / 180.0;
    final dLon = (b.longitude - a.longitude) * pi / 180.0;
    final h = sin(dLat / 2) * sin(dLat / 2) +
        cos(lat1) * cos(lat2) * sin(dLon / 2) * sin(dLon / 2);
    return earthRadius * 2 * atan2(sqrt(h.clamp(0.0, 1.0)), sqrt((1 - h).clamp(0.0, 1.0)));
  }

  void _clearRoutes() {
    _currentRoute = null;
    _metroPlan = null;
    _metroBoardReady = false;
    _metroBoarded = false;
    _metroArrivalReached = false;
    _metroCreditsAwarded = false;
  }

  void _setLoading(bool value) {
    _isLoading = value;
    notifyListeners();
  }
}

