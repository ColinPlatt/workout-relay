const translations = {
  en: {
    languageLabel: "Language", logout: "Log out", heroEyebrow: "Your plan. Your calendar.",
    heroTitle: "Structured workouts, sent from your phone.",
    heroCopy: "Upload a carefully validated JSON plan or connect your training assistant, then send every session to Garmin Connect.",
    trustLine: "Garmin passwords and MFA codes are never stored. Encrypted session tokens keep you connected.",
    login: "Log in", createAccount: "Create account", accountAccess: "Account access", email: "Email",
    password: "Password", passwordHint: "Use at least 12 characters. This is your Workout Relay password.",
    dashboard: "Dashboard", dashboardTitle: "Plan the work. Send it once.", checkingGarmin: "Checking Garmin…",
    tapToManage: "Tap to manage", upload: "Upload", formatGuide: "Plan format", automation: "Automation", settings: "Settings",
    addPlan: "Add a workout plan", chooseJson: "Choose JSON",
    planJson: "Plan JSON", refresh: "Refresh", close: "Close", apiKeyName: "API key name",
    addPlanHelp: "Paste a plan or select a JSON file. Validate it before sending anything to Garmin.",
    jsonPlaceholder: '{ "schema_version": 1, "plan_id": "2026-W40", ... }', loadExample: "Load example",
    validate: "Validate", sendToGarmin: "Send to Garmin", recent: "Recent", planHistory: "Plan history",
    noPlans: "No plans submitted yet.", guideTitle: "A predictable format for reliable workouts",
    guideIntro: "Workout Relay does not ask an AI to interpret your plan at upload time. Every duration, target, repeat, and date is explicit, validated, and deterministic.",
    copyAiInstructions: "Copy AI instructions", viewExample: "View complete example", viewSchema: "View JSON Schema", requiredStructure: "Required structure",
    structureHelp: "A plan contains one or more dated running workouts. Unknown fields are rejected so mistakes are visible.",
    durations: "Durations", durationHelp: "Time is always seconds. Distance is always metres. Values must be positive whole numbers.",
    targets: "Targets", targetHelp: "Use one target per step. Pace is minutes per kilometre; slow must genuinely be slower than fast.",
    repeats: "Repeats", repeatHelp: "A repeat contains 1–5 simple steps. Repeats cannot be nested, and the expanded workout cannot exceed 50 steps.",
    rulesChecklist: "Reliability checklist", ruleDate: "Use a distinct ID for each different workout; reuse that ID when editing or rescheduling it.",
    ruleUnique: "IDs identify a workout across your whole account, not just within one plan.", ruleTitle: "Garmin workout titles contain at most 50 characters.",
    ruleValidate: "Call the validation endpoint before submitting an automatically generated plan.",
    ruleNoMarkdown: "Send raw JSON—not Markdown code fences or explanatory text.", assistantAccess: "Assistant access",
    assistantHelp: "For a custom GPT or your own scripts, create a revocable bearer key. Add it only to the tool's protected API-authentication field—never paste it into a chat. The full key is shown once. Claude cannot use a bearer key: connect it with the connector above.",
    keyNamePlaceholder: "My training assistant", createKey: "Create key", apiWorkflow: "Recommended API workflow",
    connectAssistant: "Connect an assistant", connectorAddress: "Connector address", copyAddress: "Copy address",
    connectAssistantHelp: "Add this address as a connector in Claude or ChatGPT. You sign in here once and approve; the assistant never sees your Garmin or Workout Relay password.",
    inClaude: "In Claude", inChatgpt: "In ChatGPT",
    claudeStep1: "Open Settings, then Connectors, and choose Add custom connector.",
    claudeStep2: "Paste the address above and add it. Leave the advanced OAuth fields empty.",
    claudeStep3: "Adding a connector may need a browser rather than the phone app; once added it works in any chat.",
    chatgptStep1: "Paid plans: open Settings, then Security and login, and turn on Developer mode. Connectors then accept a custom server address.",
    chatgptStep2: "Add the address above as a custom connector and approve the sign-in.",
    chatgptStep3: "Free plans do not offer custom connectors. Use the Upload tab on this site, which needs no connector.",
    connectApprove: "Sign in to Workout Relay and approve the requested permissions. Activity access shares completed workouts and health/run metrics, including heart rate, but no GPS tracks. Existing connections need fresh approval: remove and re-add the connector, then approve activities:read to ask about past runs.",
    activityKeyConsent: "Also allow completed activities and health/run metrics (including heart rate, without GPS). Leave unchecked for plan-only access.",
    connectedAssistants: "Connected assistants", noConnections: "No assistants connected yet.",
    disconnect: "Disconnect", connectionRevoked: "Assistant disconnected.", lastUsed: "Last used {when}", neverUsed: "Not used yet",
    confirmDisconnectAssistant: "Disconnect this assistant? It will no longer be able to send plans or read your activities.",
    apiStep1: "Fetch the format instructions and JSON Schema.", apiStep2: "Generate a plan as raw JSON.",
    apiStep3: "POST it to /api/v1/plans/validate.", apiStep4: "Fix every structured validation error.",
    apiStep5: "POST the valid plan to /api/v1/plans.", openApiDocs: "Open interactive API documentation ↗",
    apiStep6: "Poll the returned submission URL until it is completed or failed.",
    connectGarmin: "Connect Garmin", garminConnected: "Garmin connected",
    connectedHelp: "Encrypted Garmin session tokens are stored so you do not need to sign in for every upload.",
    disconnectGarmin: "Disconnect and delete tokens", beforeConnecting: "Before connecting",
    garminDisclosure: "This uses an unofficial Garmin integration. Your credentials pass through Workout Relay once and are not stored. Encrypted session tokens provide ongoing account access.",
    garminTrustBoundary: "Because the operator controls the hosted server, use this service only if you trust its operator. Encryption protects stored data, but cannot protect against malicious server code.",
    garminEmail: "Garmin email", garminPassword: "Garmin password",
    garminConsent: "I understand this is unofficial and encrypted session tokens will be stored.",
    connectSecurely: "Connect securely", mfaHelp: "Enter the verification code Garmin sent you. This attempt expires after five minutes.",
    verificationCode: "Verification code", verify: "Verify", disclaimer: "Independent software. Not affiliated with Garmin.",
    security: "Security", changePassword: "Change password", currentPassword: "Current password", newPassword: "New password",
    savePassword: "Save new password", dangerZone: "Danger zone", deleteAccount: "Delete account",
    deleteAccountHelp: "Permanently delete your account, plans, API keys, and encrypted Garmin tokens.",
    confirmPassword: "Confirm your password", deleteEverything: "Delete everything", passwordChanged: "Password changed.",
    confirmAccountDeletion: "Permanently delete your account and every stored plan and token? This cannot be undone.",
    garminNotConnected: "Garmin not connected", garminNeedsLogin: "Garmin needs reconnection", mockConnection: "Development mode",
    connectedAs: "Connected as {name}", validPlan: "Valid plan: {count} workout(s).", planCompleted: "Plan sent: {created} created, {updated} updated, {skipped} unchanged.",
    planQueued: "Plan queued. You can leave this page while Workout Relay sends it.",
    stillProcessing: "Still sending to Garmin. Check Plan history in a few minutes.",
    jsonCleaned: "Removed text around the JSON.", copyFailed: "Copy failed. Select the text and copy it manually.",
    invalidJson: "The text is not valid JSON: {detail}", validationFailed: "The plan needs correction:",
    apiKeyOnce: "Copy this key now. It will not be shown again:", revoke: "Revoke", keyCreated: "API key created.",
    copied: "Copied to clipboard.", disconnected: "Garmin tokens deleted.", connected: "Garmin connected.",
    mfaRequired: "Garmin verification is required.", exampleLoaded: "Example loaded.", working: "Working…",
    status_completed: "Completed", status_failed: "Failed", status_processing: "Processing", status_queued: "Queued",
    historyCounts: "{created} created · {updated} updated · {skipped} unchanged",
    confirmDisconnect: "Disconnect Garmin and permanently delete the stored session tokens?",
    error_authentication_required: "Please log in.", error_invalid_credentials: "Incorrect email or password.",
    error_email_exists: "An account with this email already exists.", error_csrf_failed: "Your session expired. Reload and try again.",
    error_browser_session_required: "This security action must be completed in the website.",
    error_invalid_current_password: "The current password is incorrect.",
    error_rate_limited: "Too many attempts. Wait before trying again.", error_garmin_login_failed: "Garmin rejected the login. Check the credentials or try again later.",
    error_garmin_mfa_failed: "The Garmin verification code was rejected. Start the connection again.",
    error_garmin_attempt_expired: "The Garmin login attempt expired. Start again.",
    error_garmin_not_connected: "Connect Garmin before sending a plan.", error_garmin_reauthentication_required: "Garmin requires you to reconnect.",
    error_garmin_upload_failed: "Garmin could not accept the workout. Try again later.", error_internal_upload_error: "The upload could not be completed.",
    error_garmin_outcome_unknown: "Garmin's response was unclear. Check Garmin Connect, then resend the exact same plan to check again. Do not change workout IDs: this could create duplicates. If this persists, contact the operator.",
    error_garmin_prior_upload_unresolved: "A previous version of this workout is unfinished. Resend that exact plan first, keeping its workout IDs, before making changes.",
    error_plan_too_large: "The plan file is too large.", error_default: "Something went wrong. Please try again.",
    schema_required: "{path}: missing required field “{field}”.", schema_type: "{path}: incorrect value type.",
    schema_pattern: "{path}: value has an invalid format.", schema_const: "{path}: expected “{expected}”.",
    schema_additionalProperties: "{path}: contains an unknown field.", schema_oneOf: "{path}: does not match an allowed structure.",
    schema_minimum: "{path}: value is below the minimum {limit}.", schema_maximum: "{path}: value exceeds {limit}.",
    schema_minLength: "{path}: value is too short.", schema_maxLength: "{path}: value is too long.",
    schema_minItems: "{path}: add at least {limit} item(s).", schema_maxItems: "{path}: no more than {limit} item(s) are allowed.",
    schema_format: "{path}: invalid date or format.", duplicate_workout_id: "{path}: workout ID must be unique.",
    invalid_date: "{path}: use a real date in YYYY-MM-DD format.",
    target_order: "{path}: low must be strictly lower than high.", pace_order: "{path}: slow must be a slower pace than fast.",
    expanded_steps: "{path}: repeat expansion creates {count} steps; the maximum is {maximum}."
  },
  fr: {
    languageLabel: "Langue", logout: "Se déconnecter", heroEyebrow: "Votre plan. Votre calendrier.",
    heroTitle: "Des entraînements structurés, envoyés depuis votre téléphone.",
    heroCopy: "Importez un plan JSON soigneusement validé ou connectez votre assistant d'entraînement, puis envoyez chaque séance vers Garmin Connect.",
    trustLine: "Les mots de passe Garmin et codes MFA ne sont jamais conservés. Des jetons de session chiffrés maintiennent la connexion.",
    login: "Se connecter", createAccount: "Créer un compte", accountAccess: "Accès au compte", email: "E-mail",
    password: "Mot de passe", passwordHint: "Utilisez au moins 12 caractères. Il s'agit de votre mot de passe Workout Relay.",
    dashboard: "Tableau de bord", dashboardTitle: "Planifiez. Envoyez une seule fois.", checkingGarmin: "Vérification de Garmin…",
    tapToManage: "Touchez pour gérer", upload: "Importer", formatGuide: "Format du plan", automation: "Automatisation", settings: "Réglages",
    addPlan: "Ajouter un plan d'entraînement", chooseJson: "Choisir un JSON",
    planJson: "JSON du plan", refresh: "Actualiser", close: "Fermer", apiKeyName: "Nom de la clé API",
    addPlanHelp: "Collez un plan ou sélectionnez un fichier JSON. Validez-le avant tout envoi vers Garmin.",
    jsonPlaceholder: '{ "schema_version": 1, "plan_id": "2026-W40", ... }', loadExample: "Charger l'exemple",
    validate: "Valider", sendToGarmin: "Envoyer vers Garmin", recent: "Récent", planHistory: "Historique des plans",
    noPlans: "Aucun plan envoyé pour le moment.", guideTitle: "Un format prévisible pour des séances fiables",
    guideIntro: "Workout Relay ne demande pas à une IA d'interpréter votre plan lors de l'import. Chaque durée, cible, répétition et date est explicite, validée et déterministe.",
    copyAiInstructions: "Copier les instructions IA", viewExample: "Voir l'exemple complet", viewSchema: "Voir le schéma JSON", requiredStructure: "Structure obligatoire",
    structureHelp: "Un plan contient une ou plusieurs séances de course datées. Les champs inconnus sont refusés afin de rendre les erreurs visibles.",
    durations: "Durées", durationHelp: "Le temps est toujours en secondes. La distance est toujours en mètres. Les valeurs sont des nombres entiers positifs.",
    targets: "Cibles", targetHelp: "Utilisez une cible par étape. L'allure est en minutes par kilomètre ; slow doit réellement être plus lent que fast.",
    repeats: "Répétitions", repeatHelp: "Une répétition contient 1 à 5 étapes simples. Elles ne peuvent pas être imbriquées et la séance développée ne peut dépasser 50 étapes.",
    rulesChecklist: "Liste de fiabilité", ruleDate: "Utilisez un identifiant distinct par séance différente ; réutilisez-le pour la modifier ou la déplacer.",
    ruleUnique: "Les identifiants désignent une séance dans tout votre compte, pas seulement dans un plan.", ruleTitle: "Les titres de séance Garmin contiennent au maximum 50 caractères.",
    ruleValidate: "Appelez l'endpoint de validation avant de soumettre un plan généré automatiquement.",
    ruleNoMarkdown: "Envoyez du JSON brut, sans bloc de code Markdown ni texte explicatif.", assistantAccess: "Accès pour assistant",
    assistantHelp: "Pour un GPT personnalisé ou vos propres scripts, créez une clé révocable. Ajoutez-la uniquement au champ d'authentification API protégé de l'outil, jamais dans une conversation. La clé complète n'est affichée qu'une fois. Claude ne peut pas utiliser de clé : connectez-le avec le connecteur ci-dessus.",
    keyNamePlaceholder: "Mon assistant d'entraînement", createKey: "Créer une clé", apiWorkflow: "Flux API recommandé",
    connectAssistant: "Connecter un assistant", connectorAddress: "Adresse du connecteur", copyAddress: "Copier l'adresse",
    connectAssistantHelp: "Ajoutez cette adresse comme connecteur dans Claude ou ChatGPT. Vous vous connectez ici une fois et vous approuvez ; l'assistant ne voit jamais vos mots de passe Garmin ou Workout Relay.",
    inClaude: "Dans Claude", inChatgpt: "Dans ChatGPT",
    claudeStep1: "Ouvrez Réglages, puis Connecteurs, et choisissez Ajouter un connecteur personnalisé.",
    claudeStep2: "Collez l'adresse ci-dessus et ajoutez-la. Laissez vides les champs OAuth avancés.",
    claudeStep3: "L'ajout peut nécessiter un navigateur plutôt que l'application mobile ; une fois ajouté, il fonctionne dans toutes les conversations.",
    chatgptStep1: "Offres payantes : ouvrez Réglages, puis Sécurité et connexion, et activez le mode développeur. Les connecteurs acceptent alors une adresse personnalisée.",
    chatgptStep2: "Ajoutez l'adresse ci-dessus comme connecteur personnalisé et approuvez la connexion.",
    chatgptStep3: "Les offres gratuites ne proposent pas de connecteurs personnalisés. Utilisez l'onglet Importer de ce site, qui n'en demande aucun.",
    connectApprove: "Connectez-vous à Workout Relay et approuvez les autorisations demandées. L'accès aux activités partage vos séances terminées et vos mesures de santé/course, dont la fréquence cardiaque, sans traces GPS. Pour une connexion existante, supprimez puis ajoutez à nouveau le connecteur et approuvez activities:read pour consulter vos courses passées.",
    activityKeyConsent: "Autoriser aussi les activités terminées et les mesures de santé/course (dont la fréquence cardiaque, sans GPS). Laissez décoché pour limiter l'accès aux plans.",
    connectedAssistants: "Assistants connectés", noConnections: "Aucun assistant connecté pour le moment.",
    disconnect: "Déconnecter", connectionRevoked: "Assistant déconnecté.", lastUsed: "Dernière utilisation {when}", neverUsed: "Jamais utilisé",
    confirmDisconnectAssistant: "Déconnecter cet assistant ? Il ne pourra plus envoyer de plans ni lire vos activités.",
    apiStep1: "Récupérer les instructions de format et le schéma JSON.", apiStep2: "Générer le plan en JSON brut.",
    apiStep3: "L'envoyer à /api/v1/plans/validate.", apiStep4: "Corriger chaque erreur de validation structurée.",
    apiStep5: "Envoyer le plan valide à /api/v1/plans.", openApiDocs: "Ouvrir la documentation API interactive ↗",
    apiStep6: "Interroger l'URL de soumission renvoyée jusqu'au statut completed ou failed.",
    connectGarmin: "Connecter Garmin", garminConnected: "Garmin connecté",
    connectedHelp: "Des jetons de session Garmin chiffrés sont conservés afin d'éviter une reconnexion à chaque import.",
    disconnectGarmin: "Déconnecter et supprimer les jetons", beforeConnecting: "Avant la connexion",
    garminDisclosure: "Cette intégration Garmin n'est pas officielle. Vos identifiants transitent une seule fois par Workout Relay et ne sont pas conservés. Des jetons de session chiffrés permettent l'accès ultérieur.",
    garminTrustBoundary: "L'opérateur contrôlant le serveur hébergé, n'utilisez ce service que si vous lui faites confiance. Le chiffrement protège les données stockées, mais pas contre un code serveur malveillant.",
    garminEmail: "E-mail Garmin", garminPassword: "Mot de passe Garmin",
    garminConsent: "Je comprends que cette intégration n'est pas officielle et que des jetons de session chiffrés seront conservés.",
    connectSecurely: "Se connecter en sécurité", mfaHelp: "Saisissez le code de vérification envoyé par Garmin. Cette tentative expire après cinq minutes.",
    verificationCode: "Code de vérification", verify: "Vérifier", disclaimer: "Logiciel indépendant, non affilié à Garmin.",
    security: "Sécurité", changePassword: "Modifier le mot de passe", currentPassword: "Mot de passe actuel", newPassword: "Nouveau mot de passe",
    savePassword: "Enregistrer le nouveau mot de passe", dangerZone: "Zone sensible", deleteAccount: "Supprimer le compte",
    deleteAccountHelp: "Supprimez définitivement votre compte, vos plans, vos clés API et vos jetons Garmin chiffrés.",
    confirmPassword: "Confirmez votre mot de passe", deleteEverything: "Tout supprimer", passwordChanged: "Mot de passe modifié.",
    confirmAccountDeletion: "Supprimer définitivement votre compte ainsi que tous les plans et jetons enregistrés ? Cette action est irréversible.",
    garminNotConnected: "Garmin non connecté", garminNeedsLogin: "Garmin doit être reconnecté", mockConnection: "Mode développement",
    connectedAs: "Connecté en tant que {name}", validPlan: "Plan valide : {count} séance(s).", planCompleted: "Plan envoyé : {created} créée(s), {updated} mise(s) à jour, {skipped} inchangée(s).",
    planQueued: "Plan mis en file d'attente. Vous pouvez quitter cette page pendant l'envoi.",
    stillProcessing: "Envoi vers Garmin toujours en cours. Consultez l'historique dans quelques minutes.",
    jsonCleaned: "Texte autour du JSON supprimé.", copyFailed: "Échec de la copie. Sélectionnez le texte et copiez-le manuellement.",
    invalidJson: "Le texte n'est pas un JSON valide : {detail}", validationFailed: "Le plan doit être corrigé :",
    apiKeyOnce: "Copiez cette clé maintenant. Elle ne sera plus affichée :", revoke: "Révoquer", keyCreated: "Clé API créée.",
    copied: "Copié dans le presse-papiers.", disconnected: "Jetons Garmin supprimés.", connected: "Garmin connecté.",
    mfaRequired: "Une vérification Garmin est nécessaire.", exampleLoaded: "Exemple chargé.", working: "Traitement…",
    status_completed: "Terminé", status_failed: "Échec", status_processing: "En cours", status_queued: "En attente",
    historyCounts: "{created} créée(s) · {updated} mise(s) à jour · {skipped} inchangée(s)",
    confirmDisconnect: "Déconnecter Garmin et supprimer définitivement les jetons de session enregistrés ?",
    error_authentication_required: "Veuillez vous connecter.", error_invalid_credentials: "E-mail ou mot de passe incorrect.",
    error_email_exists: "Un compte existe déjà avec cet e-mail.", error_csrf_failed: "Votre session a expiré. Rechargez la page.",
    error_browser_session_required: "Cette action de sécurité doit être effectuée sur le site web.",
    error_invalid_current_password: "Le mot de passe actuel est incorrect.",
    error_rate_limited: "Trop de tentatives. Patientez avant de réessayer.", error_garmin_login_failed: "Garmin a refusé la connexion. Vérifiez les identifiants ou réessayez plus tard.",
    error_garmin_mfa_failed: "Le code de vérification Garmin a été refusé. Recommencez la connexion.",
    error_garmin_attempt_expired: "La tentative de connexion Garmin a expiré. Recommencez.",
    error_garmin_not_connected: "Connectez Garmin avant d'envoyer un plan.", error_garmin_reauthentication_required: "Garmin demande une nouvelle connexion.",
    error_garmin_upload_failed: "Garmin n'a pas accepté la séance. Réessayez plus tard.", error_internal_upload_error: "L'import n'a pas pu être terminé.",
    error_garmin_outcome_unknown: "La réponse de Garmin est incertaine. Vérifiez Garmin Connect, puis renvoyez exactement le même plan pour vérifier à nouveau. Ne changez pas les identifiants : cela pourrait créer des doublons. Si le problème persiste, contactez l'opérateur.",
    error_garmin_prior_upload_unresolved: "Une version précédente de cette séance est inachevée. Renvoyez d'abord ce plan exact, avec les mêmes identifiants, avant de le modifier.",
    error_plan_too_large: "Le fichier du plan est trop volumineux.", error_default: "Une erreur est survenue. Veuillez réessayer.",
    schema_required: "{path} : le champ « {field} » est obligatoire.", schema_type: "{path} : type de valeur incorrect.",
    schema_pattern: "{path} : format de valeur incorrect.", schema_const: "{path} : la valeur attendue est « {expected} ».",
    schema_additionalProperties: "{path} : contient un champ inconnu.", schema_oneOf: "{path} : ne correspond pas à une structure autorisée.",
    schema_minimum: "{path} : la valeur est inférieure au minimum {limit}.", schema_maximum: "{path} : la valeur dépasse {limit}.",
    schema_minLength: "{path} : la valeur est trop courte.", schema_maxLength: "{path} : la valeur est trop longue.",
    schema_minItems: "{path} : ajoutez au moins {limit} élément(s).", schema_maxItems: "{path} : maximum {limit} élément(s).",
    schema_format: "{path} : date ou format incorrect.", duplicate_workout_id: "{path} : l'identifiant doit être unique.",
    invalid_date: "{path} : utilisez une date réelle au format YYYY-MM-DD.",
    target_order: "{path} : low doit être strictement inférieur à high.", pace_order: "{path} : slow doit être une allure plus lente que fast.",
    expanded_steps: "{path} : les répétitions produisent {count} étapes ; le maximum est {maximum}."
  }
};

