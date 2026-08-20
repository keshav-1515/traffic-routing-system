import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AppTheme {
  static const Color backgroundLight = Color(0xFFF6F8FB);
  static const Color surfaceLight = Colors.white;
  static const Color backgroundDark = Color(0xFF0D1117);
  static const Color surfaceDark = Color(0xFF151A23);
  static const Color accentGreen = Color(0xFF16C784);
  static const Color accentAmber = Color(0xFFFFB020);
  static const Color accentRed = Color(0xFFFF4D5E);
  static const Color accentBlue = Color(0xFF287BFF);
  static const Color textPrimary = Color(0xFFF7F9FC);
  static const Color textSecondary = Color(0xFF9AA5B5);

  static ThemeData _base({required Brightness brightness}) {
    final dark = brightness == Brightness.dark;
    final surface = dark ? surfaceDark : surfaceLight;
    final background = dark ? backgroundDark : backgroundLight;
    final text = dark ? textPrimary : const Color(0xFF17202A);
    final muted = dark ? textSecondary : const Color(0xFF657184);

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      scaffoldBackgroundColor: background,
      colorScheme: ColorScheme.fromSeed(
        seedColor: accentBlue,
        brightness: brightness,
        primary: accentBlue,
        secondary: accentGreen,
        surface: surface,
        error: accentRed,
      ),
      textTheme: GoogleFonts.outfitTextTheme().apply(
        bodyColor: text,
        displayColor: text,
      ).copyWith(
        bodyMedium: GoogleFonts.outfit(color: text, fontSize: 14),
        bodySmall: GoogleFonts.outfit(color: muted, fontSize: 12),
      ),
      cardTheme: CardThemeData(
        color: surface,
        elevation: dark ? 3 : 1,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: dark ? Colors.white.withValues(alpha: .055) : Colors.black.withValues(alpha: .035),
        labelStyle: TextStyle(color: muted, fontWeight: FontWeight.w600),
        hintStyle: TextStyle(color: muted.withValues(alpha: .82)),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide.none,
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: background,
        foregroundColor: text,
        elevation: 0,
      ),
      dividerTheme: DividerThemeData(
        color: dark ? Colors.white.withValues(alpha: .08) : Colors.black.withValues(alpha: .08),
        thickness: 1,
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    );
  }

  static ThemeData get lightTheme => _base(brightness: Brightness.light);
  static ThemeData get darkTheme => _base(brightness: Brightness.dark);
}
