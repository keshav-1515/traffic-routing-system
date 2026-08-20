import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config/app_theme.dart';
import '../providers/eco_credits_provider.dart';
import '../providers/theme_provider.dart';
import '../providers/traffic_provider.dart';

class ProfileView extends StatelessWidget {
  const ProfileView({super.key});

  @override
  Widget build(BuildContext context) {
    final credits = context.watch<EcoCreditsProvider>();
    final traffic = context.watch<TrafficProvider>();
    final theme = context.watch<ThemeProvider>();
    final dark = Theme.of(context).brightness == Brightness.dark;

    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 10, 18, 28),
        children: [
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              gradient: const LinearGradient(colors: [AppTheme.accentBlue, Color(0xFF5A5CF0)]),
              borderRadius: BorderRadius.circular(24),
            ),
            child: const Row(children: [
              CircleAvatar(radius: 34, backgroundColor: Colors.white24, child: Icon(Icons.person, size: 38, color: Colors.white)),
              SizedBox(width: 16),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('NavQ Commuter', style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.bold)),
                SizedBox(height: 3),
                Text('commuter@navq.app', style: TextStyle(color: Colors.white70)),
                SizedBox(height: 5),
                Text('Eco Pioneer', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
              ])),
            ]),
          ),
          const SizedBox(height: 18),
          Row(children: [
            Expanded(child: _metric('Credits', '${credits.credits}', Icons.stars, AppTheme.accentAmber, dark)),
            const SizedBox(width: 12),
            Expanded(child: _metric('Eco Trips', '${credits.ecoTrips}', Icons.eco, AppTheme.accentGreen, dark)),
            const SizedBox(width: 12),
            Expanded(child: _metric('CO₂ Saved', '${credits.totalCo2Saved.toStringAsFixed(1)} kg', Icons.cloud_done, AppTheme.accentBlue, dark)),
          ]),
          const SizedBox(height: 20),
          _section(context, 'Travel Preferences', [
            ListTile(
              leading: Icon(traffic.travelMode == 'walking' ? Icons.directions_walk : Icons.directions_car, color: AppTheme.accentBlue),
              title: const Text('Preferred travel mode'),
              subtitle: Text(traffic.travelMode == 'walking' ? 'Walking' : 'Car'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _chooseTravelMode(context),
            ),
            ListTile(
              leading: const Icon(Icons.route, color: AppTheme.accentGreen),
              title: const Text('Trip history'),
              subtitle: Text('${credits.trips.length} recent routes'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const TripHistoryView())),
            ),
          ]),
          const SizedBox(height: 14),
          _section(context, 'Settings', [
            ListTile(
              leading: const Icon(Icons.dark_mode_outlined),
              title: const Text('Appearance'),
              subtitle: Text(theme.mode == ThemeMode.dark ? 'Dark' : theme.mode == ThemeMode.light ? 'Light' : 'System'),
              trailing: DropdownButton<ThemeMode>(
                value: theme.mode,
                underline: const SizedBox(),
                items: const [
                  DropdownMenuItem(value: ThemeMode.system, child: Text('System')),
                  DropdownMenuItem(value: ThemeMode.light, child: Text('Light')),
                  DropdownMenuItem(value: ThemeMode.dark, child: Text('Dark')),
                ],
                onChanged: (value) {
                  if (value != null) theme.setMode(value);
                },
              ),
            ),
            ListTile(
              leading: const Icon(Icons.notifications_none),
              title: const Text('Notifications'),
              subtitle: const Text('Alerts, eco rewards and trip updates'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const NotificationSettingsView())),
            ),
            ListTile(
              leading: const Icon(Icons.location_on_outlined),
              title: const Text('Location permissions'),
              subtitle: const Text('GPS access for navigation'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const LocationPermissionView())),
            ),
            ListTile(
              leading: const Icon(Icons.privacy_tip_outlined),
              title: const Text('Privacy'),
              subtitle: const Text('How NavQ handles route and location data'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PrivacyView())),
            ),
            ListTile(
              leading: const Icon(Icons.info_outline),
              title: const Text('About NavQ'),
              subtitle: const Text('AI traffic intelligence + EcoSaver'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const AboutNavQView())),
            ),
          ]),
        ],
      ),
    );
  }

  Widget _metric(String label, String value, IconData icon, Color color, bool dark) => Container(
        padding: const EdgeInsets.all(13),
        decoration: BoxDecoration(
          color: dark ? Colors.white.withValues(alpha: .045) : Colors.white,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: dark ? Colors.white.withValues(alpha: .06) : Colors.black12),
        ),
        child: Column(children: [
          Icon(icon, color: color),
          const SizedBox(height: 7),
          Text(value, style: TextStyle(fontWeight: FontWeight.bold, color: color)),
          const SizedBox(height: 2),
          Text(label, style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary), textAlign: TextAlign.center),
        ]),
      );

  Widget _section(BuildContext context, String title, List<Widget> children) => Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 4), child: Text(title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16))),
            ...children,
          ]),
        ),
      );

  void _chooseTravelMode(BuildContext context) async {
    final traffic = context.read<TrafficProvider>();
    final mode = await showModalBottomSheet<String>(
      context: context,
      builder: (_) => SafeArea(child: Column(mainAxisSize: MainAxisSize.min, children: [
        const Padding(padding: EdgeInsets.fromLTRB(18, 16, 18, 8), child: Align(alignment: Alignment.centerLeft, child: Text('Preferred travel mode', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)))),
        ListTile(leading: const Icon(Icons.directions_car), title: const Text('Car'), trailing: traffic.travelMode == 'car' ? const Icon(Icons.check, color: AppTheme.accentBlue) : null, onTap: () => Navigator.pop(context, 'car')),
        ListTile(leading: const Icon(Icons.directions_walk), title: const Text('Walking'), trailing: traffic.travelMode == 'walking' ? const Icon(Icons.check, color: AppTheme.accentBlue) : null, onTap: () => Navigator.pop(context, 'walking')),
        const SizedBox(height: 8),
      ])),
    );
    if (mode != null) await traffic.setTravelMode(mode);
  }
}

