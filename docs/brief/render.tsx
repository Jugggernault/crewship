import { writeFileSync } from "node:fs";
import { renderPdf } from "@formepdf/core";
import { serialize } from "@formepdf/react";

import { Brief } from "./document";

// Same serialize-then-render path as pdfcn's own /api/pdf/forme route.
const bytes = await renderPdf(JSON.stringify(serialize(<Brief />)));
writeFileSync(new URL("./shipcrew.pdf", import.meta.url), Buffer.from(bytes));
console.log("wrote shipcrew.pdf");
