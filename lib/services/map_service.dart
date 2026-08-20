import 'package:flutter/material.dart';

class MapService {
  static Color congestionColor(double score) {
    if (score < .4) return const Color(0xFF2ECC71);
    if (score < .7) return const Color(0xFFF5B041);
    return const Color(0xFFE74C3C);
  }
}
