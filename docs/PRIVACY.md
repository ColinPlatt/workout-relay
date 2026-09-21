# Privacy / Confidentialité

## English

Workout Relay is a personal tool. It stores what it needs to send your plans to
Garmin, and nothing else. There is no analytics, advertising or third-party
tracking, and no data is shared with anyone except Garmin, at your instruction.

### Why there is no cookie banner

Two cookies are set: a signed-in session cookie and a CSRF cookie. Both are
strictly necessary to provide a service you explicitly asked for, which is the
[exemption CNIL describes](https://www.cnil.fr/fr/cookies-et-autres-traceurs/que-dit-la-loi),
so neither requires consent. Your language choice is kept in this browser's
local storage, set by your own action, and never leaves the device.

A consent banner for those would be theatre. What genuinely needs a decision is
where your **Garmin session** may live, and that question is asked at the moment
it applies — when you connect Garmin — not in a pop-up on arrival.

### Your Garmin session: the choice

**This visit only.** The session tokens are held encrypted in the
server's memory and expire after a fixed window, enforced by the server rather
than by guessing when your browser closed. Nothing Garmin-related is written to
the database. A restart or deploy also ends the session. You sign in to Garmin
again next time, and an assistant cannot send plans once the window closes.

**Keep Garmin connected** (pre-selected). The session tokens are encrypted and
stored until you disconnect, so uploads work later and an assistant can send
plans unattended. This is offered first because sending plans later, without
signing in again, is what most people come here for; switch to the other option
if you would rather nothing Garmin-related were stored.

Either way, your Garmin email, password and MFA codes are never stored. They
pass through the server once to obtain the session, and are discarded. To change
the choice, disconnect Garmin and connect again.

### What is stored

| Data | Why | How long |
|---|---|---|
| Account email, Argon2id password hash | Sign-in | Until you delete the account |
| Plans you send, and their results | Showing history, avoiding duplicate uploads | `PLAN_RETENTION_DAYS`, then deleted automatically |
| Encrypted Garmin session tokens | Sending plans without a fresh Garmin login | Until you disconnect — **only** under "Keep Garmin connected" |
| Hashed API keys, assistant connections | Letting your tools act for you | Until revoked |
| A hashed record of each signed-in browser session | Keeping you signed in | `SESSION_DAYS`, then deleted |
| Your language choice | Showing the interface in French or English | With the account |
| Audit events (what happened, never contents) | Security, and showing you what an assistant did | With the account |

The exact periods in force are served at `/api/v1/policy` and shown in the
privacy dialog, so what you read is the configuration rather than prose that
could drift from it.

### Activity data

Reading completed activities requires a separate `activities:read` permission
you grant explicitly when connecting an assistant.

The measurements themselves — heart rate, pace, distance and the rest — are
fetched from Garmin on request, passed to the assistant, and not stored here.

What *is* stored: the **IDs** of the activities shown to an assistant, together
with your Garmin account name and a timestamp, for 24 hours. That record is
what confines an assistant to activities it was actually shown in your own
listing, instead of letting it ask for arbitrary IDs. It holds no measurements.
It is deleted when it ages out, when you disconnect Garmin, or with the account.

### Clearing plan history

**Clear history** under Plans deletes your finished submissions — the plan
contents and their results — at any time, without waiting for `PLAN_RETENTION_DAYS`
to expire. Nothing in the service depends on that history: duplicate prevention
uses the workout links described below, not the submissions. Two things it deliberately does not touch: workouts already sent to
Garmin, which live in your Garmin account and are yours to delete there, and
the small record of which workout id became which Garmin workout. That record
holds no plan content, and removing it would make the next send create a second
copy of every workout instead of updating the existing one.

### Your rights

Access and erasure are built in: everything above is visible in the interface,
and deleting your account removes it. Disconnecting an assistant ends its access
immediately. The operator controls the server, so use it only if you trust them
— encryption protects stored data and backups, not against malicious server code.

## Français

Workout Relay est un outil personnel. Il ne conserve que ce qui est nécessaire
pour envoyer vos plans vers Garmin. Aucune analyse d'audience, publicité ni
traceur tiers ; aucune donnée n'est partagée, sauf avec Garmin, sur votre ordre.

### Pourquoi il n'y a pas de bannière cookies

Deux cookies sont déposés : celui de session et celui de protection CSRF. Tous
deux sont strictement nécessaires à un service que vous avez expressément
demandé, ce qui correspond à l'exemption décrite par la CNIL : leur dépôt ne
requiert pas de consentement. Votre choix de langue reste dans le stockage local
de ce navigateur et ne quitte jamais l'appareil.

Le vrai choix porte sur la conservation de votre **session Garmin**, et il est
posé au moment où il s'applique : lors de la connexion à Garmin.

### Votre session Garmin : le choix

**Cette visite uniquement.** Les jetons sont conservés chiffrés en
mémoire et expirent après une fenêtre fixe, appliquée par le serveur. Rien
concernant Garmin n'est écrit en base. Un redémarrage met également fin à la
session. Vous vous reconnecterez à Garmin la prochaine fois, et un assistant ne
peut plus envoyer de plans une fois la fenêtre fermée.

**Rester connecté à Garmin** (pré-sélectionné). Les jetons chiffrés sont conservés jusqu'à votre
déconnexion : les imports fonctionnent plus tard et un assistant peut envoyer
des plans sans vous.

Dans les deux cas, votre e-mail, votre mot de passe et vos codes MFA Garmin ne
sont jamais conservés. Pour changer de choix, déconnectez puis reconnectez.

### Données d'activité

Les mesures (fréquence cardiaque, allure, distance) sont récupérées auprès de
Garmin à la demande, transmises à l'assistant, et ne sont pas conservées ici.
Sont en revanche conservés 24 heures : les identifiants des activités montrées
à un assistant, le nom de votre compte Garmin et un horodatage. Ce registre
limite l'assistant aux activités qui lui ont été présentées ; il ne contient
aucune mesure.

« Effacer l'historique » supprime à tout moment vos envois terminés, sans
attendre la période de conservation. Les durées exactes sont indiquées dans la
fenêtre de confidentialité.

### Vos droits

L'accès et l'effacement sont intégrés : tout est visible dans l'interface, et la
suppression du compte efface l'ensemble. Déconnecter un assistant met fin à son
accès immédiatement. L'opérateur contrôle le serveur : n'utilisez ce service que
si vous lui faites confiance.