const state = { language: "en", authMode: "login", user: null, csrf: null, garmin: null, mfaAttempt: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function interpolate(template, values = {}) {
  return template.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? `{${key}}`);
}
function t(key, values) { return interpolate(translations[state.language][key] || translations.en[key] || key, values); }

function chooseInitialLanguage() {
  const override = localStorage.getItem("workoutRelayLanguage");
  if (["en", "fr"].includes(override)) return override;
  return navigator.languages?.some((lang) => lang.toLowerCase().startsWith("fr")) ? "fr" : "en";
}

function applyLanguage(language, persist = false) {
  state.language = language === "fr" ? "fr" : "en";
  document.documentElement.lang = state.language;
  $("#language").value = state.language;
  $$('[data-i18n]').forEach((element) => { element.textContent = t(element.dataset.i18n); });
  $$('[data-i18n-placeholder]').forEach((element) => { element.placeholder = t(element.dataset.i18nPlaceholder); });
  $$('[data-i18n-aria]').forEach((element) => { element.setAttribute("aria-label", t(element.dataset.i18nAria)); });
  document.title = `Workout Relay — ${t("dashboard")}`;
  if (persist) localStorage.setItem("workoutRelayLanguage", state.language);
  setAuthMode(state.authMode);
  if (state.garmin) renderGarminStatus(state.garmin);
  if (state.user) { loadHistory(); loadKeys(); loadConnections(); }
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|; )workout_relay_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : state.csrf;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD"].includes(options.method || "GET")) {
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  const json = response.headers.get("content-type")?.includes("application/json");
  const body = response.status === 204 ? null : (json ? await response.json() : await response.text());
  if (!response.ok) {
    const detail = body?.detail ?? body;
    const error = new Error(typeof detail === "string" ? detail : detail?.code || "default");
    error.code = typeof detail === "object" ? detail.code : "default";
    error.validationErrors = detail?.errors;
    error.payload = detail;
    throw error;
  }
  return body;
}

