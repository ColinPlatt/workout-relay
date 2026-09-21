# Security model / Modèle de sécurité

## English

Workout Relay uses Garmin's unofficial Connect endpoints. This is a
**trust-based hosted integration**, not delegated OAuth.

### Where Garmin tokens live

The person chooses when connecting. Under **this visit only** the encrypted
tokens are held in process memory with a server-enforced expiry that refreshing
does not extend, and the database row holds no ciphertext at all; a restart ends
the session. Under **keep connected** the encrypted tokens are stored until the
person disconnects. The choice is recorded in the audit log, which is what
demonstrates consent. Connections made before this release keep the arrangement
they were made under. [docs/PRIVACY.md](PRIVACY.md) is the user-facing notice.

### What is stored

- Workout Relay account email and a salted Argon2id password hash.
- Submitted workout plans and redacted upload results.
- Activity IDs observed in the user's own listing, Garmin account binding and
  observation time, to restrict detail access. Authorization expires after 24
  hours; these records are removed when Garmin is disconnected or the account
  is deleted. No completed-activity metric responses are persisted.
- Hashed Workout Relay API keys. Full keys are displayed only once.
- An authenticated-encryption ciphertext containing Garmin access and refresh
  tokens.

### What is not stored

- Garmin email and password.
- Garmin MFA codes.
- Plaintext Garmin tokens.
- Credential request bodies in application logs or audit events.

Garmin credentials necessarily exist briefly in application memory while the
server exchanges them for session tokens. The operator controls the production
application and could theoretically deploy code that captures them. Do not
claim that the operator is technically unable to access Garmin accounts.

The live login endpoint contains no analytics or third-party scripts, sends
`Cache-Control: no-store`, and keeps unfinished MFA sessions in process memory
for at most five minutes. A restart invalidates an unfinished attempt.

### Production requirements

- HTTPS only; secure cookies are enforced by production configuration.
- A randomly generated `MASTER_ENCRYPTION_KEY` stored in a platform secret
  manager, never committed or placed in the database.
- Request-body capture disabled at the load balancer, WAF, APM, error tracker,
  and application logger for `/api/v1/garmin/connect/*`.
- Encrypted database backups and restricted production access.
- Rate limiting shared across instances at the ingress layer.
- One application worker unless MFA state is moved to a dedicated auth broker.
- Regular dependency updates and restore tests.

Garmin session tokens are broad, undocumented credentials and must be treated
as password-equivalent secrets. Database encryption protects backups and direct
database access; it does not protect against malicious production code.

`activities:read` is a separate Workout Relay permission, not a Garmin scope.
Fresh OAuth consent or explicit API-key selection is required; existing grants
cannot acquire it by refresh. It shares health/run metrics, including heart
rate, with the authorized assistant. Responses use an allowlist and exclude GPS
tracks and raw files, but activity names may themselves reveal locations.
Disable activity/MCP response-body capture in external monitoring. Revocation
blocks future requests, not data already shared with an assistant.

## Français

Workout Relay utilise des endpoints Garmin Connect non documentés. Il s'agit
d'une **intégration hébergée fondée sur la confiance**, et non d'un OAuth
délégué.

### Données conservées

- E-mail du compte Workout Relay et empreinte Argon2id salée du mot de passe.
- Plans d'entraînement et résultats d'import expurgés.
- Identifiants des activités observées dans votre historique, compte Garmin
  associé et date d'observation ; l'accès au détail expire après 24 heures.
  Ces références sont supprimées à la déconnexion Garmin ou à la suppression
  du compte. Les réponses contenant les mesures ne sont pas conservées.
- Empreintes des clés API Workout Relay ; la clé complète n'est affichée qu'une
  seule fois.
- Chiffrement authentifié des jetons d'accès et de renouvellement Garmin.

### Données non conservées

- E-mail et mot de passe Garmin.
- Codes MFA Garmin.
- Jetons Garmin en clair.
- Corps des requêtes d'identification dans les journaux ou événements d'audit.

Les identifiants Garmin existent brièvement en mémoire pendant leur échange
contre des jetons de session. L'opérateur contrôle le code de production et
pourrait théoriquement déployer un code qui les capture. Il ne faut donc pas
prétendre que l'opérateur est techniquement incapable d'accéder aux comptes.

En production : HTTPS est obligatoire, la clé `MASTER_ENCRYPTION_KEY` reste
dans un gestionnaire de secrets, la capture des corps de requête est désactivée
sur les routes Garmin, les sauvegardes sont chiffrées et la limitation de débit
est partagée entre les instances.

`activities:read` est une autorisation Workout Relay distincte, pas une portée
Garmin. Elle exige un nouveau consentement OAuth ou un choix explicite pour
une nouvelle clé API. Elle partage des mesures de santé/course, dont la
fréquence cardiaque, avec l'assistant autorisé, sans traces GPS ni fichiers
bruts. Le nom d'une activité peut toutefois révéler un lieu. Désactivez la
capture des réponses activités/MCP dans les outils de supervision. La révocation
bloque les futures requêtes, sans effacer les données déjà partagées.
