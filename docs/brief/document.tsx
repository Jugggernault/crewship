import { readFileSync } from "node:fs";
import { Document, Page } from "@formepdf/react";

import { Heading } from "@/components/pdf/heading/heading";
import { Text } from "@/components/pdf/text/text";
import { PdfImage } from "@/components/pdf/pdf-image/pdf-image";
import { PdfCard } from "@/components/pdf/card/card";
import { PdfList } from "@/components/pdf/list/list";
import { Section } from "@/components/pdf/section/section";
import { Stack } from "@/components/pdf/stack/stack";
import { PageFooter } from "@/components/pdf/page-footer/page-footer";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/pdf/table/table";

const img = (name: string) =>
  `data:image/png;base64,${readFileSync(new URL(`./img2/${name}.png`, import.meta.url)).toString("base64")}`;

const Footer = () => <PageFooter leftText="shipcrew" rightText="Septembre 2026" fixed />;

const Stat = ({ title, text }: { title: string; text: string }) => (
  <PdfCard title={title} padding="sm" style={{ flex: 1 }}>
    <Text variant="sm" noMargin>{text}</Text>
  </PdfCard>
);

const Row = ({ cells }: { cells: string[] }) => (
  <TableRow>
    {cells.map((c, i) => (
      <TableCell key={i} width={i === 0 ? "34%" : "22%"}>{c}</TableCell>
    ))}
  </TableRow>
);

export const Brief = () => (
  <Document>
    {/* Page 1 — ce que c'est */}
    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Heading level={1} noMargin>shipcrew</Heading>
        <Text variant="lg" color="mutedForeground">
          Un PRD en entrée, une app en ligne en sortie. Des agents IA construisent en parallèle sur GitHub ;
          l'humain pilote depuis un board.
        </Text>

        <Stack direction="horizontal" gap="sm">
          <Stat title="36 min" text="du PRD à l'app en ligne (run 4)" />
          <Stat title="3 interventions" text="humaines, contre 25 au premier run" />
          <Stat title="~300 Mo" text="par agent en mode headless (vs 660 Mo)" />
        </Stack>

        <Heading level={2}>Comment ça marche</Heading>
        <PdfList
          variant="numbered"
          gap="xs"
          items={[
            { text: "Le planner découpe le PRD en tâches (issues GitHub), avec dépendances et fichiers réservés." },
            { text: "Les tâches indépendantes partent en parallèle, chacune dans sa branche et son agent." },
            { text: "Chaque PR passe la CI, puis un reviewer ; le merge est automatique, sauf zone sensible." },
            { text: "L'app est en ligne dès la fondation, puis redéployée à chaque merge." },
            { text: "QA et sécurité vérifient à la fin et créent seules les tâches de correction." },
          ]}
        />

        <Heading level={2}>Le board</Heading>
        <PdfImage src={img("board")} variant="bordered" caption="Une carte = une tâche = une issue + une PR. Ici : 6 tâches mergées, app livrée." />
        <Footer />
      </Section>
    </Page>

    {/* Page 2 — exemple réel */}
    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Heading level={2} noMargin>Exemple : « Mini-sondages », construit par shipcrew</Heading>
        <Text variant="sm" color="mutedForeground">
          PRD d'une page : créer un sondage, voter, voir les résultats. Next.js + shadcn, vraies routes API,
          fausse base de données générée (faker). Aucune ligne écrite à la main.
        </Text>
        <Stack direction="horizontal" gap="md">
          <PdfImage src={img("app-home")} width={200} variant="bordered" caption="Accueil + création" />
          <Stack gap="sm" style={{ flex: 1 }}>
            <PdfImage src={img("app-poll")} variant="bordered" caption="Résultats après un vote" />
            <PdfList
              variant="bullet"
              gap="xs"
              items={[
                { text: "6 tâches, 6 PR, CI GitHub verte, 6 revues approuvées" },
                { text: "3 pages construites en parallèle" },
                { text: "Sécurité : taille des champs limitée (erreur 400) — trou trouvé par la QA aux runs 1-2, prévenu d'office ensuite" },
                { text: "Run 4 sur Vercel ; run 5 en conteneur, URL publique trycloudflare.com sans clé ni compte" },
              ]}
            />
          </Stack>
        </Stack>
        <Heading level={3}>Le rapport de fin, généré automatiquement</Heading>
        <PdfImage src={img("report")} width={250} variant="bordered" caption="URL vérifiée par le serveur, coût, durée, PR, décisions des agents, interventions humaines." />
        <Footer />
      </Section>
    </Page>

    {/* Page 3 — ce qu'il y a dedans */}
    <Page size="A4" margin={40}>
      <Section spacing="none">
        <Heading level={2} noMargin>Ce qui est en place</Heading>
        <Stack direction="horizontal" gap="md">
          <PdfImage src={img("drawer")} width={140} variant="bordered" caption="Une tâche : PR, CI, revue, arbre des agents" />
          <Stack gap="xs" style={{ flex: 1 }}>
            <PdfList
              variant="bullet"
              gap="xs"
              items={[
                { text: "CI/CD via gh : issues, PR, CI, merge sérialisé, correction auto si la CI casse" },
                { text: "Rôles + skills : planner, designer, scaffolder, developer, reviewer, integrator, qa, security, devops" },
                { text: "Garde-fous : commandes autorisées par rôle, fichiers réservés par tâche, pas de push ni de .env" },
                { text: "Tests dans un vrai navigateur (Playwright, Chrome headless) + tests unitaires" },
                { text: "L'humain ne regarde qu'une colonne : Intervention" },
                { text: "Chaque projet = un dossier de sessions, avec son icône board" },
              ]}
            />
            <PdfImage src={img("sidebar")} width={170} variant="bordered" />
          </Stack>
        </Stack>

        <Heading level={3}>Sans Vercel : conteneur + URL publique, sans clé</Heading>
        <PdfImage src={img("live")} width={280} variant="bordered" caption="URL live affichée dès le premier déploiement, mise à jour à chaque merge." />
        <PdfList
          variant="bullet"
          gap="xs"
          items={[
            { text: "Image Docker minimale (~60 Mo compressée, ~40 Mo de RAM au repos), relancée sans coupure" },
            { text: "URL publique via tunnel Cloudflare (poste local) ou <app>.<ip>.sslip.io + HTTPS (VPS)" },
            { text: "Option VPS : ArgoCD, une preview par PR (prouvé sur un cluster local ; VPS réel à tester)" },
          ]}
        />

        <Heading level={3}>Progression sur le même PRD</Heading>
        <Table variant="striped">
          <TableHeader>
            <TableRow header>
              <TableCell header width="34%">Run</TableCell>
              <TableCell header width="22%">Durée</TableCell>
              <TableCell header width="22%">Interventions</TableCell>
              <TableCell header width="22%">Coût</TableCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            <Row cells={["1", "49 min", "25", "6,33 $"]} />
            <Row cells={["2", "59 min", "~14", "6,48 $"]} />
            <Row cells={["3", "41 min", "14", "7,06 $"]} />
            <Row cells={["4", "36 min", "3", "6,41 $"]} />
            <Row cells={["5 (Docker, sans Vercel)", "n/d*", "1", "4,64 $"]} />
          </TableBody>
        </Table>
        <Text variant="xs" color="mutedForeground">* Run 5 coupé par des correctifs du déploiement. Coûts = quota Claude.</Text>
        <Footer />
      </Section>
    </Page>
  </Document>
);