class TripHistoryView extends StatelessWidget {
  const TripHistoryView({super.key});

  @override
  Widget build(BuildContext context) {
    final trips = context.watch<EcoCreditsProvider>().trips;
    return Scaffold(
      appBar: AppBar(title: const Text('Trip history')),
      body: trips.isEmpty
          ? const Center(child: Padding(padding: EdgeInsets.all(30), child: Text('Completed routes will appear here after you finish a journey.', textAlign: TextAlign.center)))
          : ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: trips.length,
              separatorBuilder: (_, __) => const SizedBox(height: 8),
              itemBuilder: (_, index) {
                final trip = trips[index];
                final icon = trip.mode.toLowerCase().contains('metro') ? Icons.subway_rounded : trip.mode == 'walking' ? Icons.directions_walk : Icons.directions_car;
                return Card(
                  child: ListTile(
                    leading: CircleAvatar(backgroundColor: AppTheme.accentBlue.withValues(alpha: .1), child: Icon(icon, color: AppTheme.accentBlue)),
                    title: Text(trip.destination, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: Text('${trip.start}  •  ${trip.distanceMeters < 1000 ? '${trip.distanceMeters.round()} m' : '${(trip.distanceMeters / 1000).toStringAsFixed(1)} km'}  •  ${trip.mode}\n${_date(trip.date)}'),
                    isThreeLine: true,
                  ),
                );
              },
            ),
    );
  }

  String _date(DateTime date) => '${date.day.toString().padLeft(2, '0')}/${date.month.toString().padLeft(2, '0')}/${date.year} ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
}

class NotificationSettingsView extends StatefulWidget {
  const NotificationSettingsView({super.key});
  @override
  State<NotificationSettingsView> createState() => _NotificationSettingsViewState();
}

