import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:latlong2/latlong.dart';

import '../config/app_theme.dart';
import '../models/poi_model.dart';
import '../providers/traffic_provider.dart';

class NearbyPlacesSheet extends StatefulWidget {
  const NearbyPlacesSheet({super.key});

  @override
  State<NearbyPlacesSheet> createState() => _NearbyPlacesSheetState();
}

class _NearbyPlacesSheetState extends State<NearbyPlacesSheet> {
  PoiType _selected = PoiType.metro;

  @override
  Widget build(BuildContext context) {
    final p = context.watch<TrafficProvider>();
    final items = p.pois.where((e) => e.type == _selected).take(30).toList();
    return DraggableScrollableSheet(
      initialChildSize: .62,
      minChildSize: .42,
      maxChildSize: .92,
      expand: false,
      builder: (_, scrollController) => Material(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        child: Column(
          children: [
            const SizedBox(height: 10),
            Container(width: 42, height: 4, decoration: BoxDecoration(color: Colors.grey, borderRadius: BorderRadius.circular(20))),
            const Padding(
              padding: EdgeInsets.fromLTRB(18, 14, 18, 3),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text('Nearby places', style: TextStyle(fontSize: 19, fontWeight: FontWeight.w800)),
              ),
            ),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 18),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text('Metro stations, schools and hospitals from OpenStreetMap.', style: TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
              ),
            ),
            const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              child: Row(children: [
                Expanded(child: _chip('Metro', Icons.subway_rounded, PoiType.metro)),
                const SizedBox(width: 7),
                Expanded(child: _chip('Schools', Icons.school_outlined, PoiType.school)),
                const SizedBox(width: 7),
                Expanded(child: _chip('Hospitals', Icons.local_hospital_outlined, PoiType.hospital)),
              ]),
            ),
            const SizedBox(height: 10),
            Expanded(
              child: items.isEmpty
                  ? const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('No mapped places found nearby. Try another location.', textAlign: TextAlign.center)))
                  : ListView.separated(
                      controller: scrollController,
                      padding: const EdgeInsets.fromLTRB(14, 4, 14, 18),
                      itemCount: items.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 7),
                      itemBuilder: (_, i) {
                        final poi = items[i];
                        return ListTile(
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                          tileColor: Theme.of(context).brightness == Brightness.dark ? Colors.white.withValues(alpha: .045) : Colors.black.withValues(alpha: .03),
                          leading: CircleAvatar(
                            backgroundColor: _color(poi.type).withValues(alpha: .12),
                            child: Icon(_icon(poi.type), color: _color(poi.type)),
                          ),
                          title: Text(poi.name, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700)),
                          subtitle: Text(poi.network?.isNotEmpty == true ? poi.network! : _label(poi.type), style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary)),
                          trailing: const Icon(Icons.arrow_forward_ios_rounded, size: 15),
                          onTap: () {
                            context.read<TrafficProvider>().setEndPlace(poi.name, poi.location);
                            Navigator.pop(context);
                          },
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _chip(String label, IconData icon, PoiType type) {
    final active = _selected == type;
    final color = _color(type);
    return InkWell(
      borderRadius: BorderRadius.circular(13),
      onTap: () async {
        setState(() => _selected = type);
        final p = context.read<TrafficProvider>();
        final center = p.currentPosition == null
            ? p.startLocation
            : LatLng(p.currentPosition!.latitude, p.currentPosition!.longitude);
        if (center != null) await p.loadNearbyPois(center, types: {type});
      },
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 10),
        decoration: BoxDecoration(color: active ? color.withValues(alpha: .12) : Colors.transparent, borderRadius: BorderRadius.circular(13), border: Border.all(color: active ? color.withValues(alpha: .45) : Colors.black12)),
        child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(icon, size: 17, color: active ? color : null), const SizedBox(width: 5), Text(label, style: TextStyle(fontSize: 12, fontWeight: active ? FontWeight.w800 : FontWeight.w600))]),
      ),
    );
  }

  IconData _icon(PoiType type) => switch (type) {
        PoiType.metro => Icons.subway_rounded,
        PoiType.school => Icons.school_rounded,
        PoiType.hospital => Icons.local_hospital_rounded,
      };

  String _label(PoiType type) => switch (type) {
        PoiType.metro => 'Metro station',
        PoiType.school => 'School',
        PoiType.hospital => 'Hospital',
      };

  Color _color(PoiType type) => switch (type) {
        PoiType.metro => const Color(0xFF8E5CF6),
        PoiType.school => AppTheme.accentBlue,
        PoiType.hospital => AppTheme.accentRed,
      };
}
