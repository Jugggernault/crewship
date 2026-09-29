# shipcrew v2 : proposition de système complet

> Des agents qui construisent et maintiennent une app **en parallèle** sur un seul repo GitHub, avec des humains dans le même flux. Tout se pilote depuis un **canvas** où l'on crée les tâches, suit leur avancement et tranche quand il le faut.
> Sources : shep, OpenHands Agent Canvas, open-swe (Deep Agents), Canvas-0S, et la run gozem_clone (16 features en ~67 min grâce au swarm en worktrees).

## 0. Ce que chaque source apporte

| Source | À reprendre | À laisser |
|---|---|---|
| **shep** | Plafond de parallélisme recalculé depuis la DB (seuls les agents actifs comptent, donc aucun slot perdu après un crash). Gate de dépendances séparée du gate de capacité, avec rebase automatique. Un worker détaché par run : PID, heartbeat, contrôle de vivacité, SIGTERM→SIGKILL. Boucle de correction CI (3 essais). Résolution de conflits par agent, vérifiée par l'absence de marqueurs. Skills injectés dans le worktree et exclus des commits. Mémoire projet extraite après chaque merge. | Aucun sandbox (`--dangerously-skip-permissions`). Local et mono-utilisateur. Tokens stockés en clair. Périmètre trop large. |
| **OpenHands Agent Canvas** | Modèle d'événements typés (action / observation / source) et machine d'état `idle → running → waiting_for_confirmation → finished / error / stuck`. Historique en REST puis WebSocket `resend since` pour reprendre. 1 conversation = 1 worktree + 1 branche. Conversations parent/enfant. Automations (trigger + filtre + prompt + historique). Paliers de confirmation (jamais / toujours / seulement le risqué). Secrets jamais renvoyés à l'UI. | Registre multi-backend, Electron, routage entre LLM, marketplace. Pas de vue board : c'est une liste + détail. |
| **open-swe** | Ids déterministes dérivés des clés externes (issue ou PR → thread). Messages humains en cours de run, soit en interruption soit en file d'attente. Un sandbox persistant par tâche, jamais remplacé en silence. **Token GitHub injecté par un proxy, jamais présent dans le sandbox.** Guards en middleware plutôt que confiance dans le prompt (création de PR, push de workflows). Politique `APPROVALS.md` par chemin. Plan en artefact commentable. Reviewer read-only qui poste ses findings dans le diff. PR attribuées à l'humain déclencheur. | Aucune coordination entre agents sur un même repo : pas de DAG, pas de locks. Couplage à LangSmith. Fichier `server.py` de 1 754 lignes. |
| **Canvas-0S** | « On supervise des missions, pas des agents. » Log d'événements append-only comme source de vérité, et UI = projection par un reducer. Ingest idempotent (id, `seq` par mission). Taxonomie fermée d'événements (UEP). Contrat d'adapter à 4 méthodes. Capabilities qui masquent ce que le runtime ne sait pas faire. PermissionBridge : l'agent attend pendant que l'humain décide. Modèle d'attention : la caméra va vers l'urgent. | Canvas en lecture seule, sans arêtes ni tâches. Desktop mono-utilisateur. Pas de git. Identité des tâches tirée du texte du plan. |
| **Run gozem** | Fondation + contrat partagé d'abord, puis des pistes en worktrees, puis intégration. Chromium système. Validation sur un build de prod. | Build séquentiel. Blocages silencieux. Plafond de features dans le planner. |

**Le trou commun aux quatre :** aucun ne coordonne vraiment des agents parallèles sur le même repo (DAG + chemins possédés + merge sérialisé), et aucun n'offre un canvas où l'on *édite* le travail. C'est la valeur propre de shipcrew v2.

## 1. Architecture

