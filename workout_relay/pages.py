"""Small public pages and shared language selection; no authentication required."""

from html import escape
from urllib.parse import urlencode

LANGUAGE_COOKIE = "workout_relay_language"


def page_language(request, explicit=None):
    for choice in (explicit, request.query_params.get("lang"), request.cookies.get(LANGUAGE_COOKIE)):
        if choice in ("en", "fr"):
            return choice
    preferred = []
    for order, entry in enumerate(request.headers.get("accept-language", "").split(",")):
        parts = entry.strip().lower().split(";")
        language = parts[0].split("-")[0]
        try:
            quality = next((float(part.strip()[2:]) for part in parts[1:] if part.strip().startswith("q=")), 1)
        except ValueError:
            continue
        if language in ("en", "fr") and 0 < quality <= 1:
            preferred.append((quality, -order, language))
    return max(preferred)[2] if preferred else "en"


# Drawn rather than written with emoji: flag emoji render as bare letters on
# Windows and on some Android builds, which is precisely where a reader who
# needs the switch is most likely to be.
FLAG_GB = (
    '<svg class="flag" viewBox="0 0 60 40" aria-hidden="true" focusable="false">'
    '<clipPath id="{p}gb"><rect width="60" height="40" rx="5"/></clipPath>'
    '<g clip-path="url(#{p}gb)">'
    '<rect width="60" height="40" fill="#012169"/>'
    '<path d="M0 0l60 40M60 0L0 40" stroke="#fff" stroke-width="9"/>'
    '<path d="M0 0l60 40M60 0L0 40" stroke="#C8102E" stroke-width="4"/>'
    '<path d="M30 0v40M0 20h60" stroke="#fff" stroke-width="13"/>'
    '<path d="M30 0v40M0 20h60" stroke="#C8102E" stroke-width="7"/>'
    '</g></svg>'
)
FLAG_FR = (
    '<svg class="flag" viewBox="0 0 60 40" aria-hidden="true" focusable="false">'
    '<clipPath id="{p}fr"><rect width="60" height="40" rx="5"/></clipPath>'
    '<g clip-path="url(#{p}fr)">'
    '<rect width="20" height="40" fill="#002395"/>'
    '<rect x="20" width="20" height="40" fill="#fff"/>'
    '<rect x="40" width="20" height="40" fill="#ED2939"/>'
    '</g></svg>'
)


def language_links(path, language, **params):
    links = []
    for code, flag, label in (("en", FLAG_GB, "English"), ("fr", FLAG_FR, "Français")):
        url = escape(path + "?" + urlencode({**params, "lang": code}), quote=True)
        current = ' aria-current="true"' if language == code else ""
        links.append(f'<a href="{url}" lang="{code}" hreflang="{code}"{current}>'
                     f'{flag.format(p=code)} {label}</a>')
    label = "Langue" if language == "fr" else "Language"
    return f'<nav class="language-choices" aria-label="{label}">{"".join(links)}</nav>'


