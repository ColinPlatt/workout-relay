# Security model / Modèle de sécurité

## English

Workout Relay uses Garmin's unofficial Connect endpoints. This is a
**trust-based hosted integration**, not delegated OAuth.

### What is stored

- Workout Relay account email and a salted Argon2id password hash.
- Submitted workout plans and redacted upload results.
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

## Français

Workout Relay utilise des endpoints Garmin Connect non documentés. Il s'agit
d'une **intégration hébergée fondée sur la confiance**, et non d'un OAuth
délégué.

### Données conservées

- E-mail du compte Workout Relay et empreinte Argon2id salée du mot de passe.
- Plans d'entraînement et résultats d'import expurgés.
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
