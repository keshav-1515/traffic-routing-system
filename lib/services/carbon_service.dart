class CarbonService {
  static const double carKgPerKm = 0.192;
  static const double metroKgPerKm = 0.041;

  double carEmission(double km) => km * carKgPerKm;
  double metroEmission(double km) => km * metroKgPerKm;
  double saved(double carKm, double ecoKm) => (carEmission(carKm) - metroEmission(ecoKm)).clamp(0.0, double.infinity).toDouble();
}