ABOUT = {
    "en": {
        "eyebrow": "Chat. Run. Repeat.", "title": "Your next run starts with a conversation.",
        "intro": "Plan with Claude or ChatGPT. Train with Garmin. Bring the results back to your chat.",
        "steps": [
            ("Plan your week", "Tell your connected assistant your goal and the days you can run. Ask it to create a running plan and send it through Workout Relay."),
            ("Put it on your calendar", "Workout Relay checks the plan and schedules each session in Garmin Connect, days or weeks ahead. Ask your assistant to edit the same sessions when your plans change."),
            ("Sync, then run", "Sync a compatible Garmin device with Garmin Connect to download upcoming workouts. Follow the steps on your watch, then sync again to upload your completed activity."),
            ("Review and adjust", "After syncing, ask your chat to review your latest run. With activity access enabled, it can retrieve available pace, distance, duration and heart-rate metrics, give feedback and propose changes to your next sessions."),
        ],
        "note": "No workout files to export for your chat. Retrieval happens when the assistant calls the tools; Workout Relay does not start chats or send automatic post-run feedback.",
        "cta": "Get started", "details": "A few things to know",
        "limits": "You need a Garmin device that supports the workouts you schedule and an assistant account with custom-connector access. Availability depends on the provider and your account. You can also upload a plan file without a connector.",
        "session": "Keep Garmin connected for access between visits. With This visit only, reconnect when the temporary session expires. Activity access is a separate choice.",
        "disclaimer": "Independent software, not affiliated with Garmin. Workout Relay uses an unofficial Garmin Connect integration.",
        "sources": "Connection and device guidance", "back": "Workout Relay",
    },
    "fr": {
        "eyebrow": "Discutez. Courez. Recommencez.", "title": "Votre prochaine course commence par une conversation.",
        "intro": "Planifiez avec Claude ou ChatGPT. Entraînez-vous avec Garmin. Retrouvez vos résultats dans la conversation.",
        "steps": [
            ("Planifiez votre semaine", "Indiquez à votre assistant connecté votre objectif et vos jours disponibles. Demandez-lui de créer un plan de course et de l'envoyer via Workout Relay."),
            ("Remplissez votre calendrier", "Workout Relay vérifie le plan et programme chaque séance dans Garmin Connect, des jours ou des semaines à l'avance. Si vos projets changent, demandez à l'assistant de modifier les mêmes séances."),
            ("Synchronisez, puis courez", "Synchronisez un appareil Garmin compatible avec Garmin Connect pour récupérer les séances à venir. Suivez les étapes sur votre montre, puis synchronisez à nouveau pour transférer l'activité terminée."),
            ("Analysez et ajustez", "Après la synchronisation, demandez à votre assistant d'analyser votre dernière course. Avec l'accès aux activités, il peut récupérer les mesures disponibles d'allure, distance, durée et fréquence cardiaque, puis proposer des ajustements pour vos prochaines séances."),
        ],
        "note": "Aucun fichier d'activité à exporter pour votre chat. La récupération se fait lorsque l'assistant appelle les outils ; Workout Relay ne lance pas de conversation ni de bilan automatique après une course.",
        "cta": "Commencer", "details": "Bon à savoir",
        "limits": "Il vous faut un appareil Garmin compatible avec vos séances et un compte d'assistant donnant accès aux connecteurs personnalisés. La disponibilité dépend du fournisseur et de votre compte. Vous pouvez aussi importer un fichier de plan sans connecteur.",
        "session": "Gardez Garmin connecté pour un accès entre vos visites. Avec Cette visite uniquement, reconnectez-vous à l'expiration de la session temporaire. L'accès aux activités est un choix distinct.",
        "disclaimer": "Logiciel indépendant, non affilié à Garmin. Workout Relay utilise une intégration Garmin Connect non officielle.",
        "sources": "Aide sur les connexions et les appareils", "back": "Workout Relay",
    },
}


def about_html(language):
    text = ABOUT[language]
    steps = "".join(f'<li><span class="workflow-number" aria-hidden="true">{number:02}</span>'
                    f'<div><h2>{escape(title)}</h2><p>{escape(body)}</p></div></li>'
                    for number, (title, body) in enumerate(text["steps"], 1))
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="description" content="{escape(text["intro"], quote=True)}">'
        '<title>Workout Relay — About / À propos</title><link rel="stylesheet" href="/static/styles.css">'
        '</head><body class="about-page"><header class="topbar">'
        f'<a class="brand" href="/?lang={language}"><img class="brand-icon" src="/static/runner.svg" alt="" width="38" height="38">'
        '<span>Workout Relay</span></a>' + language_links("/about", language) + '</header><main>'
        f'<section class="about-hero"><p class="eyebrow">{escape(text["eyebrow"])}</p>'
        f'<h1>{escape(text["title"])}</h1><p class="hero-copy">{escape(text["intro"])}</p>'
        f'<a class="button primary" href="/?lang={language}">{escape(text["cta"])}</a></section>'
        f'<ol class="workflow-cards">{steps}</ol><p class="workflow-note">{escape(text["note"])}</p>'
        f'<details class="disclosure"><summary>{escape(text["details"])}</summary>'
        f'<p>{escape(text["limits"])}</p><p>{escape(text["session"])}</p>'
        f'<p>{escape(text["disclaimer"])}</p><p>{escape(text["sources"])}: '
        '<a href="https://developers.openai.com/plugins/deploy/connect-chatgpt">ChatGPT</a> · '
        '<a href="https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp">Claude</a> · '
        '<a href="https://support.garmin.com/en-IN/?faq=XRcMvEtKdf7yBf8My9jua6">Garmin</a>'
        '</p></details></main><footer>Workout Relay</footer></body></html>'
    )
