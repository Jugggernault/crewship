# shipcrew — proposition

> Un PRD en entrée, une URL de démo exploitable en sortie, zéro intervention après le « go ».
> Plugin Claude Code + CrewAI Flow qui pilote des sessions Claude Code headless.

## 1. Décision d'architecture

```
 Toi ──chat──▶ /shipcrew:ship  (seul point humain, dans Claude Code)
                 │ doctor → grilling (PRD blindé) → choix de style → DESIGN.md lint OK → « go »
                 ▼
          shipcrew run <projet>          (arrière-plan, CrewAI Flow @persist)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ plan      Crew CrewAI : Product Manager + Architecte → features.json      │
 │ design    Claude Code (designer) : DESIGN.md lint + brand board .fig      │
 │ scaffold  Claude Code (scaffolder) : Next.js, gh repo create, vercel link,│
 │           vercel integration add neon, Drizzle, init.sh, smoke test       │
 │ build     boucle : 1 feature = 1 session Claude Code (TDD + navigateur)   │
 │   ⇅ qa    Claude Code (QA) : suite Playwright + démo-script + audit style  │
 │           → qa.json ; les features en échec repartent dans la boucle      │
 │ secure    strix -n (si Docker) → Claude Code (sécurité) corrige crit/high │
 │ deploy    Claude Code (devops) : vercel deploy --prod, inspect --wait     │
 │           → le Flow VÉRIFIE l'URL lui-même (HTTP) ; sinon fallback local  │
 │ report    REPORT.md : URL démo, repo, features ✓/✗, sécurité, coût        │
 └───────────────────────────────────────────────────────────────────────────┘
```

**Pourquoi ce partage CrewAI / Claude Code :**

| Besoin | Qui | Pourquoi |
|---|---|---|
| Orchestration, état, reprise, routage, budgets | **CrewAI Flow** (`@start/@listen/@router`, `@persist`) | Déterministe, état typé Pydantic, reprise après crash (`shipcrew resume`) |
| Raisonnement multi-rôles sans outils (backlog, archi) | **CrewAI Crew** (PM + Architecte, `output_pydantic=Backlog`) | C'est là que le jeu de rôles apporte quelque chose : le PM coupe, l'archi valide la faisabilité |
| Tout ce qui touche au code, au shell, au navigateur, au déploiement | **Claude Code headless** (`claude -p --output-format json --permission-mode auto --plugin-dir … --max-budget-usd …`) | Meilleur agent de code disponible, hérite de tes skills/plugins/MCP (Vercel, open-pencil, superpowers…) |
| LLM des agents CrewAI | `ClaudeCodeLLM` (BaseLLM custom → `claude -p --tools ""`) | Tout tourne sur ton login Claude Code : **pas de clé API séparée** |

Ce que je **n'ai pas** fait exprès : donner des outils CrewAI aux agents qui codent. Un agent CrewAI qui
appelle un outil « ClaudeCode » paie deux LLM pour une seule décision et perd le contexte entre appels.
Les agents « qui ont des mains » sont donc des sessions Claude Code avec un rôle (`config/roles.yaml`).

## 2. Les agents