function errorText(error) { return t(`error_${error.code || "default"}`); }
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }
function showToast(message) {
  const toast = $("#toast"); toast.textContent = message; toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer); showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 3500);
}
function setBusy(button, busy) {
  if (!button.dataset.original) button.dataset.original = button.textContent;
  button.disabled = busy; button.textContent = busy ? t("working") : button.dataset.original;
  if (!busy && button.dataset.i18n) button.textContent = t(button.dataset.i18n);
}

function setAuthMode(mode) {
  state.authMode = mode;
  $$('.tab').forEach((tab) => tab.classList.toggle("active", tab.dataset.authMode === mode));
  $("#auth-submit").dataset.i18n = mode === "login" ? "login" : "createAccount";
  $("#auth-submit").textContent = t($("#auth-submit").dataset.i18n);
  $("#account-password").autocomplete = mode === "login" ? "current-password" : "new-password";
  $("#auth-error").textContent = "";
}

function showDashboard(user) {
  state.user = user;
  $("#auth-view").classList.add("hidden"); $("#dashboard").classList.remove("hidden"); $("#logout").classList.remove("hidden");
  $("#account-label").textContent = user.email;
  Promise.all([loadGarmin(), loadHistory(), loadKeys(), loadConnections()]);
}
function showAuth() {
  state.user = null;
  $("#dashboard").classList.add("hidden"); $("#auth-view").classList.remove("hidden"); $("#logout").classList.add("hidden");
}

