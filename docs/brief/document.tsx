import { readFileSync } from "node:fs";
import { Document, Page } from "@formepdf/react";

import { Heading } from "@/components/pdf/heading/heading";
import { Text } from "@/components/pdf/text/text";
import { PdfImage } from "@/components/pdf/pdf-image/pdf-image";
import { PdfCard } from "@/components/pdf/card/card";
import { Badge } from "@/components/pdf/badge/badge";
import { PdfList } from "@/components/pdf/list/list";
import { Section } from "@/components/pdf/section/section";
import { Stack } from "@/components/pdf/stack/stack";
import { Divider } from "@/components/pdf/divider/divider";
import { PageFooter } from "@/components/pdf/page-footer/page-footer";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/pdf/table/table";

const img = (name: string) =>
  `data:image/png;base64,${readFileSync(new URL(`./img/${name}.png`, import.meta.url)).toString("base64")}`;

const Footer = () => (
  <PageFooter leftText="shipcrew — présentation" rightText="Septembre 2026" fixed />
);

const Pillar = ({ n, title, children }: { n: string; title: string; children: React.ReactNode }) => (
  <Section spacing="sm" noWrap>
    <Stack direction="horizontal" gap="sm" align="center">
      <Badge variant="primary" size="sm">{n}</Badge>
      <Heading level={3} noMargin>{title}</Heading>
    </Stack>
    {children}
  </Section>
);

