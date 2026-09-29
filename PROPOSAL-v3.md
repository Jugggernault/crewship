# shipcrew v3 : construire sur un backend existant + vue kanban/arbre

> Remplace l'architecture « from scratch » de PROPOSAL-v2. Basé sur la lecture du code de :
> - OpenHands `software-agent-sdk` v1.49.6 (MIT) ;
> - OpenHands `automation` (MIT, bêta) ;
> - omnigent 0.16-dev (Apache-2.0, alpha) ;
> - open-swe, shep et Canvas-0S (analyses précédentes).

## 1. Ce que dit le code (et qui change le plan)

| Constat | Source | Conséquence |
|---|---|---|
| Le frontend omnigent est **soudé à l'API omnigent** : ~75 fichiers codent `/v1/...` en dur, sans couche d'adapter. | `web/src/lib/identity.ts` | « Backend OpenHands + frontend omnigent » impose de réécrire un shim d'environ 86 routes. **Non.** |
| Omnigent a déjà ce qu'on veut réutiliser : pilotage des vraies CLI (Claude Code, Codex, Gemini, Cursor, OpenCode… via tmux/SDK/ACP, abonnements compris), sessions parallèles en worktree, sandbox bwrap + proxy egress + proxy de credentials, arbre parent/enfant en DB. | `harness_plugins.py`, `host/git_worktree.py`, `sandbox/`, `inner/credential_proxy.py`, `db_models.py:860-945` | C'est la meilleure base pour « plusieurs CLI + parallélisme ». |
| **L'arbre des sous-agents en React Flow existe déjà** dans omnigent (statuts working / awaiting / failed / done, clic pour naviguer). L'API expose aussi `GET /v1/sessions/{id}/child_sessions` + un event SSE de mise à jour. Les sous-agents Task de Claude sont remontés avec `parent_subagent_id`. | `web/src/shell/SubagentsGraphView.tsx`, `hooks/useChildSessions.ts` | La vue « clic sur une tâche → arbre » est **déjà à 70 %**. |
| Il manque un **kanban** partout (omnigent, OpenHands, open-swe, shep). | — | C'est notre vue principale. |
| Dans OpenHands, les sous-agents internes (Task / Delegate / Workflow) **ne sont pas observables en live** : ils tournent in-process, dans le même dossier que le parent. Seules les conversations enfants créées par l'API ont un `parent_conversation_id`. | `openhands-tools/.../task/manager.py`, `delegate/impl.py` | OpenHands comme base, c'est un arbre à construire soi-même. On y perd aussi le frontend omnigent. |
| Aucune des bases ne gère de **DAG de tâches**, de merge sérialisé ni de boucle PR → CI → review. Omnigent a une GitHub App et un observateur de PR ; `git_router` d'OpenHands est en lecture seule. | — | La couche d'orchestration reste la nôtre : c'est le code shipcrew v0.2. |
| Le service automation d'OpenHands en mode local est **mono-utilisateur / mono-org** et n'écrit rien vers GitHub. | `automation/auth.py` | On reprend son modèle (trigger event + filtre JMESPath + prompt), pas le service : ce serait un 3e backend. |