async function loadGarmin() {
  state.garmin = await api("/api/v1/garmin/status"); renderGarminStatus(state.garmin); return state.garmin;
}
function renderGarminStatus(status) {
  const card = $("#garmin-card"); const strong = card.querySelector("strong"); const small = card.querySelector("small");
  card.classList.toggle("connected", status.connected); card.classList.toggle("attention", status.status === "reauthentication_required");
  if (status.connected) {
    strong.textContent = t("garminConnected"); small.textContent = status.mode === "mock" ? t("mockConnection") : t("connectedAs", { name: status.display_name || "Garmin" });
  } else if (status.status === "reauthentication_required") {
    strong.textContent = t("garminNeedsLogin"); small.textContent = t("tapToManage");
  } else { strong.textContent = t("garminNotConnected"); small.textContent = t("tapToManage"); }
}

async function openGarminDialog() {
  const status = await loadGarmin();
  $("#garmin-connected").classList.toggle("hidden", !status.connected);
  $("#garmin-login-form").classList.toggle("hidden", status.connected);
  $("#garmin-mfa-form").classList.add("hidden");
  $("#garmin-name").textContent = status.display_name || "";
  $("#garmin-dialog").showModal();
}

// Assistants usually wrap JSON in a ```json fence or add a sentence around it.
function extractJson(text) {
  const trimmed = text.trim();
  const fenced = trimmed.match(/```[a-z]*\s*\n?([\s\S]*?)```/i);
  let candidate = fenced ? fenced[1].trim() : trimmed;
  const start = candidate.indexOf("{"); const end = candidate.lastIndexOf("}");
  if (start !== -1 && end > start) candidate = candidate.slice(start, end + 1);
  return candidate;
}
function parsePlan() {
  const textarea = $("#plan-json"); const raw = textarea.value;
  const candidate = extractJson(raw);
  let plan;
  try { plan = JSON.parse(candidate); }
  catch (error) { const wrapped = new Error("invalid_json"); wrapped.detail = error.message; throw wrapped; }
  if (candidate !== raw.trim()) { textarea.value = JSON.stringify(plan, null, 2); showToast(t("jsonCleaned")); }
  return plan;
}
function validationText(item) { return t(item.code, { path: item.path, ...(item.params || {}) }); }
function showPlanResult(result, error = false) {
  const box = $("#plan-result"); box.classList.remove("hidden"); box.classList.toggle("error-result", error);
  if (result.validationErrors) {
    box.innerHTML = `<strong>${escapeHtml(t("validationFailed"))}</strong><ul class="validation-list">${result.validationErrors.map((item) => `<li>${escapeHtml(validationText(item))}</li>`).join("")}</ul>`;
  } else { box.textContent = result.message; }
}