| Agent | Exécuté par | Skills / outils | Livrable (dans le repo) | Porte de sortie |
|---|---|---|---|---|
| Interviewer | toi + Claude (chat) | `grilling` / `grill-me` (mattpocock) | `.shipcrew/prd.md` | plus aucune inconnue |
| Directeur artistique | toi + Claude (chat) | `design-taste-frontend`, `high-end-visual-design`, spec `@google/design.md` | `DESIGN.md` | `design.md lint` = 0 erreur |
| Product Manager | CrewAI Agent | — | backlog | 3–12 features, critères testables |
| Architecte | CrewAI Agent | — | `features.json` (stack, needs_db, data model) | schéma Pydantic valide |
| Designer | Claude Code | `design-lock` (ce plugin), MCP **open-pencil** | `DESIGN.md`, `design/brand.fig` + `.png` | lint OK (brand board best-effort) |
| Scaffolder / DevOps | Claude Code | `vercel:nextjs`, `vercel:vercel-storage`, `vercel:shadcn`, `gh`, `vercel` | repo GitHub, projet Vercel lié, Neon, `init.sh` | `npm run build` + smoke test verts |
| Développeur (×N features) | Claude Code | `superpowers:test-driven-development`, `verification-before-completion`, `design-lock`, MCP `chrome-devtools` | 1 commit / feature, test e2e | dernière ligne `PASS` |
| QA | Claude Code | Playwright, MCP `chrome-devtools`, `design-lock`, `impeccable` (audit) | `qa.json` | `pass: true` (sinon réouverture, 3 tours max) |
| Sécurité | Claude Code (+ Strix si dispo) | `security-review`, MCP `chrome-devtools`, curl | `security.md` | crit/high corrigés ou justifiés |
| Release | Claude Code + vérif Python | `vercel:deploy`, `vercel:deployments-cicd` | `deploy.json` | l'URL répond (vérifié par le Flow, pas par l'agent) |

**Style verrouillé** : le skill que tu cherchais n'existe pas « tel quel » ; le meilleur assemblage est
**Google DESIGN.md** (format + CLI `lint`/`export`/`diff`, Apache-2.0, ~28k★) comme source de vérité,
+ un skill maison minimal `design-lock` (tokens only, `export --format css-tailwind`, grep anti-hex,
lint obligatoire avant « done »), + `impeccable` pour l'audit visuel (déjà installé chez toi).
Le même DESIGN.md alimente Stitch (`create_design_system_from_design_md`, MCP déjà connecté) si tu veux
des maquettes d'écrans en planning.

## 3. Inventaire open source (ce que j'ai vérifié et retenu)

| Besoin | Choix | Statut vérifié | Alternatives |
|---|---|---|---|
| Orchestration | **CrewAI 1.15** (Flows, `@persist`, `@human_feedback`, `BaseLLM` custom) | résolu via uv : v1.15.22 | Claude Agent SDK seul (voir §6) |
| Interview PRD | **mattpocock/skills** `grill-me` + `grilling` (primitive réutilisable) | README lu ; `grill-me` + `grilling` installés | `superpowers:brainstorming`, `grill-with-docs`, `to-spec`, `to-tickets` du même repo |
| Design system | **google-labs-code/design.md** (`npx @google/design.md lint/export/diff/spec`) | README lu, ~28k★, Apache-2.0, format *alpha* | google-labs-code/stitch-skills (`design-md`, `taste-design`), VoltAgent/awesome-design-md (catalogue) |
| Charte .fig | **open-pencil/open-pencil** (lit/écrit `.fig` natif, CLI `openpencil`, MCP `openpencil-mcp`, 100+ outils) | README lu, ~8.6k★, MIT, actif ; MCP déjà présent chez toi | l'ancien repo `open-pencil/skills` est **archivé** → skill via `npx skills add open-pencil/open-pencil` |
| Sécurité | **skill `security-review` + pentest par Claude (curl + chrome-devtools)** ; option **usestrix/strix** (`strix -n --target <dir|url|repo>`, exit ≠ 0 si vulnérabilités) | README lu, ~65k★, Apache-2.0, **Docker + `STRIX_LLM` + `LLM_API_KEY` requis** | skill `security-review` (Claude Code), semgrep, gitleaks |
| Discipline de dev | **obra/superpowers** (TDD, verification-before-completion, subagent-driven-dev, worktrees, code review) | README lu, MIT, dispo sur le marketplace officiel | mattpocock `implement`/`tdd`/`code-review` |
| Vérif navigateur | **chrome-devtools MCP** (ton Chromium, déjà configuré en scope user, hérité par les sessions headless) | vérifié en local | Claude in Chrome (`claude --chrome`, extension, pas headless), Playwright MCP, vercel-labs/agent-browser |
| Base de données | **Neon via `vercel integration add neon`** (non-interactif, env vars posées, branche par preview) | `vercel integration add --help` vérifié en local (`--non-interactive`, `--plan`, `--prefix`) | Supabase CLI/MCP (plus de features, plus de pièces mobiles) |
| GitHub / Vercel | `gh repo create --source . --push`, `vercel link --yes`, `vercel git connect`, `vercel deploy --prod`, `vercel inspect --wait/--logs` + plugin Vercel (déjà installé) | vercel CLI connecté (`jugggernault`) ; **gh absent** | — |
| Skill CrewAI | `claudiodearaujo/izacenter` → `crewai` | installé (`~/.claude/skills/crewai`) — générique, basé sur de vieilles API (`ChatOpenAI`), utile comme mémo seulement | docs.crewai.com + AGENTS.md que génère `crewai create` |

