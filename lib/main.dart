import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'config/app_theme.dart';
import 'providers/traffic_provider.dart';
import 'providers/eco_credits_provider.dart';
import 'providers/theme_provider.dart';
import 'views/map_home_view.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final credits = EcoCreditsProvider();
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: credits),
        ChangeNotifierProvider(create: (_) => TrafficProvider(credits)),
        ChangeNotifierProvider(create: (_) => ThemeProvider()),
      ],
      child: const NavQApp(),
    ),
  );
}

class NavQApp extends StatelessWidget { const NavQApp({super.key});
 @override Widget build(BuildContext context){final theme=context.watch<ThemeProvider>();return MaterialApp(title:'NavQ',debugShowCheckedModeBanner:false,theme:AppTheme.lightTheme,darkTheme:AppTheme.darkTheme,themeMode:theme.mode,home:const MapHomeView());}
}
