import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/eco_credits_provider.dart';
import '../config/app_theme.dart';

class CreditsSheet extends StatelessWidget {
  const CreditsSheet({super.key});

  @override
  Widget build(BuildContext context) {
    final c = context.watch<EcoCreditsProvider>();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            const Icon(Icons.stars, color: AppTheme.accentAmber, size: 28),
            const SizedBox(width: 8),
            const Text(
              'Eco Credits',
              style: TextStyle(fontSize: 19, fontWeight: FontWeight.bold),
            ),
            const Spacer(),
            Text(
              '${c.credits}',
              style: const TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.bold,
                color: AppTheme.accentAmber,
              ),
            ),
          ],
        ),
        const SizedBox(height: 5),
        Text(
          '${c.ecoTrips} EcoSaver trips  •  ${c.totalCo2Saved.toStringAsFixed(1)} kg CO₂ saved',
          style: const TextStyle(color: AppTheme.textSecondary),
        ),
        const SizedBox(height: 16),
        const Text(
          'Rewards',
          style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
        ),
        const SizedBox(height: 8),
        ...c.rewards.map((r) {
          return Container(
            margin: const EdgeInsets.only(bottom: 9),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: .04),
              borderRadius: BorderRadius.circular(16),
            ),
            child: Row(
              children: [
                Text(r.icon, style: const TextStyle(fontSize: 24)),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        r.title,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      Text(
                        r.description,
                        style: const TextStyle(
                          fontSize: 11,
                          color: AppTheme.textSecondary,
                        ),
                      ),
                    ],
                  ),
                ),
                FilledButton(
                  onPressed: () {
                    final ok = c.redeemReward(r);
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text(
                          ok
                              ? 'Reward redeemed successfully.'
                              : 'Not enough credits.',
                        ),
                      ),
                    );
                  },
                  child: Text('${r.costCredits}'),
                ),
              ],
            ),
          );
        }),
        const SizedBox(height: 8),
        const Text(
          'Credit History',
          style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
        ),
        if (c.history.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Text(
              'Your credit activity will appear here.',
              style: TextStyle(color: AppTheme.textSecondary),
            ),
          )
        else
          ...c.history.take(5).map(
            (h) => ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(
                h.amount > 0
                    ? Icons.add_circle_outline
                    : Icons.remove_circle_outline,
                color: h.amount > 0
                    ? AppTheme.accentGreen
                    : AppTheme.accentRed,
              ),
              title: Text(h.title),
              trailing: Text(
                '${h.amount > 0 ? '+' : ''}${h.amount}',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: h.amount > 0
                      ? AppTheme.accentGreen
                      : AppTheme.accentRed,
                ),
              ),
            ),
          ),
      ],
    );
  }
}
