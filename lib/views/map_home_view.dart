import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:provider/provider.dart';

import '../config/app_theme.dart';
import '../models/poi_model.dart';
import '../providers/eco_credits_provider.dart';
import '../providers/traffic_provider.dart';
import '../widgets/credits_sheet.dart';
import '../widgets/ecosaver_sheet.dart';
import '../widgets/nearby_places_sheet.dart';
import '../widgets/top_search_bar.dart';
import 'profile_view.dart';

class MapHomeView extends StatefulWidget {
  const MapHomeView({super.key});

  @override
  State<MapHomeView> createState() => _MapHomeViewState();
}

class _MapHomeViewState extends State<MapHomeView> {
  final MapController _mapController = MapController();
  Timer? _routeFitTimer;
  String? _lastRouteSignature;

  @override
  void dispose() {
    _routeFitTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.watch<TrafficProvider>();
    final route = p.currentRoute;
    final dark = Theme.of(context).brightness == Brightness.dark;

    _scheduleRouteFit(p);

    return Scaffold(
      body: Stack(
        fit: StackFit.expand,
        children: [
          FlutterMap(
            mapController: _mapController,
            options: MapOptions(
              initialCenter: _mapCenter(p),
              initialZoom: 15.2,
              minZoom: 3,
              maxZoom: 19,
              interactionOptions: const InteractionOptions(flags: InteractiveFlag.all),
            ),
            children: [
              TileLayer(
                urlTemplate: dark
                    ? 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'
                    : 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                subdomains: dark ? const ['a', 'b', 'c', 'd'] : const [],
                userAgentPackageName: 'com.navq.traffic',
                maxZoom: 20,
              ),
              if (p.roads.isNotEmpty)
                PolylineLayer(
                  polylines: p.roads.take(500).map((r) => Polyline(
                    points: r.points,
                    strokeWidth: dark ? 2 : 1.5,
                    color: dark ? const Color(0xFF405064).withValues(alpha: .42) : Colors.black.withValues(alpha: .08),
                  )).toList(),
                ),
              if (p.metroPlan != null)
                PolylineLayer(
                  polylines: [
                    if (p.metroPlan!.walkToStation.length >= 2)
                      Polyline(points: p.metroPlan!.walkToStation, strokeWidth: 8, color: Colors.white.withValues(alpha: .9)),
                    if (p.metroPlan!.walkToStation.length >= 2)
                      Polyline(points: p.metroPlan!.walkToStation, strokeWidth: 4, color: AppTheme.accentGreen),
                    Polyline(points: p.metroPlan!.metroLeg, strokeWidth: 10, color: Colors.white.withValues(alpha: .9)),
                    Polyline(points: p.metroPlan!.metroLeg, strokeWidth: 6, color: const Color(0xFF8E5CF6)),
                    if (p.metroPlan!.walkFromStation.length >= 2)
                      Polyline(points: p.metroPlan!.walkFromStation, strokeWidth: 8, color: Colors.white.withValues(alpha: .9)),
                    if (p.metroPlan!.walkFromStation.length >= 2)
                      Polyline(points: p.metroPlan!.walkFromStation, strokeWidth: 4, color: AppTheme.accentGreen),
                  ],
                )
              else if (route != null && route.coordinates.length >= 2)
                PolylineLayer(
                  polylines: [
                    Polyline(points: route.coordinates, strokeWidth: 11, color: (dark ? Colors.black : Colors.white).withValues(alpha: .94)),
                    Polyline(points: route.coordinates, strokeWidth: 6, color: AppTheme.accentBlue),
                  ],
                ),
              MarkerLayer(
                markers: [
                  ...p.pois.map(_poiMarker),
                  if (p.startLocation != null && !p.isJourneyActive)
                    _placeMarker(p.startLocation!, AppTheme.accentGreen, Icons.trip_origin),
                  if (p.endLocation != null)
                    _placeMarker(p.endLocation!, AppTheme.accentRed, Icons.location_on_rounded),
                  if (p.currentPosition != null && !p.isJourneyActive)
                    Marker(
                      point: LatLng(p.currentPosition!.latitude, p.currentPosition!.longitude),
                      width: 34,
                      height: 34,
                      child: _blueLocationDot(),
                    ),
                  if (p.currentPosition != null && p.isJourneyActive)
                    Marker(
                      point: LatLng(p.currentPosition!.latitude, p.currentPosition!.longitude),
                      width: 58,
                      height: 58,
                      child: _navigationMarker(moving: p.hasActualMovement),
                    ),
                  if (p.metroPlan != null)
                    _placeMarker(p.metroPlan!.originStation, const Color(0xFF8E5CF6), Icons.subway_rounded),
                  if (p.metroPlan != null)
                    _placeMarker(p.metroPlan!.destinationStation, const Color(0xFF8E5CF6), Icons.subway_rounded),
                ],
              ),
            ],
          ),
          const Positioned(top: 0, left: 0, right: 0, child: TopSearchBar()),
          Positioned(
            right: 16,
            bottom: (route == null ? 92 : 268) + 60,
            child: _floatingButton(Icons.place_outlined, () => _openNearbyPlaces(context), color: const Color(0xFF8E5CF6)),
          ),
          Positioned(
            right: 16,
            bottom: (route == null ? 92 : 268) + 8,
            child: _floatingButton(Icons.eco_rounded, () => _openEcoSheet(context), color: AppTheme.accentGreen),
          ),
          Positioned(
            right: 16,
            bottom: route == null ? 92 : 268,
            child: _floatingButton(Icons.my_location_rounded, () => _locate(context)),
          ),
          if (route != null)
            Positioned(left: 12, right: 12, bottom: 76, child: _routeCard(context, p)),
          Positioned(left: 0, right: 0, bottom: 0, child: _bottomNavigation(context)),
        ],
      ),
    );
  }