export const Brief = () => (
  <Document>
    {/* Page 1 — le quoi et le pourquoi */}
    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Heading level={1} noMargin>shipcrew</Heading>
        <Text variant="lg" color="mutedForeground">
          Une équipe d'agents IA qui construit et maintient une application sur GitHub, avec des humains
          qui pilotent depuis un seul écran.
        </Text>
        <Section variant="callout">
          <Text noMargin>
            On décrit le besoin (un PRD). shipcrew le découpe en tâches, les confie à des agents spécialisés qui
            travaillent en parallèle, fait passer chaque livraison par la CI et une revue de code, puis merge.
            L'humain n'intervient que lorsqu'une décision lui revient.
          </Text>
        </Section>

        <Stack direction="horizontal" gap="sm">
          <PdfCard title="16 features" padding="sm" style={{ flex: 1 }}>
            <Text variant="sm" noMargin>livrées en ~67 min sur une démo réelle (11 agents)</Text>
          </PdfCard>
          <PdfCard title="3 agents en parallèle" padding="sm" style={{ flex: 1 }}>
            <Text variant="sm" noMargin>démarrés en ~2 s chacun, chacun dans sa copie du code</Text>
          </PdfCard>
          <PdfCard title="117 cas testés" padding="sm" style={{ flex: 1 }}>
            <Text variant="sm" noMargin>de garde-fous vérifiés automatiquement, pour chaque rôle</Text>
          </PdfCard>
        </Stack>

        <Heading level={2}>Un seul écran : le board</Heading>
        <PdfImage src={img("board")} variant="bordered" caption="Chaque carte = une tâche = une issue + une PR. Les colonnes disent où elle en est." />

        <Footer />
      </Section>
    </Page>

    {/* Page 2 — CI/CD, rôles, review */}
    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Heading level={2}>Le parcours d'une tâche</Heading>
        <PdfList
          variant="numbered"
          gap="xs"
          items={[
            { text: "Le planner transforme le PRD en cartes, avec leurs dépendances et les fichiers que chacune touche." },
            { text: "Une carte démarre seule quand ses dépendances sont mergées, qu'il reste de la place et qu'aucune autre tâche ne touche les mêmes fichiers." },
            { text: "Un agent developer code dans sa propre branche, tests compris." },
            { text: "shipcrew pousse, ouvre la PR, lance la CI et corrige jusqu'à 3 fois si elle échoue." },
            { text: "Un agent reviewer relit ; si c'est bon, merge automatique ; si c'est sensible, un humain approuve." },
          ]}
        />
        <Pillar n="1" title="Des agents avec un rôle et des compétences">
          <Text variant="sm">
            Chaque rôle est un agent à part, avec ses instructions, ses skills et ses règles. Les règles communes
            sont écrites une seule fois.
          </Text>
          <Table variant="striped">
            <TableHeader>
              <TableRow header>
                <TableCell header width="22%">Rôle</TableCell>
                <TableCell header width="43%">Ce qu'il fait</TableCell>
                <TableCell header width="35%">Skills</TableCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[
                ["planner", "PRD en tâches, dépendances, fichiers", "grilling, écriture de PRD"],
                ["developer", "code + tests d'une tâche", "TDD, vérification, design-lock"],
                ["reviewer", "relit la PR, lecture seule", "code-review"],
                ["integrator", "résout les conflits de merge", "merge, vérification"],
                ["qa", "teste l'app dans un navigateur", "chrome-devtools, impeccable"],
                ["security", "cherche les failles", "security-review"],
                ["designer · scaffolder · devops", "style, squelette du projet, déploiement", "design-lock, shadcn, vercel"],
              ].map(([r, d, s]) => (
                <TableRow key={r}>
                  <TableCell width="22%">{r}</TableCell>
                  <TableCell width="43%">{d}</TableCell>
                  <TableCell width="35%">{s}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Text variant="sm" color="mutedForeground">
            Chaque rôle a une liste de commandes autorisées ; tout le reste demande l'accord d'un humain.
          </Text>
        </Pillar>

        <Footer />
      </Section>
    </Page>

    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Pillar n="2" title="CI/CD branchée sur GitHub (gh)">
          <PdfList
            variant="bullet"
            gap="xs"
            items={[
              { text: "Tout passe par le CLI gh : issues, branches, PR, statut de la CI, merge." },
              { text: "Chaque projet reçoit un workflow GitHub Actions prêt à l'emploi : lint, typage, tests, build, tests navigateur, audit des dépendances." },
              { text: "CI rouge : les logs d'erreur sont renvoyés à l'agent, qui corrige (3 essais max, puis un humain est prévenu)." },
              { text: "Les merges passent un par un, jamais deux à la fois : main reste toujours verte." },
              { text: "Les agents ne peuvent pas pousser eux-mêmes : seul shipcrew pousse, et seulement ses propres branches." },
            ]}
          />
        </Pillar>

        <Pillar n="3" title="Revue de code systématique">
          <Stack direction="horizontal" gap="md">
            <PdfImage src={img("review")} width={210} variant="bordered" />
            <Stack gap="xs" style={{ flex: 1 }}>
              <Text variant="sm" noMargin>
                Après une CI verte, un reviewer relit la PR avec les critères d'acceptation de la tâche.
              </Text>
              <Text variant="sm" noMargin>
                Ses remarques sont classées (bloquant, majeur, mineur) avec le fichier et la ligne, directement sur la carte.
              </Text>
              <Text variant="sm" noMargin>
                S'il demande des changements, le developer corrige ; au bout de 3 allers-retours, un humain tranche.
              </Text>
              <Text variant="sm" noMargin>
                Les zones sensibles (auth, CI, migrations, secrets…) exigent toujours une approbation humaine.
              </Text>
            </Stack>
          </Stack>
        </Pillar>
        <Divider spacing="sm" />
        <Text variant="xs" color="mutedForeground">
          Construit sur omnigent (open source, Apache-2.0), qui pilote les agents Claude Code. Inspiré d'OpenHands,
          open-swe et shep.
        </Text>
        <Footer />
      </Section>
    </Page>

    {/* Page 3 — navigateur, charge cognitive, observabilité */}
    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Pillar n="4" title="Qualité testée dans un vrai navigateur">
          <PdfList
            variant="bullet"
            gap="xs"
            items={[
              { text: "Chaque tâche arrive avec ses tests de bout en bout (Playwright), qui ouvrent l'application dans Chromium et vérifient les critères d'acceptation." },
              { text: "Ces tests tournent en local et dans la CI, sur la version de production de l'app." },
              { text: "L'agent qa parcourt le scénario de démo dans le navigateur, écran par écran, et relève erreurs console et écarts de design." },
            ]}
          />
        </Pillar>

        <Pillar n="5" title="Moins de charge mentale pour l'humain">
          <Stack direction="horizontal" gap="md">
            <PdfImage src={img("intervention")} width={140} variant="bordered" />
            <Stack gap="xs" style={{ flex: 1 }}>
              <Text variant="sm" noMargin>
                L'humain ne surveille pas des agents : il regarde un board de tâches.
              </Text>
              <Text variant="sm" noMargin>
                Une seule colonne demande son attention : « Intervention ». La carte dit pourquoi (commande à autoriser,
                merge à approuver, revue bloquée) et mène à l'action en un clic.
              </Text>
              <Text variant="sm" noMargin>
                Glisser une carte suffit à agir : « Ready » la lance, l'assigner à un humain arrête l'agent et lui
                laisse la main.
              </Text>
            </Stack>
          </Stack>
        </Pillar>

        <Pillar n="6" title="Tout reste observable">
          <Stack direction="horizontal" gap="md">
            <PdfImage src={img("tree")} width={220} variant="bordered" />
            <Stack gap="xs" style={{ flex: 1 }}>
              <Text variant="sm" noMargin>
                En cliquant sur une carte, on voit en direct l'arbre des agents qui travaillent dessus
                (developer, reviewer, sous-agents).
              </Text>
              <Text variant="sm" noMargin>
                Un clic ouvre la session : conversation, terminal, diff, fichiers.
              </Text>
              <Text variant="sm" noMargin>
                Sur chaque carte : statut, branche, PR, CI, verdict de revue et coût.
              </Text>
            </Stack>
          </Stack>
        </Pillar>

        <Divider spacing="sm" />
        <Heading level={3}>Où on en est</Heading>
        <PdfList
          variant="checklist"
          gap="xs"
          items={[
            { text: "Board, planner, rôles, garde-fous, boucle PR / CI / revue / merge", checked: true },
            { text: "Testé de bout en bout en local (2 tâches liées, CI corrigée, revue, merge)", checked: true },
            { text: "Test sur un vrai dépôt GitHub (connexion gh à faire)", checked: false },
            { text: "Hébergement sur un serveur pour toute l'équipe, agents isolés dans des conteneurs", checked: false },
          ]}
        />
        <Footer />
      </Section>
    </Page>
  </Document>
);
