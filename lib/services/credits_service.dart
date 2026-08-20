class CreditsService {
  int ecoSaverReward({required bool metro, required bool walking, required double carbonSavedKg}) {
    var points = metro ? 15 : 10;
    if (walking) points += 5;
    if (carbonSavedKg >= 1) points += 5;
    return points;
  }
}