**Techno :** **CrewAI n'existe qu'en Python**, et omnigent comme OpenHands sont en Python. « TypeScript + CrewAI » devient donc : **backend Python (omnigent forké + notre module) et frontend TypeScript (celui d'omnigent + notre board)**.

## 2. Architecture recommandée

```
┌───────────────────── web (fork du frontend omnigent, React 18 + shadcn + React Flow) ─────────────────────┐
│  NOUVEAU  BoardPage (kanban, @dnd-kit)   ──clic carte──►  tiroir tâche :                                  │
│           Backlog · Prêt · En cours · Review · Intervention · Mergé          SubagentsGraphView (existant)  │
│                                                                              + chat/stream, diff, terminal, │
│  EXISTANT Chat, Canvas, Inbox (interventions), Policies, Skills, Usage       CI, review, PR (panels existants)│
└──────────────────────────────▲───────────── /v1/* + /v1/shipcrew/* (même auth) ───────────────────────────┘
┌──────────────────────────────┴─────────── omnigent server (fork minimal) ─────────────────────────────────┐
│ sessions · runners · harnesses (claude/codex/gemini…) · worktrees · sandbox · policies · skills · GitHub App│
│ ┌─ NOUVEAU module omnigent/shipcrew/ (router /v1/shipcrew, tables via migration Alembic) ───────────────┐  │
│ │ Planner CrewAI (PM + architecte) → tâches + DAG + chemins possédés + issues GitHub                     │  │
│ │ Scheduler 4 gates (deps mergées × capacité × chemins possédés × budget) → crée des sessions omnigent  │  │
│ │ Boucle PR : push → PR draft → CI (fix ×3) → review cross-vendor → policy APPROVALS.md → merge sérialisé│  │
│ │ Triggers (modèle OpenHands automation : event GitHub + filtre JMESPath + prompt) · doctor · templates CI│  │
│ └───────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        runners (VPS) : 1 session = 1 worktree + bwrap + egress proxy + credentials par proxy
        GitHub : issues = tâches, PR = livrables, Actions = CI (runner self-hosted) · Vercel preview/prod
```

**Modèle :**
- 1 **tâche** = 1 carte = 1 issue GitHub = 1 **session racine** omnigent en worktree.
- Les **sous-agents** sont les sessions enfants : celles qu'on lance nous-mêmes (reviewer, integrator, sécurité) et celles que la CLI lance (Task de Claude). Ensemble, ils forment l'arbre React Flow de la carte.
- Les colonnes sont une projection de l'état de la tâche et de la session. Glisser une carte déclenche une action :
  - Prêt → lance la tâche ;
  - vers un humain → assigne l'issue et arrête l'agent ;
  - vers Mergé → exige une approbation.

**Parallélisme / vitesse :**
- la fondation d'abord (contrat partagé), puis les pistes en parallèle ;
- les 4 gates du scheduler, avec une capacité recalculée depuis la DB (idée shep) ;
- **review cross-vendor** : Claude code, Codex relit. Le pattern « Polly » d'omnigent le fait déjà.

**Ce qu'on garde tel quel d'omnigent :**
- harnesses multi-CLI et auth par abonnement ;
- worktrees, sandbox et proxys ;
- arbre des sous-agents ;
- Inbox (interventions) ;
- Policies (approbations, plafonds de dépense) ;
- skills (`/v1/skills`) ;
- tâches planifiées (RRULE) ;
- GitHub App et observateur de PR ;
- intégration Slack.

**Ce qu'on prend à OpenHands :**
- le modèle de trigger d'automation ;
- les analyseurs de risque (ConfirmRisky) si la policy omnigent ne suffit pas ;
- OpenHands lui-même peut devenir un harness ACP de plus plus tard. Ce n'est pas une base.

**Ce qu'on prend à open-swe / shep :**
- ids déterministes (issue → session) ;
- messages humains en interruption ou en file d'attente ;
- guards en hooks plutôt qu'en prompt ;
- boucle de correction CI ;
- résolution de conflits vérifiée par l'absence de marqueurs ;
- mémoire extraite après merge.

**Stratégie de fork :**
- ajouts isolés : `omnigent/shipcrew/` + `web/src/pages/BoardPage.tsx` + `web/src/board/` ;
- 1 ligne pour monter le router et 1 route dans le frontend ;
- rebase sur upstream chaque semaine.

Le rythme de release d'omnigent (hebdomadaire, alpha) l'impose : **pas de modifications dispersées dans leur code.**

## 3. Roadmap

| Phase | Contenu | Go / démo |
|---|---|---|
| **P0 Spike (2–3 j)** | Faire tourner omnigent en local. Via l'API : 3 sessions Claude Code en parallèle en worktree, abonnement, une session enfant → vérifier `child_sessions` et l'arbre. Mesurer CPU/RAM par session. | **Go/no-go sur la base.** Si non, fallback OpenHands agent-server + UI à nous. |
| **P1 Board** | BoardPage sur les sessions existantes (labels comme colonnes) + tiroir avec SubagentsGraphView + panels existants. | Kanban live, clic → arbre des agents. |
| **P2 Orchestrateur** | Module `shipcrew` : tâches/DAG, planner CrewAI → issues, scheduler 4 gates, boucle PR/CI/review/merge, templates CI, doctor (code v0.2 porté). | PRD → N PR en parallèle, reviewées cross-vendor, `main` verte. |
| **P3 Prod** | VPS (Docker Compose : server + runners + GitHub runner + Caddy), OIDC équipe, APPROVALS.md, gitleaks/Semgrep, previews Vercel, triggers GitHub (issue labellisée, CI rouge → tâche). | Humains et agents sur le même board, en ligne. |

**Risques :**
- **Churn alpha d'omnigent** : fork minimal et rebase hebdomadaire.
- **Dépendances tmux/bwrap** sur les hosts : OK sur Arch et sur le VPS.
- **Usage automatisé des abonnements** Claude/ChatGPT : vérifier les CGU Anthropic/OpenAI, sinon basculer sur des clés API.
- **Télémétrie omnigent** active par défaut : la désactiver.