```
                 ┌──────────── Canvas web (humains, GitHub OAuth) ─────────────┐
                 │ Mission board (DAG) · Tâche (stream, diff, CI, review) ·     │
                 │ Centre d'interventions · Journal de décisions · Automations   │
                 └───────────────▲ WS (events since seq) │ REST (commands) ─────┘
                                 │                       ▼
┌───────────────────────── crewd (coordinateur, 1 process, VPS) ─────────────────────────┐
│ Event log append-only (SQLite WAL) ─► projections (missions, tasks, runs, PRs)          │
│ Scheduler : gate dépendances × gate capacité × gate chemins possédés × budget           │
│ Policy engine (APPROVALS.md, paliers de risque) · Webhooks GitHub (HMAC) + polling      │
│ Merge serializer · Automations (cron / event + filtre) · Skill registry · Memory         │
└──────┬──────────────────────────────┬──────────────────────────────┬───────────────────┘
       │ spawn / heartbeat / kill     │ gh + GitHub App (tokens courts) │ Vercel / Neon
┌──────▼───────────────────────┐      ▼                               ▼
│ Worker pool (1 conteneur/run)│   GitHub : issues = tâches, PR = livrables,
│ worktree + branche + port +  │   Actions = CI (runner self-hosted sur le VPS),
│ DB (Neon branch ou lib/db.ts)│   Vercel = preview par PR + prod au merge
│ Claude Agent SDK + hooks     │
│ skills injectés par rôle     │
│ proxy credentials (egress)   │
└──────────────────────────────┘
```

**Modèle de travail :**
- **Mission** : un PRD ou un objectif.
- **Tâche** : 1 tâche = 1 issue GitHub, avec un id déterministe `repo#issue`.
- **Run** : une tentative d'agent, dans un worktree et un conteneur.
- **PR** : le livrable de la tâche.
- Les dépendances forment un DAG. Chaque tâche déclare les **chemins qu'elle possède**.
- Un humain qui s'assigne l'issue en prend la main, et les agents ne la touchent plus.

## 2. Couches

### 2.1 Abstraction utilisateur : le canvas
**Stack :** web, servi depuis le VPS, avec **React Flow (xyflow) + elkjs**, plutôt que tldraw. On veut un DAG éditable avec auto-layout, pas du dessin libre. On garde de Canvas-0S le store reducer et le modèle d'attention.

**Nœuds :**
- **Mission** : un conteneur.
- **Tâche** : une carte qui montre l'issue, le statut, l'acteur (agent ou humain), la PR, les badges CI et review, et le coût.
- **Intervention** : une pastille qui pulse sur la carte concernée.

**Arêtes :** les dépendances.

