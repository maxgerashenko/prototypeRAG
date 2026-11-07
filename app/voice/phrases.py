"""Fixed spoken lines and farewell words per language (#238).

The LLM answers in the caller's language, but lines the code speaks itself (re-prompt, silence
goodbye, error and give-up lines) and the farewell regex used to be English only. Scope for the
pilot: English plus the languages named in STT_ALTERNATIVE_LANGUAGES examples (Spanish, Russian).
Lines follow `VOICE_LANGUAGE` (its primary subtag); a language not listed falls back to English.
Per-turn detected language is not used: it would need the STT result's language per turn.
The AI disclosure in the greeting stays English (legal wording, DEC-17/DEC-53: needs sign-off).
"""

import re

from app.config import get_settings

LINES: dict[str, dict[str, str]] = {
    "en": {
        "reprompt": "Are you still there?",
        "silence_goodbye": "I haven't heard anything, so I'll end the call now. Thanks for calling, goodbye.",
        "give_up": "Sorry, I couldn't find that. Can I take a message?",
        "give_up_done": "Sorry, I couldn't finish everything, but what you asked for before has gone through.",
        "trouble": "Sorry, I'm having trouble. Can I take a message?",
    },
    "es": {
        "reprompt": "¿Sigue ahí?",
        "silence_goodbye": "No he oído nada, así que voy a terminar la llamada. Gracias por llamar, adiós.",
        "give_up": "Lo siento, no he podido encontrarlo. ¿Quiere dejar un mensaje?",
        "give_up_done": "Lo siento, no pude terminar todo, pero lo que pidió antes ya está hecho.",
        "trouble": "Lo siento, tengo problemas. ¿Quiere dejar un mensaje?",
    },
    "ru": {
        "reprompt": "Вы ещё на линии?",
        "silence_goodbye": "Я ничего не слышу, поэтому завершаю звонок. Спасибо за звонок, до свидания.",
        "give_up": "Извините, я не смог это найти. Хотите оставить сообщение?",
        "give_up_done": "Извините, я не смог закончить всё, но то, о чём вы просили раньше, уже сделано.",
        "trouble": "Извините, у меня неполадки. Хотите оставить сообщение?",
    },
}

# Same shape as the English regex in session.py: the whole last sentence is a farewell
FAREWELL_PATTERNS: dict[str, re.Pattern] = {
    "es": re.compile(
        r"\b(?:adi[oó]s|hasta (?:luego|pronto|la vista)|nos vemos|eso es todo|es todo)"
        r"(?:,? (?:gracias|muchas gracias))?$",
        re.IGNORECASE,
    ),
    "ru": re.compile(
        r"(?<!\w)(?:до свидания|до встречи|пока|всего доброго|всего хорошего|это всё|это все)"
        r"(?:,? (?:спасибо|большое спасибо))?$",
        re.IGNORECASE,
    ),
}


def language() -> str:
    return get_settings().voice_language.split("-")[0].lower()


def line(key: str) -> str:
    return LINES.get(language(), LINES["en"])[key]