  Marker _poiMarker(MapPoi poi) {
    final color = switch (poi.type) {
      PoiType.metro => const Color(0xFF8E5CF6),
      PoiType.school => AppTheme.accentBlue,
      PoiType.hospital => AppTheme.accentRed,
    };
    final icon = switch (poi.type) {
      PoiType.metro => Icons.subway_rounded,
      PoiType.school => Icons.school_rounded,
      PoiType.hospital => Icons.local_hospital_rounded,
    };
    return Marker(
      point: poi.location,
      width: 38,
      height: 38,
      child: Tooltip(
        message: poi.name,
        child: Container(
          decoration: BoxDecoration(color: Colors.white, shape: BoxShape.circle, boxShadow: const [BoxShadow(blurRadius: 8, color: Colors.black26)], border: Border.all(color: color, width: 2.5)),
          child: Icon(icon, color: color, size: 19),
        ),
      ),
    );
  }

  LatLng _mapCenter(TrafficProvider p) {
    if (p.currentPosition != null) return LatLng(p.currentPosition!.latitude, p.currentPosition!.longitude);
    if (p.currentRoute != null && p.currentRoute!.coordinates.isNotEmpty) return p.currentRoute!.coordinates.first;
    if (p.startLocation != null) return p.startLocation!;
    if (p.nodes.isNotEmpty) return LatLng(p.nodes.first.lat, p.nodes.first.lng);
    return const LatLng(20.5937, 78.9629);
  }

  void _scheduleRouteFit(TrafficProvider p) {
    final route = p.currentRoute;
    if (route == null || route.coordinates.length < 2) return;
    final signature = '${p.isEcoSaverActive}:${route.coordinates.length}:${route.coordinates.first}:${route.coordinates.last}';
    if (signature == _lastRouteSignature) return;
    _lastRouteSignature = signature;
    _routeFitTimer?.cancel();
    _routeFitTimer = Timer(const Duration(milliseconds: 180), () {
      if (!mounted) return;
      _fitRoute(route.coordinates);
    });
  }