**Interactions sur le canvas :**
- créer une tâche (ce qui crée l'issue) ;
- tirer une arête (ce qui pose la dépendance) ;
- glisser la carte sur un avatar humain ou sur « agents » (ce qui assigne) ;
- sélection multiple puis approuver ou relancer (vue fleet de shep).

**Divulgation progressive :** un clic sur une carte ouvre un tiroir avec :
- le stream live de l'agent (actions et observations) ;
- le diff ;
- le terminal ;
- les logs CI ;
- les findings du reviewer ;
- le plan commentable (open-swe) ;
- un champ « message à l'agent », avec le choix interrompre ou mettre en file.

**Autres vues :**
- **Centre d'interventions**, trié par urgence (PermissionBridge de Canvas-0S). C'est la seule vue qui dérange l'humain.
- **Journal de décisions** : ce que les agents ont tranché seuls, pour pouvoir le contester.
- **Vue Automations.**

**Notifications :** Telegram ou Slack pour les interventions urgentes uniquement.

### 2.2 Parallélisation (le moteur de vitesse)
- **Fondation d'abord.** La tâche F01 livre le contrat partagé : data layer (`lib/db.ts` faker ou Drizzle), routes API en stub typé, composants partagés, et toutes les routes UI en stub. Les autres tâches attendent son merge.
- **Scheduler à 4 gates** :
  1. dépendances mergées ;
  2. capacité, recalculée depuis le log, où seuls les runs actifs comptent ;
  3. **aucun run actif ne possède un chemin qui chevauche** (c'est ce qui manque aux quatre sources) ;
  4. budget restant.
- **Isolation par run** : worktree, branche, conteneur, `PORT` et DB. Pour la DB, c'est une branche Neon (branching natif) ou `lib/db.ts` en mémoire.
- **Worker détaché** (shep) : heartbeat, watchdog d'inactivité (déjà écrit dans v0.2), stop SIGTERM→SIGKILL, et reprise via la session Agent SDK.
- **Cible** : 8 à 16 runs concurrents sur un VPS de 16 vCPU / 64 Go.

### 2.3 Intégrations
- **GitHub App**, plutôt qu'un PAT :
  - webhooks HMAC avec polling en secours ;
  - tokens d'installation courts, injectés par le proxy du worker.
- **Déclencheurs :**
  - issue avec le label `agent-ready` ;
  - `@shipcrew` en commentaire ;
  - commentaire sur une PR d'agent (renvoyé à la tâche en interruption ou en file) ;
  - `check_run` en échec (déclenche la boucle de correction CI).
- **Vercel** : preview par PR, prod au merge.
- **Neon** : une branche DB par PR.
- **Plus tard, par adapters :** Linear/Jira (import d'issues), Slack/Telegram (interventions), Sentry (erreur prod → tâche automatique).

### 2.4 Pipeline d'une tâche
1. **Plan** : gate optionnel, que la politique rend obligatoire sur les chemins sensibles.
2. **Implémentation** : TDD et e2e d'abord, puis toutes les commandes de `ci.yml` en local.
3. Le **coordinateur** (pas l'agent) pousse la branche et ouvre une PR draft attribuée à l'humain déclencheur.
4. **CI**, avec une boucle de correction de 3 essais.
5. **Reviewer read-only** : findings dans le diff, APPROVE ou CHANGES.
6. **Policy** :
   - merge automatique si le risque est bas ;
   - approbation humaine si la PR touche l'auth, la CI, les migrations, l'infra, les secrets ou les instructions d'agents (`APPROVALS.md`).
7. **Merge sérialisé** : update-branch, CI, squash. Un conflit lance un agent integrator, et le résultat est vérifié par l'absence de marqueurs de conflit.
8. **Après merge** : extraction de mémoire dans `AGENTS.md`, puis déblocage des tâches qui en dépendent.

La GitHub merge queue native est utilisée quand le plan GitHub la propose. Sinon, c'est le serializer maison.

### 2.5 CI/CD
- **CI** (`templates/ci.yml`, écrit dans v0.2) :
  - lint, typecheck, tests unitaires, build ;
  - Playwright sur un build de prod, avec Chromium préinstallé ;
  - `npm audit`.
- **Sécurité en CI :**
  - **gitleaks** et **Semgrep** partout ;
  - CodeQL si le repo est public ;
  - dependency review.
- **CD** : preview par PR, prod au merge sur `main`, rollback Vercel en un clic depuis le canvas.
- **Runner** : GitHub Actions self-hosted sur le VPS, via la variable `SHIPCREW_RUNNER` déjà prévue dans le template.

### 2.6 Sécurité
- **Guards en hooks Claude Code** (`PreToolUse`), pas en prompt. Ils bloquent :
  - `gh pr create` et `git push` par l'agent (c'est le coordinateur qui les fait) ;
  - les modifications de `.github/workflows/**` sans approbation ;
  - la lecture de `.env*` ;
  - `playwright install` et les gros téléchargements.
- **Conteneur par run :**
  - Docker rootless ;
  - CPU et mémoire limités ;
  - sortie réseau sur allowlist (npm, GitHub, Vercel, fonts) ;
  - aucun secret dans l'environnement, le proxy ajoute les credentials.
- **Contenu non fiable :** les commentaires venant de l'extérieur de l'organisation sont encapsulés et marqués non fiables (open-swe).
- **Secrets :** chiffrés au repos, jamais renvoyés à l'UI (OpenHands).
- **Traçabilité :** chaque décision d'agent est tracée dans le journal.
- **Agent sécurité :** il intervient automatiquement sur les PR à risque élevé, en plus des scanners de la CI.

### 2.7 Skills et configuration
- **Registre à trois niveaux** : org, repo et utilisateur (open-swe). Chaque skill est **épinglé** (`source@ref`, OpenHands).
- **Mapping rôle → skills** dans `roles.yaml`. Par exemple :
  - developer : TDD, verification, design-lock, shadcn MCP ;
  - reviewer : code-review ;
  - security : security-review.
- **Injection dans le worktree** (`.claude/skills`), exclue des commits (shep).
- **Catalogue MCP** avec health check (shadcn, chrome-devtools, Vercel…).
- **`doctor`** (v0.2) : résout et vérifie les binaires et les chemins, et demande le chemin à l'utilisateur si l'outil est introuvable.
- **Instructions en cascade** : org → `AGENTS.md` du repo → `AGENTS.md` imbriqués → tâche.
- **Stacks par défaut** : web = Next.js + shadcn (via MCP) ; mobile = Expo + React Native Reusables. Sans DB, on garde des routes API réelles branchées sur `lib/db.ts` (faker, seed 42).

## 3. Choix techniques

- **Moteur en Python**, pour réutiliser le code v0.2 : `tools.py`, `gh.py`, watchdog, rôles, templates.
  - **Claude Agent SDK** (Python) remplace `claude -p` en subprocess. On y gagne les hooks, la reprise de session et le streaming natif.
  - **CrewAI est retiré.** Il ne sert plus qu'au planner, et `@persist` est remplacé par le log d'événements.
- **API** : FastAPI + WebSocket.
- **État** : SQLite WAL. Passage à Postgres le jour où il y a plusieurs VPS.
- **Canvas** : Next.js + React Flow + shadcn, dans le même repo.
- **Types UEP** : générés depuis un seul JSON Schema vers Python (pydantic) et TypeScript. C'est l'idée ts-rs de Canvas-0S, sans Rust.
- **Adapter agent** (contrat Canvas-0S : `connect`, `subscribe`, `command`, `resolveRef`) :
  - v1 : Claude Code ;
  - v2 : Codex et autres via ACP. Le code ACP de Canvas-0S sert de référence.
- **Déploiement** : un VPS (Hetzner CCX33 ou équivalent) avec Docker Compose regroupant crewd, le canvas, le worker pool, le runner GitHub et Caddy (TLS).

## 4. Roadmap (chaque phase se démontre seule)

| Phase | Contenu | Démo |
|---|---|---|
| **P1 Moteur** | Log d'événements + scheduler à 4 gates, workers Agent SDK avec hooks guards + watchdog, GitHub App (issues/PR/CI fix/reviewer/merge sérialisé), templates CI, CLI `status`. On finit v0.2 dedans. | PRD → N PR en parallèle, reviewées et mergées, `main` verte. |
| **P2 Canvas v1** | DAG des missions/tâches (lecture + création/assignation/dépendances), tiroir tâche (stream, diff, CI, review), centre d'interventions, GitHub OAuth. | Un humain et des agents sur le même board, et un humain qui reprend une tâche. |
| **P3 Durcissement** | Conteneurs par run + proxy de credentials + egress, `APPROVALS.md`, gitleaks/Semgrep, branches Neon, previews Vercel, runner self-hosted. | PR à risque bloquée pour approbation humaine, secrets introuvables dans le sandbox. |
| **P4 Durée** | Automations (cron/event), registre de skills épinglés, mémoire projet, journal de décisions, Telegram/Slack, adapter ACP. | Erreur Sentry → tâche auto → PR corrigée pendant la nuit. |
