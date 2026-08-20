import '../models/route_model.dart';

class TrafficPredictionService {
  TrafficPrediction fallback(double current) => TrafficPrediction(
    roadId: 'fallback',
    currentCongestion: current,
    predictedCongestion: (current + .11).clamp(0.0, 1.0).toDouble(),
    horizonMinutes: 15,
    isFallback: true,
  );
}
