import 'dart:convert';
import 'dart:math';
import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';
import '../config/api_constants.dart';
import '../models/graph_node.dart';
import '../models/reward_item.dart';
import '../models/poi_model.dart';
import '../models/metro_route_model.dart';
import 'poi_service.dart';
import '../models/route_model.dart';

class GraphData {
  final List<GraphNode> nodes;
  final List<RoadSegment> roads;
  final bool demoMode;
  const GraphData(this.nodes, this.roads, this.demoMode);
}

class ApiService {
  final PoiService _poiService = PoiService();
  final Map<String, ({DateTime at, List<MapPoi> data})> _poiCache = {};
  Future<GraphData> fetchGraph() async {
    try {
      final response = await http.get(Uri.parse(ApiConstants.graphEndpoint)).timeout(ApiConstants.requestTimeout);
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String,dynamic>;
        final nodes = ((data['nodes']?['features'] as List?) ?? []).whereType<Map<String,dynamic>>().map(GraphNode.fromJson).toList();
        final roads = ((data['edges']?['features'] as List?) ?? []).whereType<Map<String,dynamic>>().map(RoadSegment.fromJson).toList();
        return GraphData(nodes, roads, data['using_synthetic'] == true);
      }
    } catch (_) {}
    return _demoGraph();
  }

  Future<List<RoadSegment>> fetchTraffic() async {
    try {
      final response = await http.get(Uri.parse(ApiConstants.trafficEndpoint)).timeout(ApiConstants.requestTimeout);
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String,dynamic>;
        return ((data['roads'] as List?) ?? []).whereType<Map<String,dynamic>>().map(RoadSegment.fromJson).toList();
      }
    } catch (_) {}
    return [];
  }

  Future<TrafficPrediction?> fetchPrediction(String roadId) async {
    try {
      final response = await http.post(Uri.parse(ApiConstants.predictionEndpoint), headers: {'Content-Type':'application/json'}, body: jsonEncode({'road_id':roadId,'horizon_minutes':15})).timeout(ApiConstants.requestTimeout);
      if (response.statusCode == 200) return TrafficPrediction.fromJson(jsonDecode(response.body));
    } catch (_) {}
    return TrafficPrediction(roadId: roadId, currentCongestion: .63, predictedCongestion: .74, horizonMinutes: 15, isFallback: true);
  }

  Future<TrafficRoute?> fetchRoute(String source, String target, {String mode='car'}) async {
    try {
      final uri = Uri.parse('${ApiConstants.routeEndpoint}?source=$source&target=$target&by=time&mode=$mode');
      final response = await http.get(uri).timeout(ApiConstants.requestTimeout);
      if (response.statusCode == 200) return TrafficRoute.fromJson(jsonDecode(response.body));
    } catch (_) {}
    return _demoRoute(source,target,isEco:false,walking: mode=='walking');
  }

  Future<TrafficRoute?> fetchEcoRoute(String source, String target) async {
    try {
      final response = await http.post(Uri.parse(ApiConstants.ecoRouteEndpoint), headers: {'Content-Type':'application/json'}, body: jsonEncode({'source':source,'target':target})).timeout(ApiConstants.requestTimeout);
      if (response.statusCode == 200) return TrafficRoute.fromJson(jsonDecode(response.body), isEco:true);
    } catch (_) {}
    return _demoRoute(source,target,isEco:true);
  }


  Future<LatLng?> geocode(String query) async {
    final q = query.trim();
    if (q.isEmpty) return null;

    // Nominatim is used only for place resolution. Keep the request small and
    // prefer India results; routing itself is handled by a dedicated router.
    try {
      final uri = Uri.https('nominatim.openstreetmap.org', '/search', {
        'q': q,
        'format': 'jsonv2',
        'limit': '3',
        'addressdetails': '1',
        'countrycodes': 'in',
      });
      final response = await http.get(uri, headers: {
        'User-Agent': 'NavQ/1.2 (navigation app)',
        'Accept': 'application/json',
        'Accept-Language': 'en-IN,en;q=0.8',
      }).timeout(const Duration(seconds: 5));
      if (response.statusCode != 200) return null;

      final data = jsonDecode(response.body) as List?;
      if (data == null || data.isEmpty) return null;

      final best = Map<String, dynamic>.from(data.first as Map);
      final lat = double.tryParse(best['lat']?.toString() ?? '');
      final lon = double.tryParse(best['lon']?.toString() ?? '');
      if (lat == null || lon == null) return null;
      return LatLng(lat, lon);
    } catch (_) {
      return null;
    }
  }


  Future<TrafficRoute?> fetchMapRoute(
    LatLng start,
    LatLng end, {
    String mode = 'car',
  }) async {
    // Use OSRM directly for driving: it is substantially faster than the
    // public Valhalla instance and returns road-network distance + ETA.
    if (mode == 'car') {
      final osrm = await _fetchOsrmRoute(start, end);
      if (osrm != null) return osrm;
      // Valhalla remains the accuracy/availability fallback.
      return _fetchValhallaRoute(start, end, 'auto');
    }

    // Valhalla is retained for pedestrian routing because the public OSRM
    // endpoint is not a walking profile.
    return _fetchValhallaRoute(start, end, 'pedestrian');
  }

  Future<TrafficRoute?> _fetchOsrmRoute(LatLng start, LatLng end) async {
    try {
      final uri = Uri.parse(
        'https://router.project-osrm.org/route/v1/driving/'
        '${start.longitude.toStringAsFixed(6)},${start.latitude.toStringAsFixed(6)};'
        '${end.longitude.toStringAsFixed(6)},${end.latitude.toStringAsFixed(6)}'
        '?overview=full&geometries=geojson&steps=false',
      );
      final response = await http.get(
        uri,
        headers: const {'User-Agent': 'NavQ/1.2 navigation app'},
      ).timeout(const Duration(seconds: 7));
      if (response.statusCode != 200) return null;
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      if (data['code'] != 'Ok') return null;
      return _parseOsrmLikeRoute(data);
    } catch (_) {
      return null;
    }
  }

  Future<TrafficRoute?> _fetchValhallaRoute(
    LatLng start,
    LatLng end,
    String costing,
  ) async {
    try {
      final payload = {
        'locations': [
          {'lat': start.latitude, 'lon': start.longitude, 'type': 'break'},
          {'lat': end.latitude, 'lon': end.longitude, 'type': 'break'},
        ],
        'costing': costing,
        'units': 'kilometers',
        'format': 'osrm',
        'shape_format': 'geojson',
        'directions_type': 'none',
        if (costing == 'pedestrian')
          'costing_options': {
            'pedestrian': {
              'walking_speed': 5.0,
              'use_ferry': 0.25,
              'max_distance': 100.0,
            },
          },
      };

      final response = await http.post(
        Uri.parse(ApiConstants.valhallaEndpoint),
        headers: const {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'User-Agent': 'NavQ/1.2 navigation app',
        },
        body: jsonEncode(payload),
      ).timeout(const Duration(seconds: 7));

      if (response.statusCode != 200) return null;
      return _parseOsrmLikeRoute(
        jsonDecode(response.body) as Map<String, dynamic>,
      );
    } catch (_) {
      return null;
    }
  }

  TrafficRoute? _parseOsrmLikeRoute(Map<String, dynamic> data) {
    final routes = (data['routes'] as List?) ?? const [];
    if (routes.isEmpty) return null;
    final route = routes.first as Map<String, dynamic>;
    final geometry = route['geometry'];

    Map<String, dynamic>? geometryMap;
    if (geometry is Map<String, dynamic>) {
      geometryMap = geometry;
    } else if (geometry is Map) {
      geometryMap = Map<String, dynamic>.from(geometry);
    }

    final raw = (geometryMap?['coordinates'] as List?) ?? const [];
    final allPoints = raw.whereType<List>().where((c) => c.length >= 2).map(
      (c) => LatLng(
        (c[1] as num).toDouble(),
        (c[0] as num).toDouble(),
      ),
    ).toList();

    if (allPoints.length < 2) return null;

    // Full geometry is accurate but can contain several thousand vertices.
    // Simplify before handing it to flutter_map to avoid UI jank.
    final points = _reduceRoutePoints(allPoints, 900);

    final distanceMeters = route['distance'] is num
        ? (route['distance'] as num).toDouble()
        : ((route['summary']?['length'] as num?)?.toDouble() ?? 0) * 1000;
    final durationMinutes = route['duration'] is num
        ? (route['duration'] as num).toDouble() / 60.0
        : ((route['summary']?['time'] as num?)?.toDouble() ?? 0) / 60.0;

    if (distanceMeters <= 0 || durationMinutes <= 0) return null;

    return TrafficRoute(
      pathNodes: const [],
      distanceMeters: distanceMeters,
      timeMinutes: durationMinutes,
      coordinates: points,
      congestion: 0,
      predictedCongestion: 0,
      isFallback: false,
    );
  }

  List<LatLng> _reduceRoutePoints(List<LatLng> points, int maxPoints) {
    if (points.length <= maxPoints) return points;
    final stride = (points.length / maxPoints).ceil();
    final reduced = <LatLng>[];
    for (var i = 0; i < points.length; i += stride) {
      reduced.add(points[i]);
    }
    if (reduced.last != points.last) reduced.add(points.last);
    return reduced;
  }


  Future<List<MapPoi>> fetchNearbyPois(
    LatLng center, {
    Set<PoiType> types = const {PoiType.metro, PoiType.school, PoiType.hospital},
    double radiusMeters = 6000,
  }) async {
    final typeKey = types.map((e) => e.name).toList()..sort();
    final key = '${center.latitude.toStringAsFixed(3)},'
        '${center.longitude.toStringAsFixed(3)}:${typeKey.join(",")}:'
        '${radiusMeters.round()}';
    final cached = _poiCache[key];
    if (cached != null &&
        DateTime.now().difference(cached.at) < const Duration(minutes: 2)) {
      return cached.data;
    }

    final data = await _poiService.fetchNearby(
      center,
      types: types,
      radiusMeters: radiusMeters,
    );
    _poiCache[key] = (at: DateTime.now(), data: data);
    return data;
  }

  Future<MetroRoutePlan?> buildMetroPlan(LatLng start, LatLng end) async {
    // Metro discovery is intentionally limited to 6 km and node stations.
    // The previous implementation queried broad Overpass data twice and then
    // routed the two walking legs sequentially, making EcoSaver feel slow.
    final results = await Future.wait([
      fetchNearbyPois(
        start,
        types: const {PoiType.metro},
        radiusMeters: 6000,
      ),
      fetchNearbyPois(
        end,
        types: const {PoiType.metro},
        radiusMeters: 6000,
      ),
    ]);

    final startStations = results[0].where((p) => p.type == PoiType.metro).take(6).toList();
    final endStations = results[1].where((p) => p.type == PoiType.metro).take(6).toList();
    if (startStations.isEmpty || endStations.isEmpty) return null;

    double directDistance(LatLng a, LatLng b) {
      const earthRadius = 6371000.0;
      final p1 = a.latitude * pi / 180.0;
      final p2 = b.latitude * pi / 180.0;
      final dp = (b.latitude - a.latitude) * pi / 180.0;
      final dl = (b.longitude - a.longitude) * pi / 180.0;
      final h = sin(dp / 2) * sin(dp / 2) +
          cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2);
      return earthRadius *
          2 *
          atan2(sqrt(h.clamp(0.0, 1.0)), sqrt((1 - h).clamp(0.0, 1.0)));
    }

    MapPoi? bestStart;
    MapPoi? bestEnd;
    double bestScore = double.infinity;

    for (final s in startStations) {
      final walkStart = directDistance(start, s.location);
      for (final e in endStations) {
        final metro = directDistance(s.location, e.location);
        final walkEnd = directDistance(e.location, end);
        if (walkStart > 6000 || walkEnd > 6000 || metro < 1200) continue;
        final score = metro + walkStart * 1.8 + walkEnd * 1.8;
        if (score < bestScore) {
          bestScore = score;
          bestStart = s;
          bestEnd = e;
        }
      }
    }

    if (bestStart == null || bestEnd == null) return null;

    final walkingRoutes = await Future.wait([
      fetchMapRoute(start, bestStart.location, mode: 'walking'),
      fetchMapRoute(bestEnd.location, end, mode: 'walking'),
    ]);
    final walkTo = walkingRoutes[0];
    final walkFrom = walkingRoutes[1];
    if (walkTo == null || walkFrom == null) return null;

    final metroMeters = directDistance(bestStart.location, bestEnd.location) * 1.12;
    final metroMinutes = metroMeters / 1000.0 / 32.0 * 60.0 + 5.0;
    final totalMeters = walkTo.distanceMeters + metroMeters + walkFrom.distanceMeters;
    final totalMinutes = walkTo.timeMinutes + metroMinutes + walkFrom.timeMinutes;
    final credits = (metroMeters / 1000.0).round().clamp(5, 250).toInt();

    return MetroRoutePlan(
      originStationName: bestStart.name,
      originStation: bestStart.location,
      destinationStationName: bestEnd.name,
      destinationStation: bestEnd.location,
      walkToStation: walkTo.coordinates,
      metroLeg: [bestStart.location, bestEnd.location],
      walkFromStation: walkFrom.coordinates,
      walkToStationMeters: walkTo.distanceMeters,
      metroMeters: metroMeters,
      walkFromStationMeters: walkFrom.distanceMeters,
      totalMeters: totalMeters,
      walkToMinutes: walkTo.timeMinutes,
      metroMinutes: metroMinutes,
      walkFromMinutes: walkFrom.timeMinutes,
      totalMinutes: totalMinutes,
      credits: credits,
    );
  }


  Future<List<GraphNode>> fetchMetroStations() async {
    try {
      final response = await http.get(Uri.parse(ApiConstants.metroEndpoint)).timeout(ApiConstants.requestTimeout);
      if (response.statusCode == 200) {
        final data=jsonDecode(response.body) as Map<String,dynamic>;
        return ((data['stations'] as List?) ?? []).whereType<Map<String,dynamic>>().map((e)=>GraphNode(id:e['id'].toString(),lat:(e['lat'] as num).toDouble(),lng:(e['lng'] as num).toDouble(),isMetroStation:true,name:e['name'].toString())).toList();
      }
    } catch (_) {}
    return [];
  }

  Future<List<RewardItem>> fetchRewards() async {
    try {
      final response=await http.get(Uri.parse(ApiConstants.rewardsEndpoint)).timeout(ApiConstants.requestTimeout);
      if(response.statusCode==200){final data=jsonDecode(response.body) as Map<String,dynamic>; return ((data['rewards'] as List?)??[]).whereType<Map<String,dynamic>>().map(RewardItem.fromJson).toList();}
    }catch(_){ }
    return [];
  }

  GraphData _demoGraph() {
    final nodes=<GraphNode>[]; final roads=<RoadSegment>[]; const rows=8, cols=8; const lat0=12.9692, lng0=79.1559, step=.0010;
    for(int r=0;r<rows;r++) for(int c=0;c<cols;c++) nodes.add(GraphNode(id:'${r*cols+c}',lat:lat0+r*step,lng:lng0+c*step,name:'Intersection ${r*cols+c}'));
    final random=Random(7);
    void addRoad(GraphNode a,GraphNode b){final p=[LatLng(a.lat,a.lng),LatLng(b.lat,b.lng)]; final cong=.15+random.nextDouble()*.75; roads.add(RoadSegment(id:'${a.id}-${b.id}',points:p,congestion:cong,lengthMeters:110,capacity:1800,freeFlowTimeMinutes:0.12,currentSpeedKmh:30*(1-cong*.65),roadType:'urban'));}
    for(int r=0;r<rows;r++) for(int c=0;c<cols;c++){final n=nodes[r*cols+c]; if(c<cols-1)addRoad(n,nodes[r*cols+c+1]); if(r<rows-1)addRoad(n,nodes[(r+1)*cols+c]);}
    return GraphData(nodes,roads,true);
  }

  TrafficRoute _demoRoute(String source,String target,{required bool isEco,bool walking=false}) {
    final a=int.tryParse(source)??0,b=int.tryParse(target)??1; final cols=8; final ar=a~/cols, ac=a%cols, br=b~/cols, bc=b%cols; final points=<LatLng>[];
    int r=ar,c=ac; points.add(LatLng(12.9692+r*.001,79.1559+c*.001));
    while(c!=bc){c += c<bc?1:-1; points.add(LatLng(12.9692+r*.001,79.1559+c*.001));}
    while(r!=br){r += r<br?1:-1; points.add(LatLng(12.9692+r*.001,79.1559+c*.001));}
    final double distance=(points.length-1)*.11; final double time=walking?distance/4.5*60:distance/30*60*(isEco?1.25:1.35); final double co2=isEco?distance*.18:0;
    return TrafficRoute(pathNodes:[source,target],distanceMeters:distance*1000,timeMinutes:time,coordinates:points,congestion:isEco?0.38:0.63,predictedCongestion:isEco?0.42:0.74,co2SavedKg:co2,isEcoRoute:isEco,isFallback:true);
  }
}
