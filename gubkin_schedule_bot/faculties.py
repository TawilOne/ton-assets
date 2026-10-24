"""Факультеты в боте и в мини-играх: ключ, слова для поиска на сайте (hint) и названия кнопок."""

# Факультеты в боте: hint — слова из названия факультета на сайте (должны быть ВСЕ — так «химическ»
# не спутается с «нефтехимической»), по ним ноутбук находит факультет; дальше — названия кнопок.
# Чтобы добавить факультет — допишите строку; ноутбук сам скачает его группы при следующем sync.
FACULTIES = [
    {"key": "geo", "hint": ["геолог"],
     "ru": "⛏ Геологии и геофизики", "en": "⛏ Geology & Geophysics", "fr": "⛏ Géologie et géophysique"},
    {"key": "aivt", "hint": ["автоматик", "вычислительн"],
     "ru": "💻 Автоматики и ВТ", "en": "💻 Automation & Computing", "fr": "💻 Automatique et informatique"},
    {"key": "chem", "hint": ["химическ", "технолог", "эколог"],
     "ru": "🧪 Химической технологии и экологии", "en": "🧪 Chemical Technology & Ecology",
     "fr": "🧪 Génie chimique et écologie"},
    {"key": "econ", "hint": ["экономик", "управлен"],
     "ru": "📈 Экономики и управления", "en": "📈 Economics & Management", "fr": "📈 Économie et gestion"},
    {"key": "oil", "hint": ["разработк", "месторожден"],
     "ru": "🛢 Разработки нефтяных и газовых месторождений", "en": "🛢 Oil & Gas Field Development",
     "fr": "🛢 Exploitation des gisements de pétrole et de gaz"},
    {"key": "pipe", "hint": ["трубопровод"],
     "ru": "🔧 Трубопроводного транспорта", "en": "🔧 Pipeline Transport", "fr": "🔧 Transport par pipelines"},
    {"key": "mech", "hint": ["инженерн", "механик"],
     "ru": "⚙️ Инженерной механики", "en": "⚙️ Engineering Mechanics", "fr": "⚙️ Mécanique de l'ingénieur"},
    {"key": "safety", "hint": ["комплексн", "безопасност"],
     "ru": "🦺 Комплексной безопасности ТЭК", "en": "🦺 Integrated Energy Sector Safety",
     "fr": "🦺 Sécurité intégrée du secteur énergétique"},
    {"key": "intl", "hint": ["международн", "бизнес"],
     "ru": "🌍 Международного энергетического бизнеса", "en": "🌍 International Energy Business",
     "fr": "🌍 Commerce international de l'énergie"},
    {"key": "hum", "hint": ["гуманитарн"],
     "ru": "📚 Гуманитарного образования", "en": "📚 Humanities", "fr": "📚 Sciences humaines"},
]
DEFAULT_FACULTY = "geo"  # факультет группы по умолчанию (GROUP_CODE)