Systèmes « PRD → app » dont j'ai repris les idées plutôt que de les réutiliser :
- **Anthropic, harness pour agents longue durée** : `features.json` avec drapeaux `passes`, `progress.md`,
  `init.sh`, une feature par session, test e2e réel avant de cocher → c'est exactement la boucle `build`.
- **Ralph loop / spec-kit / BMAD / Task Master** : la spec écrite est le contrat, l'agent ne demande jamais,
  il décide et journalise.
- **MetaGPT / ChatDev** : ce qui ne marche pas — trop de rôles qui discutent entre eux sans vérification
  exécutable. D'où 2 agents CrewAI seulement, et des portes vérifiées par du code.
- **OpenHands / swe_loop (ton repo omnigent)** : worktrees parallèles + kanban. Reporté en v0.2 (voir §5).

## 4. Principes qui font tenir le « zéro intervention »

1. **Toutes les questions avant le go.** Après, les rôles ont l'ordre de décider et de noter la décision.
2. **Le repo est la mémoire** (`.shipcrew/prd.md`, `features.json`, `progress.md`, `init.sh`) : chaque
   session repart à froid sans perdre le fil, et `shipcrew resume` reprend après un crash (étapes idempotentes).
3. **Une feature par session**, test e2e écrit avant le code, commit par feature.
4. **Ne jamais croire l'agent sur parole** : `PASS` sur la dernière ligne, `qa.json`, et surtout l'URL
   de démo est sondée en HTTP par le Flow lui-même.
5. **Bornes partout** : 3 tentatives / feature, 3 tours QA, 3 déploiements, `--max-budget-usd` par session,
   `SHIPCREW_MAX_USD` (60 $ par défaut) global lu depuis `log.jsonl`.
6. **Dégradation plutôt qu'arrêt** : pas de Docker → pas de Strix mais revue sécu quand même ; déploiement
   KO → démo locale `http://localhost:3000` + raison dans le rapport ; brand board optionnel.
7. **Permissions** : `--permission-mode auto` (le classifieur de Claude Code bloque les actions risquées
   sans humain). `bypassPermissions` uniquement dans une VM/conteneur jetable.

## 5. Limites connues et suite

- **v0.1 = séquentiel.** v0.2 : features indépendantes en parallèle via `claude -w` (worktrees) et merge
  par une session dédiée — c'est ce que fait déjà ton `swe_loop`.
- Strix scanne le **code** ; le scanner contre l'URL de preview est meilleur mais doit se faire après
  deploy (upgrade simple : déplacer l'étape).
- Vercel Deployment Protection peut renvoyer 401 sur les previews : le rôle devops vise l'alias de prod ;
  le Flow accepte 401/403 comme « déployé mais protégé » et le signale.
- Neon : plan gratuit par défaut ; si ton compte Vercel exige un choix de plan, passer `--plan`.
- Observabilité : ajouter Langfuse/OpenTelemetry sur le Flow quand tu lanceras plusieurs projets.

## 6. Contre-proposition honnête

CrewAI n'est pas indispensable ici : le même Flow tient en ~150 lignes avec le **Claude Agent SDK** seul
(subagents + hooks). CrewAI apporte : Flow persisté/reprenable, Crew de planification structurée, et une
porte ouverte vers d'autres LLM par rôle. Je l'ai gardé parce que tu l'as demandé et qu'il est cantonné à
ce qu'il fait bien. Si un jour il gêne, seul `flow.py` change — les rôles, skills et le plugin restent.