async function waitForSubmission(id) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const status = await api(`/api/v1/plans/${id}`);
    if (status.status === "completed") return status;
    if (status.status === "failed") {
      const error = new Error(status.result?.code || "internal_upload_error");
      error.code = status.result?.code || "internal_upload_error";
      throw error;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  return null;
}

async function loadHistory() {
  const data = await api("/api/v1/plans"); const list = $("#history-list");
  if (!data.items.length) { list.innerHTML = `<p class="muted">${escapeHtml(t("noPlans"))}</p>`; return; }
  list.innerHTML = data.items.map((item) => {
    let detail = "";
    if (item.status === "failed") detail = errorText({ code: item.result?.code });
    else if (item.status === "completed" && item.result?.counts) detail = t("historyCounts", item.result.counts);
    return `<div class="history-row"><span class="history-main"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.plan_id)}</small></span><span class="status-pill ${item.status === "failed" ? "failed" : ""}">${escapeHtml(t(`status_${item.status}`))}</span><span>${new Intl.DateTimeFormat(state.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.created_at))}</span>${detail ? `<p class="history-detail ${item.status === "failed" ? "error" : ""}">${escapeHtml(detail)}</p>` : ""}</div>`;
  }).join("");
}

async function loadConnections() {
  $("#connector-url").value = `${window.location.origin}/mcp/`;
  const data = await api("/api/v1/connections");
  const list = $("#connection-list");
  if (!data.items.length) { list.innerHTML = `<p class="muted">${escapeHtml(t("noConnections"))}</p>`; return; }
  const when = (value) => value
    ? t("lastUsed", { when: new Intl.DateTimeFormat(state.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) })
    : t("neverUsed");
  list.innerHTML = data.items.map((item) => `<div class="key-row"><span><strong>${escapeHtml(item.client_name)}</strong><small>${escapeHtml(when(item.last_used_at))}</small></span><button type="button" data-connection-id="${escapeHtml(item.id)}">${escapeHtml(t("disconnect"))}</button></div>`).join("");
  list.querySelectorAll("button").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(t("confirmDisconnectAssistant"))) return;
    await api(`/api/v1/connections/${button.dataset.connectionId}`, { method: "DELETE" });
    showToast(t("connectionRevoked"));
    await loadConnections();
  }));
}

