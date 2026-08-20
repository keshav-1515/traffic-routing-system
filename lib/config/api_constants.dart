class ApiConstants {
  // Android emulator: http://10.0.2.2:5000
  // Physical phone: replace with your PC LAN IP, e.g. http://192.168.1.10:5000
  // Demo mode is used automatically when the backend cannot be reached.
  static const String baseUrl = 'http://10.0.2.2:5000';
  static const Duration requestTimeout = Duration(seconds: 7);
  static const String valhallaEndpoint = 'https://valhalla1.openstreetmap.de/route';

  static String get graphEndpoint => '$baseUrl/api/graph';
  static String get trafficEndpoint => '$baseUrl/api/traffic';
  static String get predictionEndpoint => '$baseUrl/api/traffic/predict';
  static String get routeEndpoint => '$baseUrl/api/route';
  static String get ecoRouteEndpoint => '$baseUrl/api/eco-route';
  static String get metroEndpoint => '$baseUrl/api/metro/stations';
  static String get profileEndpoint => '$baseUrl/api/user/profile';
  static String get creditsEndpoint => '$baseUrl/api/user/credits';
  static String get rewardsEndpoint => '$baseUrl/api/rewards';
  static String get redeemEndpoint => '$baseUrl/api/rewards/redeem';
}