  void _fitRoute(List<LatLng> points) {
    if (points.length < 2) return;
    var minLat = points.first.latitude;
    var maxLat = points.first.latitude;
    var minLng = points.first.longitude;
    var maxLng = points.first.longitude;
    for (final point in points.skip(1)) {
      minLat = math.min(minLat, point.latitude);
      maxLat = math.max(maxLat, point.latitude);
      minLng = math.min(minLng, point.longitude);
      maxLng = math.max(maxLng, point.longitude);
    }
    final center = LatLng((minLat + maxLat) / 2, (minLng + maxLng) / 2);
    final span = math.max(maxLat - minLat, maxLng - minLng);
    final zoom = span < .001 ? 17.5 : span < .003 ? 16.5 : span < .008 ? 15.5 : span < .02 ? 14.5 : span < .06 ? 12.5 : span < .15 ? 10.5 : span < .5 ? 8.5 : span < 2 ? 6.5 : span < 6 ? 5.0 : span < 15 ? 3.8 : 2.6;
    _mapController.move(center, zoom);
  }

  Marker _placeMarker(LatLng point, Color color, IconData icon) => Marker(
    point: point,
    width: 44,
    height: 44,
    child: Container(
      decoration: BoxDecoration(color: Colors.white, shape: BoxShape.circle, boxShadow: const [BoxShadow(blurRadius: 9, color: Colors.black26)], border: Border.all(color: color, width: 3)),
      child: Icon(icon, color: color, size: 22),
    ),
  );

  Widget _navigationMarker({required bool moving}) => Stack(
    alignment: Alignment.center,
    children: [
      if (moving) Container(width: 56, height: 56, decoration: BoxDecoration(shape: BoxShape.circle, color: AppTheme.accentBlue.withValues(alpha: .13))),
      Container(width: 42, height: 42, decoration: BoxDecoration(color: AppTheme.accentBlue, shape: BoxShape.circle, boxShadow: const [BoxShadow(blurRadius: 10, color: Colors.black38)], border: Border.all(color: Colors.white, width: 3)), child: Icon(moving ? Icons.navigation_rounded : Icons.my_location_rounded, color: Colors.white, size: 22)),
    ],
  );

  Widget _blueLocationDot() => Container(
    decoration: const BoxDecoration(color: Colors.white, shape: BoxShape.circle, boxShadow: [BoxShadow(blurRadius: 8, color: Colors.black38)]),
    padding: const EdgeInsets.all(4),
    child: Container(decoration: const BoxDecoration(color: AppTheme.accentBlue, shape: BoxShape.circle)),
  );

