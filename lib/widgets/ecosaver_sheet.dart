import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config/app_theme.dart';
import '../providers/eco_credits_provider.dart';
import '../providers/traffic_provider.dart';

class EcoSaverSheet extends StatelessWidget {
  const EcoSaverSheet({super.key});

  @override
  Widget build(BuildContext context) {
    final p = context.watch<TrafficProvider>();
    final c = context.watch<EcoCreditsProvider>();
    final plan = p.metroPlan;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(children: [
          Container(padding: const EdgeInsets.all(10), decoration: BoxDecoration(color: AppTheme.accentGreen.withValues(alpha: .13), shape: BoxShape.circle), child: const Icon(Icons.eco, color: AppTheme.accentGreen)),
          const SizedBox(width: 10),
          const Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('EcoSaver', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            Text('Prefer walking + metro when it is feasible.', style: TextStyle(color: AppTheme.textSecondary)),
          ])),
          Switch(value: p.isEcoSaverActive, onChanged: p.toggleEcoSaver),
        ]),
        const SizedBox(height: 12),
        if (!p.isEcoSaverActive)
          _infoCard(Icons.subway_rounded, 'How it works', 'NavQ checks mapped metro stations near A and B, builds walking links to the stations and estimates the metro leg. Credits scale with metro distance.'),
        if (p.isEcoSaverActive && plan != null) ...[
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(color: AppTheme.accentGreen.withValues(alpha: .08), borderRadius: BorderRadius.circular(18), border: Border.all(color: AppTheme.accentGreen.withValues(alpha: .25))),
            child: Column(children: [
              const Row(children: [Icon(Icons.subway_rounded, size: 18, color: Color(0xFF8E5CF6)), SizedBox(width: 6), Text('Walk + Metro + Walk', style: TextStyle(fontWeight: FontWeight.w800))]),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(child: _stat('Total', _distance(plan.totalMeters))),
                Expanded(child: _stat('Time', _duration(plan.totalMinutes))),
                Expanded(child: _stat('Metro', _distance(plan.metroMeters))),
              ]),
              const SizedBox(height: 10),
              Row(children: [const Icon(Icons.stars, color: AppTheme.accentAmber), const SizedBox(width: 6), Text('+${plan.credits} credits', style: const TextStyle(fontWeight: FontWeight.bold, color: AppTheme.accentAmber))]),
              const SizedBox(height: 10),
              Text('${plan.originStationName} → ${plan.destinationStationName}', maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
            ]),
          ),
          const SizedBox(height: 10),
          const Text('Credits are awarded once when you reach the origin metro station and press “Board metro”. They are based on the mapped metro-leg distance.', style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
        ] else if (p.isEcoSaverActive) ...[
          _infoCard(Icons.route_outlined, 'No feasible metro route found', 'NavQ could not find suitable metro stations close to both ends of this trip. The normal route stays available instead of inventing a transit connection.'),
        ],
        const SizedBox(height: 14),
        Card(child: Padding(padding: const EdgeInsets.all(14), child: Row(children: [const Icon(Icons.park, color: AppTheme.accentGreen), const SizedBox(width: 10), Expanded(child: Text('Current credits: ${c.credits}', style: const TextStyle(fontWeight: FontWeight.w600))), Text('${c.totalCo2Saved.toStringAsFixed(1)} kg saved', style: const TextStyle(color: AppTheme.accentGreen, fontWeight: FontWeight.bold))]))),
      ],
    );
  }

  Widget _infoCard(IconData icon, String title, String body) => Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: Colors.black.withValues(alpha: .03), borderRadius: BorderRadius.circular(18)), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [Icon(icon, color: AppTheme.accentGreen), const SizedBox(width: 10), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w800)), const SizedBox(height: 4), Text(body, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12))]))]));

  Widget _stat(String label, String value) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(label, style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary)), const SizedBox(height: 3), Text(value, style: const TextStyle(fontWeight: FontWeight.bold))]);

  String _distance(double meters) => meters < 1000 ? '${meters.round()} m' : '${(meters / 1000).toStringAsFixed(1)} km';
  String _duration(double minutes) => minutes < 60 ? '${minutes.round()} min' : '${minutes ~/ 60}h ${(minutes % 60).round()}m';
}
