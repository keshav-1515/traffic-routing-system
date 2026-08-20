class RewardItem {
  final String id;
  final String title;
  final String description;
  final int costCredits;
  final String category;
  final String icon;

  const RewardItem({
    required this.id,
    required this.title,
    required this.description,
    required this.costCredits,
    required this.category,
    this.icon = '🎁',
  });

  factory RewardItem.fromJson(Map<String, dynamic> json) => RewardItem(
    id: (json['id'] ?? '').toString(),
    title: (json['title'] ?? '').toString(),
    description: (json['description'] ?? '').toString(),
    costCredits: ((json['cost_credits'] ?? 0) as num).toInt(),
    category: (json['category'] ?? 'Reward').toString(),
    icon: (json['icon'] ?? '🎁').toString(),
  );
}
