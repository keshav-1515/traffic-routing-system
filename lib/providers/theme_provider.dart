import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ThemeProvider extends ChangeNotifier {
  ThemeMode _mode = ThemeMode.system;
  ThemeMode get mode => _mode;

  ThemeProvider(){ _load(); }
  Future<void> _load() async {
    final p=await SharedPreferences.getInstance(); final v=p.getString('theme_mode');
    _mode = v=='light'?ThemeMode.light:v=='dark'?ThemeMode.dark:ThemeMode.system; notifyListeners();
  }
  Future<void> setMode(ThemeMode mode) async {
    _mode=mode; final p=await SharedPreferences.getInstance(); await p.setString('theme_mode',mode.name); notifyListeners();
  }
}