  Widget _routeCard(BuildContext context, TrafficProvider p) {
    final r = p.currentRoute!;
    final surface = Theme.of(context).colorScheme.surface;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final eco = p.isEcoSaverActive && p.metroPlan != null;
    final credits = context.read<EcoCreditsProvider>();
    final plan = p.metroPlan;
    return Material(
      elevation: 18,
      borderRadius: BorderRadius.circular(26),
      color: surface.withValues(alpha: .985),
      child: Container(
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(26), border: Border.all(color: dark ? Colors.white.withValues(alpha: .08) : Colors.black.withValues(alpha: .06)), boxShadow: [BoxShadow(blurRadius: 28, spreadRadius: -10, offset: const Offset(0, 10), color: Colors.black.withValues(alpha: dark ? .38 : .13))]),
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 13),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Row(children: [
            Container(width: 42, height: 42, decoration: BoxDecoration(color: (eco ? AppTheme.accentGreen : AppTheme.accentBlue).withValues(alpha: .10), shape: BoxShape.circle), child: Icon(eco ? Icons.eco_rounded : Icons.navigation_rounded, color: eco ? AppTheme.accentGreen : AppTheme.accentBlue)),
            const SizedBox(width: 10),
            Expanded(child: Text(eco ? 'EcoSaver metro route' : (p.isJourneyActive ? 'Navigation active' : 'Route ready'), style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800))),
            if (eco) _tag('METRO', const Color(0xFF8E5CF6)) else _tag(r.isFallback ? 'FALLBACK' : 'LIVE ROUTE', r.isFallback ? AppTheme.accentAmber : AppTheme.accentGreen),
            if (p.isJourneyActive) IconButton(tooltip: 'Stop journey', onPressed: p.stopJourney, icon: const Icon(Icons.stop_rounded, color: AppTheme.accentRed)),
          ]),
          const SizedBox(height: 10),
          if (eco && plan != null) ...[
            Row(children: [
              _routeStat(_formatDistance(plan.totalMeters), 'Total trip'),
              _routeStat(_formatDuration(plan.totalMinutes), 'ETA'),
              _routeStat(_formatDistance(plan.metroMeters), 'Metro'),
              _routeStat('+${plan.credits}', 'Credits', accent: AppTheme.accentAmber),
            ]),
            const SizedBox(height: 10),
            _metroLegSummary(plan),
            const SizedBox(height: 10),
            if (p.isJourneyActive && p.metroBoardReady && !p.metroBoarded)
              SizedBox(width: double.infinity, child: FilledButton.icon(onPressed: () => p.boardMetro(credits), icon: const Icon(Icons.confirmation_num_outlined), label: Text('Board metro · earn ${plan.credits} credits')))
            else if (p.isJourneyActive && p.metroBoarded && !p.metroArrivalReached)
              _statusStrip(Icons.subway_rounded, 'Metro boarded · ${plan.destinationStationName} is your exit station', const Color(0xFF8E5CF6))
            else if (p.isJourneyActive && p.metroArrivalReached)
              _statusStrip(Icons.flag_rounded, 'Metro leg complete · continue walking to destination', AppTheme.accentGreen)
            else if (p.isJourneyActive)
              _statusStrip(Icons.directions_walk_rounded, 'Walk to ${plan.originStationName}', AppTheme.accentGreen)
            else
              const SizedBox.shrink(),
            if (eco && !p.isJourneyActive) ...[
              const SizedBox(height: 9),
              SizedBox(width: double.infinity, child: FilledButton.icon(onPressed: p.isLoading ? null : p.startJourney, icon: const Icon(Icons.play_arrow_rounded), label: const Text('Start eco journey'), style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)))))
            ],
          ] else ...[
            Row(children: [_routeStat(_formatDistance(r.distanceMeters), 'Distance'), _routeStat(_formatDuration(r.timeMinutes), 'ETA'), _routeStat(r.congestion > 0 ? '${(r.congestion * 100).round()}%' : '—', 'Traffic'), _routeStat(r.isFallback ? 'Fallback' : 'Live', 'Routing')]),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(child: _modeChip(selected: p.travelMode == 'car', icon: Icons.directions_car_filled_rounded, label: 'Car', enabled: !p.isJourneyActive, onTap: () => p.setTravelMode('car'))),
              const SizedBox(width: 7),
              Expanded(child: _modeChip(selected: p.travelMode == 'walking', icon: Icons.directions_walk_rounded, label: 'Walk', enabled: !p.isJourneyActive, onTap: () => p.setTravelMode('walking'))),
            ]),
          ],
          if (!p.isJourneyActive && !eco) ...[
            const SizedBox(height: 9),
            SizedBox(width: double.infinity, child: FilledButton.icon(onPressed: p.isLoading ? null : p.startJourney, icon: const Icon(Icons.play_arrow_rounded), label: const Text('Start journey'), style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15))))),
          ],
        ]),
      ),
    );
  }

  Widget _metroLegSummary(plan) => Container(
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(color: const Color(0xFF8E5CF6).withValues(alpha: .08), borderRadius: BorderRadius.circular(16)),
    child: Column(children: [
      Row(children: [const Icon(Icons.directions_walk_rounded, size: 18, color: AppTheme.accentGreen), const SizedBox(width: 7), Expanded(child: Text('Walk to ${plan.originStationName}', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700))), Text(_formatDuration(plan.walkToMinutes), style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary))]),
      Padding(padding: const EdgeInsets.only(left: 8, top: 6, bottom: 6), child: Row(children: [Container(width: 2, height: 12, color: const Color(0xFF8E5CF6)), const SizedBox(width: 8), Expanded(child: Text('Metro ${_formatDistance(plan.metroMeters)} · ${_formatDuration(plan.metroMinutes)}', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF8E5CF6))))])),
      Row(children: [const Icon(Icons.directions_walk_rounded, size: 18, color: AppTheme.accentGreen), const SizedBox(width: 7), Expanded(child: Text('Walk from ${plan.destinationStationName}', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700))), Text(_formatDuration(plan.walkFromMinutes), style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary))]),
    ]),
  );

  Widget _statusStrip(IconData icon, String text, Color color) => Container(width: double.infinity, padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11), decoration: BoxDecoration(color: color.withValues(alpha: .10), borderRadius: BorderRadius.circular(14)), child: Row(children: [Icon(icon, color: color, size: 18), const SizedBox(width: 8), Expanded(child: Text(text, style: TextStyle(color: color, fontWeight: FontWeight.w700, fontSize: 12)))]));

  Widget _tag(String text, Color color) => Container(padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6), decoration: BoxDecoration(color: color.withValues(alpha: .11), borderRadius: BorderRadius.circular(10)), child: Text(text, style: TextStyle(color: color, fontSize: 9.5, fontWeight: FontWeight.w900, letterSpacing: .7)));

  Widget _modeChip({required bool selected, required IconData icon, required String label, required bool enabled, required VoidCallback onTap}) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Opacity(opacity: enabled ? 1 : .55, child: Material(color: selected ? AppTheme.accentBlue.withValues(alpha: dark ? .18 : .10) : (dark ? Colors.white.withValues(alpha: .045) : Colors.black.withValues(alpha: .035)), borderRadius: BorderRadius.circular(14), child: InkWell(onTap: enabled ? onTap : null, borderRadius: BorderRadius.circular(14), child: Container(height: 44, decoration: BoxDecoration(borderRadius: BorderRadius.circular(14), border: Border.all(color: selected ? AppTheme.accentBlue.withValues(alpha: .55) : (dark ? Colors.white.withValues(alpha: .06) : Colors.black.withValues(alpha: .05)))), child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(icon, size: 18, color: selected ? AppTheme.accentBlue : null), const SizedBox(width: 7), Text(label, style: TextStyle(fontWeight: selected ? FontWeight.w800 : FontWeight.w600))])))));
  }

  String _formatDistance(double meters) {
    if (meters < 1000) return '${meters.round()} m';
    final km = meters / 1000;
    return km < 100 ? '${km.toStringAsFixed(1)} km' : '${km.round()} km';
  }

  String _formatDuration(double minutes) {
    final total = minutes.round();
    if (total < 60) return '$total min';
    final days = total ~/ 1440;
    final hours = (total % 1440) ~/ 60;
    final mins = total % 60;
    if (days > 0) return '${days}d ${hours}h';
    return mins == 0 ? '${hours}h' : '${hours}h ${mins}m';
  }

  Widget _routeStat(String value, String label, {Color? accent}) => Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(value, style: TextStyle(fontWeight: FontWeight.w800, color: accent)), const SizedBox(height: 2), Text(label, style: const TextStyle(fontSize: 10, color: AppTheme.textSecondary))]));

  Widget _floatingButton(IconData icon, VoidCallback onPressed, {Color? color}) => Builder(builder: (context) {
    final surface = Theme.of(context).colorScheme.surface;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Material(elevation: 7, color: surface.withValues(alpha: .97), shape: const CircleBorder(), child: InkWell(customBorder: const CircleBorder(), onTap: onPressed, child: Padding(padding: const EdgeInsets.all(13), child: Icon(icon, color: color ?? (dark ? Colors.white70 : Colors.black87), size: 23))));
  });

  void _openEcoSheet(BuildContext context) => showModalBottomSheet(context: context, isScrollControlled: true, backgroundColor: Colors.transparent, builder: (_) => const _EcoCreditsSheet());

  void _openNearbyPlaces(BuildContext context) async {
    final p = context.read<TrafficProvider>();
    LatLng? center;
    if (p.currentPosition != null) center = LatLng(p.currentPosition!.latitude, p.currentPosition!.longitude);
    center ??= p.startLocation;
    if (center != null && p.pois.isEmpty) await p.loadNearbyPois(center);
    if (!mounted) return;
    showModalBottomSheet(context: context, isScrollControlled: true, backgroundColor: Colors.transparent, builder: (_) => const NearbyPlacesSheet());
  }

  void _openProfile(BuildContext context) => Navigator.push(context, MaterialPageRoute(builder: (_) => const ProfileView()));

  Widget _bottomNavigation(BuildContext context) {
    final surface = Theme.of(context).colorScheme.surface;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final credits = context.watch<EcoCreditsProvider>();
    return Material(
      elevation: 16,
      color: surface.withValues(alpha: .98),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 68,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _BottomItem(
                icon: Icons.map_outlined,
                label: 'Map',
                selected: true,
                dark: dark,
                onTap: () {},
              ),
              _BottomItem(
                icon: Icons.eco_outlined,
                label: 'Eco · ${credits.credits}',
                selected: false,
                dark: dark,
                onTap: () => _openEcoSheet(context),
              ),
              _BottomItem(
                icon: Icons.person_outline,
                label: 'You',
                selected: false,
                dark: dark,
                onTap: () => _openProfile(context),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _locate(BuildContext context) async {
    final ok = await context.read<TrafficProvider>().locateMe(useAsStart: true);
    if (!mounted) return;
    final p = context.read<TrafficProvider>();
    if (p.currentPosition != null) {
      _mapController.move(
        LatLng(p.currentPosition!.latitude, p.currentPosition!.longitude),
        16.5,
      );
    }
    if (!ok && p.error != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(p.error!)),
      );
    }
  }
}