async function loadKeys() {
  const data = await api("/api/v1/api-keys"); const list = $("#key-list");
  list.innerHTML = data.items.map((item) => `<div class="key-row"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.prefix)}…</small></span><button type="button" data-key-id="${item.id}">${escapeHtml(t("revoke"))}</button></div>`).join("");
  list.querySelectorAll("button").forEach((button) => button.addEventListener("click", async () => { await api(`/api/v1/api-keys/${button.dataset.keyId}`, { method: "DELETE" }); await loadKeys(); }));
}

$$('.tab').forEach((tab) => tab.addEventListener("click", () => setAuthMode(tab.dataset.authMode)));
$$('.section-tab').forEach((tab) => tab.addEventListener("click", () => {
  $$('.section-tab').forEach((other) => other.classList.toggle("active", other === tab));
  $$('.dashboard-section').forEach((section) => section.classList.add("hidden"));
  $(`#section-${tab.dataset.section}`).classList.remove("hidden");
}));

$("#language").addEventListener("change", async (event) => {
  applyLanguage(event.target.value, true);
  if (state.user) {
    try { state.user = await api("/api/v1/me/language", { method: "PUT", body: JSON.stringify({ language: state.language }) }); }
    catch (_) { /* Local override still works when the preference request fails. */ }
  }
});

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = $("#auth-submit"); setBusy(button, true); $("#auth-error").textContent = "";
  try {
    const data = await api(`/api/v1/auth/${state.authMode}`, { method: "POST", body: JSON.stringify({ email: $("#account-email").value, password: $("#account-password").value }) });
    state.csrf = data.csrf_token; showDashboard(data.user);
  } catch (error) { $("#auth-error").textContent = errorText(error); }
  finally { setBusy(button, false); }
});
$("#logout").addEventListener("click", async () => { await api("/api/v1/auth/logout", { method: "POST" }); showAuth(); });

