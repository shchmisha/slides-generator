import { html, type Raw } from "../html.ts";
import type { RenderContext } from "../types.ts";

/** Title block + logo shared by all content slides. */
export function header(title: string, subtitle: string | undefined, ctx: RenderContext): Raw {
  return html`<header class="hdr">
    <div class="hdr-text">
      <h1 class="title" data-pptx="text" data-fit="shrink">${title}</h1>
      ${subtitle ? html`<p class="subtitle" data-pptx="text">${subtitle}</p>` : ""}
    </div>
    ${ctx.logo ? html`<img class="logo" data-pptx="image" alt="" src="${ctx.logo}" />` : ""}
  </header>`;
}

export function footer(ctx: RenderContext): Raw {
  return html`<footer class="ftr">
    <div class="ftr-rule" data-pptx="shape"></div>
    <p class="ftr-left" data-pptx="text">${ctx.deck.footer ?? ctx.deck.title}</p>
    <p class="ftr-right" data-pptx="text">${ctx.index + 1} / ${ctx.total}</p>
  </footer>`;
}
