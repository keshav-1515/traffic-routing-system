import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../config/app_theme.dart';
import '../providers/traffic_provider.dart';
import '../views/profile_view.dart';

class TopSearchBar extends StatefulWidget {
  const TopSearchBar({super.key});

  @override
  State<TopSearchBar> createState() => _TopSearchBarState();
}

class _TopSearchBarState extends State<TopSearchBar> {
  bool _expanded = true;
  late final TextEditingController _startController;
  late final TextEditingController _endController;

  @override
  void initState() {
    super.initState();
    final p = context.read<TrafficProvider>();
    _startController = TextEditingController(text: p.startText);
    _endController = TextEditingController(text: p.endText);
  }

  @override
  void dispose() {
    _startController.dispose();
    _endController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.watch<TrafficProvider>();
    final top = MediaQuery.of(context).padding.top;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final surface = Theme.of(context).colorScheme.surface;

    if (_startController.text != p.startText &&
        !_startController.selection.isValid) {
      _startController.text = p.startText;
    }
    if (_endController.text != p.endText && !_endController.selection.isValid) {
      _endController.text = p.endText;
    }

    return Padding(
      padding: EdgeInsets.fromLTRB(12, top + 10, 12, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Material(
            elevation: 8,
            color: surface.withValues(alpha: .975),
            borderRadius: BorderRadius.circular(22),
            child: Container(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(22),
                border: Border.all(
                  color: dark
                      ? Colors.white.withValues(alpha: .08)
                      : Colors.black.withValues(alpha: .06),
                ),
              ),
              child: InkWell(
                borderRadius: BorderRadius.circular(22),
                onTap: () => setState(() => _expanded = !_expanded),
                child: SizedBox(
                  height: 58,
                  child: Row(
                    children: [
                      const SizedBox(width: 14),
                      Container(
                        width: 34,
                        height: 34,
                        decoration: BoxDecoration(
                          color: AppTheme.accentBlue.withValues(alpha: .10),
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(
                          Icons.navigation_rounded,
                          color: AppTheme.accentBlue,
                          size: 20,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          _summary(p),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      Icon(
                        _expanded
                            ? Icons.keyboard_arrow_up_rounded
                            : Icons.keyboard_arrow_down_rounded,
                        color: dark ? Colors.white70 : Colors.black54,
                      ),
                      const SizedBox(width: 2),
                      IconButton(
                        tooltip: 'Profile',
                        onPressed: () => Navigator.push(
                          context,
                          MaterialPageRoute(builder: (_) => const ProfileView()),
                        ),
                        icon: const CircleAvatar(
                          radius: 16,
                          backgroundColor: AppTheme.accentBlue,
                          child: Icon(
                            Icons.person,
                            size: 18,
                            color: Colors.white,
                          ),
                        ),
                      ),
                      const SizedBox(width: 4),
                    ],
                  ),
                ),
              ),
            ),
          ),
          if (_expanded) ...[
            const SizedBox(height: 8),
            Material(
              elevation: 8,
              color: surface.withValues(alpha: .98),
              borderRadius: BorderRadius.circular(20),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
                child: Column(
                  children: [
                    _locationField(
                      controller: _startController,
                      label: 'Start location',
                      hint: 'Current location or any place',
                      icon: Icons.trip_origin,
                      iconColor: AppTheme.accentGreen,
                      onChanged: p.setStartText,
                    ),
                    const SizedBox(height: 8),
                    _locationField(
                      controller: _endController,
                      label: 'Destination',
                      hint: 'Search a place, address or landmark',
                      icon: Icons.location_on,
                      iconColor: AppTheme.accentRed,
                      onChanged: p.setEndText,
                    ),
                    const SizedBox(height: 10),
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: p.isLoading
                                ? null
                                : () async {
                                    final ok =
                                        await p.locateMe(useAsStart: true);
                                    if (ok) {
                                      _startController.text = p.startText;
                                      _startController.selection =
                                          TextSelection.fromPosition(
                                        TextPosition(
                                            offset:
                                                _startController.text.length),
                                      );
                                    }
                                  },
                            icon: const Icon(Icons.my_location, size: 17),
                            label: const Text('Use my location'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: FilledButton.icon(
                            onPressed:
                                p.isLoading ? null : p.calculateRoute,
                            icon: p.isLoading
                                ? const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      color: Colors.white,
                                    ),
                                  )
                                : const Icon(Icons.route_rounded, size: 18),
                            label: const Text('Show route'),
                          ),
                        ),
                      ],
                    ),
                    if (p.error != null) ...[
                      const SizedBox(height: 7),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          p.error!,
                          style: const TextStyle(
                            color: AppTheme.accentRed,
                            fontSize: 11,
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _locationField({
    required TextEditingController controller,
    required String label,
    required String hint,
    required IconData icon,
    required Color iconColor,
    required ValueChanged<String> onChanged,
  }) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return TextField(
      controller: controller,
      onChanged: onChanged,
      textInputAction: TextInputAction.next,
      decoration: InputDecoration(
        prefixIcon: Icon(icon, color: iconColor, size: 20),
        labelText: label,
        hintText: hint,
        suffixIcon: controller.text.isNotEmpty
            ? IconButton(
                tooltip: 'Clear',
                icon: const Icon(Icons.close_rounded, size: 18),
                onPressed: () {
                  controller.clear();
                  onChanged('');
                  setState(() {});
                },
              )
            : null,
        filled: true,
        fillColor:
            dark ? Colors.white.withValues(alpha: .055) : Colors.grey.shade50,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(15),
          borderSide: BorderSide.none,
        ),
      ),
    );
  }

  String _summary(TrafficProvider p) {
    if (p.startText.isEmpty && p.endText.isEmpty) return 'Where do you want to go?';
    if (p.startText.isEmpty) return 'Choose start → ${p.endText}';
    if (p.endText.isEmpty) return '${p.startText} → Choose destination';
    return '${p.startText} → ${p.endText}';
  }
}