$("#garmin-card").addEventListener("click", openGarminDialog);
$("#close-garmin").addEventListener("click", () => $("#garmin-dialog").close());
$("#garmin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button[type=submit]"); setBusy(button, true); $("#garmin-login-error").textContent = "";
  try {
    const result = await api("/api/v1/garmin/connect/start", { method: "POST", body: JSON.stringify({ email: $("#garmin-email").value, password: $("#garmin-password").value }) });
    $("#garmin-password").value = "";
    if (result.status === "mfa_required") {
      state.mfaAttempt = result.attempt_id; $("#garmin-login-form").classList.add("hidden"); $("#garmin-mfa-form").classList.remove("hidden"); showToast(t("mfaRequired"));
    } else { showToast(t("connected")); $("#garmin-dialog").close(); await loadGarmin(); }
  } catch (error) { $("#garmin-password").value = ""; $("#garmin-login-error").textContent = errorText(error); }
  finally { setBusy(button, false); }
});
$("#garmin-mfa-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button[type=submit]"); setBusy(button, true); $("#garmin-mfa-error").textContent = "";
  try {
    await api("/api/v1/garmin/connect/complete", { method: "POST", body: JSON.stringify({ attempt_id: state.mfaAttempt, code: $("#garmin-mfa").value }) });
    $("#garmin-mfa").value = ""; state.mfaAttempt = null; showToast(t("connected")); $("#garmin-dialog").close(); await loadGarmin();
  } catch (error) { $("#garmin-mfa").value = ""; $("#garmin-mfa-error").textContent = errorText(error); }
  finally { setBusy(button, false); }
});
$("#disconnect-garmin").addEventListener("click", async () => {
  if (!confirm(t("confirmDisconnect"))) return;
  await api("/api/v1/garmin/connection", { method: "DELETE" }); $("#garmin-dialog").close(); showToast(t("disconnected")); await loadGarmin();
});

