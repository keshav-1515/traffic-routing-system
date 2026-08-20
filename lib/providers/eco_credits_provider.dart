import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/reward_item.dart';

class CreditTransaction {
  final String title;
  final int amount;
  final DateTime date;
  const CreditTransaction(this.title, this.amount, this.date);
}

class TripRecord {
  final String start;
  final String destination;
  final String mode;
  final double distanceMeters;
  final DateTime date;

  const TripRecord({
    required this.start,
    required this.destination,
    required this.mode,
    required this.distanceMeters,
    required this.date,
  });

  Map<String, dynamic> toJson() => {
        'start': start,
        'destination': destination,
        'mode': mode,
        'distance': distanceMeters,
        'date': date.toIso8601String(),
      };

  factory TripRecord.fromJson(Map<String, dynamic> json) => TripRecord(
        start: '${json['start'] ?? 'Unknown start'}',
        destination: '${json['destination'] ?? 'Unknown destination'}',
        mode: '${json['mode'] ?? 'car'}',
        distanceMeters: (json['distance'] as num?)?.toDouble() ?? 0,
        date: DateTime.tryParse('${json['date']}') ?? DateTime.now(),
      );
}

class EcoCreditsProvider extends ChangeNotifier {
  int _credits = 250;
  double _totalCo2Saved = 18.4;
  int _ecoTrips = 12;
  final List<CreditTransaction> _history = [];
  final List<TripRecord> _trips = [];

  final List<RewardItem> _rewards = const [
    RewardItem(id: 'coffee', title: 'Coffee Coupon', description: 'Green travel coffee reward.', costCredits: 100, category: 'Lifestyle', icon: '☕'),
    RewardItem(id: 'voucher', title: 'Travel Voucher', description: 'Public transport travel voucher.', costCredits: 250, category: 'Transit', icon: '🎟️'),
    RewardItem(id: 'premium', title: 'Premium Reward', description: 'Special commuter reward.', costCredits: 500, category: 'Premium', icon: '🎁'),
  ];

  int get credits => _credits;
  double get totalCo2Saved => _totalCo2Saved;
  int get ecoTrips => _ecoTrips;
  List<RewardItem> get rewards => List.unmodifiable(_rewards);
  List<CreditTransaction> get history => List.unmodifiable(_history);
  List<TripRecord> get trips => List.unmodifiable(_trips);

  EcoCreditsProvider() {
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    _credits = prefs.getInt('credits') ?? _credits;
    _totalCo2Saved = prefs.getDouble('co2_saved') ?? _totalCo2Saved;
    _ecoTrips = prefs.getInt('eco_trips') ?? _ecoTrips;

    final rawTrips = prefs.getStringList('trip_history') ?? const <String>[];
    for (final raw in rawTrips) {
      try {
        _trips.add(TripRecord.fromJson(jsonDecode(raw) as Map<String, dynamic>));
      } catch (_) {}
    }
    notifyListeners();
  }

  Future<void> _persist() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('credits', _credits);
    await prefs.setDouble('co2_saved', _totalCo2Saved);
    await prefs.setInt('eco_trips', _ecoTrips);
    await prefs.setStringList(
      'trip_history',
      _trips.take(30).map((t) => jsonEncode(t.toJson())).toList(),
    );
  }

  void addCredits(int points, double co2, {String title = 'EcoSaver trip'}) {
    if (points <= 0) return;
    _credits += points;
    _totalCo2Saved += co2;
    _ecoTrips += 1;
    _history.insert(0, CreditTransaction(title, points, DateTime.now()));
    _persist();
    notifyListeners();
  }

  void recordTrip({
    required String start,
    required String destination,
    required String mode,
    required double distanceMeters,
  }) {
    if (distanceMeters <= 0) return;
    _trips.insert(
      0,
      TripRecord(
        start: start.isEmpty ? 'Current location' : start,
        destination: destination.isEmpty ? 'Destination' : destination,
        mode: mode,
        distanceMeters: distanceMeters,
        date: DateTime.now(),
      ),
    );
    _persist();
    notifyListeners();
  }

  bool redeemReward(RewardItem reward) {
    if (_credits < reward.costCredits) return false;
    _credits -= reward.costCredits;
    _history.insert(0, CreditTransaction(reward.title, -reward.costCredits, DateTime.now()));
    _persist();
    notifyListeners();
    return true;
  }
}