class _NotificationSettingsViewState extends State<NotificationSettingsView> {
  bool _all = true;
  bool _traffic = true;
  bool _eco = true;
  bool _journey = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _all = prefs.getBool('notif_all') ?? true;
      _traffic = prefs.getBool('notif_traffic') ?? true;
      _eco = prefs.getBool('notif_eco') ?? true;
      _journey = prefs.getBool('notif_journey') ?? true;
    });
  }

  Future<void> _save(String key, bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(key, value);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Notifications')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(child: Column(children: [
          SwitchListTile(title: const Text('Allow notifications', style: TextStyle(fontWeight: FontWeight.w700)), subtitle: const Text('Master switch for NavQ alerts'), value: _all, onChanged: (v) { setState(() => _all = v); _save('notif_all', v); }),
          const Divider(height: 1),
          SwitchListTile(title: const Text('Traffic updates'), subtitle: const Text('Congestion and route changes'), value: _traffic, onChanged: _all ? (v) { setState(() => _traffic = v); _save('notif_traffic', v); } : null),
          SwitchListTile(title: const Text('EcoSaver rewards'), subtitle: const Text('Credits earned and milestone alerts'), value: _eco, onChanged: _all ? (v) { setState(() => _eco = v); _save('notif_eco', v); } : null),
          SwitchListTile(title: const Text('Journey reminders'), subtitle: const Text('Start, stop and arrival reminders'), value: _journey, onChanged: _all ? (v) { setState(() => _journey = v); _save('notif_journey', v); } : null),
        ])),
        const SizedBox(height: 12),
        const Text('These switches control NavQ in-app notification preferences. Android system permission may still be required.', style: TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
      ]),
    );
  }
}

class LocationPermissionView extends StatefulWidget {
  const LocationPermissionView({super.key});
  @override
  State<LocationPermissionView> createState() => _LocationPermissionViewState();
}

class _LocationPermissionViewState extends State<LocationPermissionView> {
  LocationPermission? _permission;
  bool _enabled = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final enabled = await Geolocator.isLocationServiceEnabled();
    final permission = await Geolocator.checkPermission();
    if (mounted) setState(() { _enabled = enabled; _permission = permission; });
  }

  @override
  Widget build(BuildContext context) {
    final granted = _permission == LocationPermission.whileInUse || _permission == LocationPermission.always;
    return Scaffold(appBar: AppBar(title: const Text('Location permissions')), body: ListView(padding: const EdgeInsets.all(16), children: [
      Card(child: Padding(padding: const EdgeInsets.all(18), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Icon(Icons.location_on_rounded, size: 34, color: AppTheme.accentBlue),
        const SizedBox(height: 10),
        Text(_enabled ? 'Location services are enabled' : 'Location services are disabled', style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 17)),
        const SizedBox(height: 6),
        Text(granted ? 'NavQ can use GPS for navigation and for detecting when you reach a metro station.' : 'Grant location access so NavQ can use your live position.' , style: const TextStyle(color: AppTheme.textSecondary)),
        const SizedBox(height: 15),
        FilledButton.icon(onPressed: () async { await Geolocator.openAppSettings(); await _refresh(); }, icon: const Icon(Icons.settings_outlined), label: const Text('Open app settings')),
        const SizedBox(height: 8),
        OutlinedButton.icon(onPressed: _refresh, icon: const Icon(Icons.refresh), label: const Text('Refresh permission status')),
      ]))),
    ]));
  }
}

class PrivacyView extends StatelessWidget {
  const PrivacyView({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Privacy')),
      body: const Padding(
        padding: EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('NavQ privacy', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
            SizedBox(height: 12),
            Text('Your live location is used to calculate routes, center the map and detect proximity to the selected metro stations during an active journey.'),
            SizedBox(height: 12),
            Text('Trip history and Eco Credits are stored locally on the device in NavQ preferences. External map/geocoding services may receive the location or place text required to calculate a route.'),
          ],
        ),
      ),
    );
  }
}

class AboutNavQView extends StatelessWidget {
  const AboutNavQView({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('About NavQ')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            const CircleAvatar(
              radius: 36,
              backgroundColor: AppTheme.accentBlue,
              child: Icon(Icons.navigation_rounded, color: Colors.white, size: 38),
            ),
            const SizedBox(height: 12),
            const Text('NavQ', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w900)),
            const SizedBox(height: 4),
            const Text('AI traffic intelligence + EcoSaver', style: TextStyle(color: AppTheme.textSecondary)),
            const SizedBox(height: 22),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  children: const [
                    ListTile(
                      leading: Icon(Icons.map_outlined),
                      title: Text('OpenStreetMap routing and place data'),
                      subtitle: Text('Used for global locations and nearby metro/school/hospital discovery.'),
                    ),
                    ListTile(
                      leading: Icon(Icons.eco_outlined),
                      title: Text('EcoSaver'),
                      subtitle: Text('Estimates greener multimodal routes using mapped metro stations and awards credits for metro distance.'),
                    ),
                    ListTile(
                      leading: Icon(Icons.location_searching),
                      title: Text('Live navigation'),
                      subtitle: Text('Uses device GPS only while navigation is active.'),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