$("#plan-file").addEventListener("change", async (event) => { const file = event.target.files[0]; if (file) $("#plan-json").value = await file.text(); });
$("#load-example").addEventListener("click", async () => { $("#plan-json").value = JSON.stringify(await api("/api/v1/plan-example"), null, 2); showToast(t("exampleLoaded")); });
$("#validate-plan").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true);
  try { const result = await api("/api/v1/plans/validate", { method: "POST", body: JSON.stringify(parsePlan()) }); showPlanResult({ message: t("validPlan", { count: result.workout_count }) }); }
  catch (error) { showPlanResult(error.message === "invalid_json" ? { message: t("invalidJson", { detail: error.detail }) } : error.validationErrors ? error : { message: errorText(error) }, true); }
  finally { setBusy(button, false); }
});
$("#upload-plan").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true);
  try {
    const queued = await api("/api/v1/plans", { method: "POST", body: JSON.stringify(parsePlan()) });
    showPlanResult({ message: t("planQueued") }); await loadHistory();
    const result = await waitForSubmission(queued.id);
    showPlanResult({ message: result ? t("planCompleted", result.result.counts) : t("stillProcessing") }); await loadHistory();
  } catch (error) { showPlanResult(error.message === "invalid_json" ? { message: t("invalidJson", { detail: error.detail }) } : error.validationErrors ? error : { message: errorText(error) }, true); }
  finally { setBusy(button, false); }
});
$("#refresh-history").addEventListener("click", loadHistory);

async function instructionsText() {
  const [info, example] = await Promise.all([api(`/api/v1/plan-format?language=${state.language}`), api("/api/v1/plan-example")]);
  const endpoints = `Authentication: ${info.authentication}\nSchema: ${info.schema_url}\nExample: ${info.example_url}\nValidate: POST ${info.validate_url}\nSubmit: POST ${info.submit_url}\nStatus: GET ${info.status_url_template}`;
  return `${info.purpose}\n\n${info.rules.map((rule) => `- ${rule}`).join("\n")}\n\n${endpoints}\n\nExample JSON:\n${JSON.stringify(example, null, 2)}`;
}
// Safari only allows clipboard writes started synchronously inside the tap, so
// hand it a ClipboardItem whose content resolves after the network requests.
function copyText(textPromise) {
  if (window.ClipboardItem && navigator.clipboard?.write) {
    const blob = textPromise.then((text) => new Blob([text], { type: "text/plain" }));
    return navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
  }
  return textPromise.then((text) => navigator.clipboard.writeText(text));
}
$("#copy-instructions").addEventListener("click", async () => {
  try { await copyText(instructionsText()); showToast(t("copied")); }
  catch (_) { showToast(t("copyFailed")); }
});
$("#copy-connector").addEventListener("click", async () => {
  try { await copyText(Promise.resolve(`${window.location.origin}/mcp/`)); showToast(t("copied")); }
  catch (_) { showToast(t("copyFailed")); }
});
$("#create-key").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  setBusy(button, true);
  try {
    const scopes = ["plans:read", "plans:write"];
    if ($("#key-activities").checked) scopes.push("activities:read");
    const result = await api("/api/v1/api-keys", { method: "POST", body: JSON.stringify({ name: $("#key-name").value || t("keyNamePlaceholder"), scopes }) });
    $("#key-activities").checked = false;
    const box = $("#key-result"); box.textContent = `${t("apiKeyOnce")}\n\n${result.token}`; box.classList.remove("hidden"); showToast(t("keyCreated")); await loadKeys();
  } catch (error) { showToast(errorText(error)); }
  finally { setBusy(button, false); }
});

$("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button"); setBusy(button, true); $("#password-error").textContent = "";
  try {
    const result = await api("/api/v1/me/password", { method: "POST", body: JSON.stringify({ current_password: $("#current-password").value, new_password: $("#new-password").value }) });
    state.csrf = result.csrf_token; $("#current-password").value = ""; $("#new-password").value = ""; showToast(t("passwordChanged"));
  } catch (error) { $("#password-error").textContent = errorText(error); }
  finally { setBusy(button, false); }
});
$("#delete-account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!confirm(t("confirmAccountDeletion"))) return;
  const button = event.currentTarget.querySelector("button"); setBusy(button, true); $("#delete-error").textContent = "";
  try {
    await api("/api/v1/account", { method: "DELETE", body: JSON.stringify({ password: $("#delete-password").value }) });
    localStorage.removeItem("workoutRelayLanguage"); showAuth();
  } catch (error) { $("#delete-error").textContent = errorText(error); }
  finally { setBusy(button, false); }
});

(async function boot() {
  applyLanguage(chooseInitialLanguage());
  try {
    const user = await api("/api/v1/me");
    if (!localStorage.getItem("workoutRelayLanguage") && user.preferred_language) applyLanguage(user.preferred_language);
    showDashboard(user);
  } catch (_) { showAuth(); }
})();
