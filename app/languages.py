from typing import Dict, List, Tuple

SUPPORTED_LANGUAGES: List[str] = [
    "en",
    "es",
    "pt",
    "fr",
    "de",
    "it",
    "ru",
    "tr",
    "ar",
    "hi",
    "bn",
    "ur",
    "id",
    "ja",
    "zh-Hans",
]

LANGUAGE_LABELS: Dict[str, str] = {
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "ru": "Русский",
    "tr": "Türkçe",
    "ar": "العربية",
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "ur": "اردو",
    "id": "Bahasa Indonesia",
    "ja": "日本語",
    "zh-Hans": "简体中文",
}

LANGUAGE_MODE_LABELS: Dict[str, str] = {
    "clean": "Clean",
    "adult": "Adult",
    "unrestricted": "Unrestricted",
}

LANGUAGE_MODE_DESCRIPTIONS: Dict[str, str] = {
    "clean": "No profanity or explicit sexual content.",
    "adult": "Allow adult language and sexual content when relevant.",
    "unrestricted": "Allow strong language; still avoid harassment, threats, or targeted hate.",
}


def is_supported_language(code: str) -> bool:
    return code in SUPPORTED_LANGUAGES


def language_options() -> List[Tuple[str, str]]:
    return [(code, LANGUAGE_LABELS.get(code, code)) for code in SUPPORTED_LANGUAGES]