class _BottomItem extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final bool dark;
  final VoidCallback onTap;

  const _BottomItem({
    required this.icon,
    required this.label,
    required this.selected,
    required this.dark,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final muted = dark ? Colors.white54 : Colors.black54;
    final strong = dark ? Colors.white : Colors.black87;
    return InkWell(
      onTap: onTap,
      child: SizedBox(
        width: 90,
        height: 68,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: selected ? AppTheme.accentBlue : muted, size: 24),
            const SizedBox(height: 3),
            Text(
              label,
              style: TextStyle(
                fontSize: 11,
                fontWeight: selected ? FontWeight.w800 : FontWeight.w500,
                color: selected ? strong : muted,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EcoCreditsSheet extends StatefulWidget {
  const _EcoCreditsSheet();

  @override
  State<_EcoCreditsSheet> createState() => _EcoCreditsSheetState();
}

class _EcoCreditsSheetState extends State<_EcoCreditsSheet> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final surface = Theme.of(context).colorScheme.surface;
    return DraggableScrollableSheet(
      initialChildSize: .62,
      minChildSize: .4,
      maxChildSize: .92,
      expand: false,
      builder: (context, _) {
        return Container(
          decoration: BoxDecoration(
            color: surface,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
            boxShadow: const [BoxShadow(blurRadius: 25, color: Colors.black38)],
          ),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(10),
                child: Container(
                  width: 42,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Colors.grey,
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Row(
                  children: [
                    Expanded(child: _tabButton('EcoSaver', Icons.eco, 0, AppTheme.accentGreen)),
                    Expanded(child: _tabButton('Credits', Icons.stars, 1, AppTheme.accentAmber)),
                  ],
                ),
              ),
              const SizedBox(height: 4),
              Expanded(
                child: _tab == 0 ? const EcoSaverSheet() : const CreditsSheet(),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _tabButton(String label, IconData icon, int index, Color color) {
    final active = _tab == index;
    return InkWell(
      onTap: () => setState(() => _tab = index),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 11),
        decoration: BoxDecoration(
          color: active ? color.withValues(alpha: .12) : Colors.transparent,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 18, color: active ? color : AppTheme.textSecondary),
            const SizedBox(width: 6),
            Text(
              label,
              style: TextStyle(
                fontWeight: FontWeight.bold,
                color: active ? color : AppTheme.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
