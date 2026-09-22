// Run: npx esbuild tests/dashboard.test.tsx --bundle --platform=node --format=esm --packages=external --jsx=automatic --outfile=node_modules/.cache/dashboard.test.mjs && NODE_ENV=production node node_modules/.cache/dashboard.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import App, { clearKeywordScope, findExactKeyword, resolveKeywordTopic, selectKeywordScope } from "../src/App";

// Fixture values deliberately distinguish topic-scoped counts from global counts.
Object.defineProperty(globalThis, "window", { value: { location: { origin: "http://127.0.0.1:8080" } }, configurable: true });
const paper = { paper_number: "1", title: "A research paper", authors: ["A. Researcher"], topics: ["ai"], keywords: ["Deep Learning"], score: { score: 60, eligible: true } };
function render(url: string, keywords = [{ slug: "deep-learning", label: "Deep Learning", paper_count: 51 }]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(["overview"], { stats: { papers: 1933, keywords: 400, institutions: 100, authors: 600 }, topics: [{ topic: "ai", label: "AI", paper_count: 261 }, { topic: "navigation", label: "Navigation", paper_count: 100 }], top_papers: [], built_at: "2026-09-21", score_version: "2026.1" });
  client.setQueryData(["network"], { nodes: [{ id: "deep-learning", label: "Deep Learning", count: 117, topic: "ai" }], edges: [] });
  client.setQueryData(["topic", "ai"], { slug: "ai", label: "AI", paper_count: 261, keywords, rankings: [paper] });
  const params = new URL(url, "http://localhost").searchParams;
  const topic = params.get("topic") ?? "";
  client.setQueryData(["researchers", 6, topic], { items: [{ id: 1, name: "A. Researcher", paper_count: topic ? 1 : 4, topic_breadth: 1, score: 60 }] });
  client.setQueryData(["institutions", 6, topic], { items: [{ id: 2, name: "Atlas Institute", paper_count: topic ? 1 : 5, topic_breadth: 2, score: 55 }] });
  client.setQueryData(["home-paper-rankings"], { items: [paper] });
  const search = new URLSearchParams(params);
  search.set("limit", "12"); search.set("offset", "0");
  client.setQueryData(["papers", search.toString()], { count: params.has("keyword") ? 51 : topic ? 261 : 1933, papers: [paper] });
  const result = renderToStaticMarkup(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}><App /></MemoryRouter></QueryClientProvider>);
  client.clear();
  return result;
}

test("dashboard order, real counts, accessible chart controls, and same-origin MCP URL", () => {
  const html = render("/");
  assert.match(html, /Most represented topics/);
  assert.match(html, /13\.5%/); // 261 / 1933; not share of summed topic memberships.
  assert.ok(html.indexOf('id="landscape-title"') < html.indexOf('id="mind-map-title"'));
  assert.ok(html.indexOf('id="mind-map-title"') < html.indexOf('id="paper-search-title"'));
  assert.ok(html.indexOf('id="paper-search-title"') < html.indexOf('id="researchers-title"'));
  assert.match(html, /class="topic-row" aria-pressed="false"/);
  assert.match(html, /value="http:\/\/127\.0\.0\.1:8080\/mcp\/"/);
});

test("dashboard uses a centered Atlas header, icon-only copy action, technical map controls, and whole-paper links", () => {
  const html = render("/");
  assert.match(html, />IROS Atlas<\/a>/);
  assert.match(html, /<form class="command"/);
  assert.match(html, /aria-label="Search IROS papers"/);
  assert.doesNotMatch(html, /<nav aria-label="Primary">/);
  assert.doesNotMatch(html, /IROS 2026 \/ Research workspace|Explore the program, follow connections|About the data|03 \/ Paper explorer/);
  assert.match(html, /aria-label="Copy MCP URL"/);
  assert.doesNotMatch(html, />Copy URL</);
  assert.match(html, /Minimum co-occurrences/);
  assert.match(html, /aria-label="Zoom in"/);
  assert.match(html, /Interactive force-directed keyword co-occurrence network/);
  assert.match(html, /Drag bubbles to rearrange the network\. Drag the background to pan, scroll to zoom, and select a bubble/);
  assert.match(html, /Accessible keyword index/);
  assert.match(html, /<article class="paper-row"><a class="paper-row-link" aria-label="Open paper: A research paper" href="\/papers\/1"/);
  assert.match(html, /Top papers/);
  assert.match(html, /Atlas Institute/);
});

test("connect page publishes a dynamic Streamable HTTP endpoint and generic JSON setup", () => {
  const html = render("/connect");
  const endpoint = "http://127.0.0.1:8080/mcp/";
  assert.match(html, /MCP endpoint/);
  assert.ok(html.includes(endpoint));
  assert.match(html, /Transport<\/dt><dd>Streamable HTTP<\/dd>/);
  assert.match(html, /Authentication<\/dt><dd>none<\/dd>/);
  assert.match(html, /Use this exact URL, including the trailing slash\./);
  assert.doesNotMatch(html, /prime-agent/i);
  assert.match(html, /Generic setup/);
  assert.match(html, /&quot;mcpServers&quot;/);
  assert.match(html, /&quot;type&quot;: &quot;http&quot;/);
  assert.match(html, /aria-label="JSON configuration"/);
});

test("dashboard exposes scoped, accessible expand controls for the community indexes", () => {
  const html = render("/?topic=ai");
  assert.match(html, /<button class="expand-button" aria-haspopup="dialog">Expand researchers<\/button>/);
  assert.match(html, /<button class="expand-button" aria-haspopup="dialog">Expand institutions<\/button>/);
  assert.match(html, /Fractional Atlas Score within Ai/);
  assert.match(html, /Research presence within Ai/);
  assert.doesNotMatch(html, /role="dialog"/); // Dialogs mount only after their respective Expand action.
});

test("topic drilldown uses scoped keyword counts and topic denominator", () => {
  const html = render("/?topic=ai");
  assert.match(html, /Keywords within AI/);
  assert.match(html, /<b>51<\/b><small>19\.5%<\/small>/); // 51/261, not 117/1933.
  assert.match(html, /← All topics/);
  assert.match(html, /class="sidebar-tree"/);
  assert.match(html, /<span>AI<\/span><small>topic<\/small>/);
  assert.match(html, /Author keywords/);
  assert.ok(html.indexOf('id="keywords-title"') < html.indexOf('class="sidebar-tree"'));
  assert.match(html, /class="keyword-list-label"/);
  assert.match(html, /aria-label="Scrollable keyword list"/);
});

test("keyword selection preserves parent and exposes matching papers and removable filters", () => {
  const html = render("/?topic=ai&keyword=deep-learning");
  assert.match(html, /class="topic-row" aria-pressed="true"/);
  assert.match(html, /AI[^]*?Deep Learning/);
  assert.match(html, /class="scope-notice"/);
  assert.match(html, /Paper results and the map follow this topic → keyword path/);
  assert.match(html, /Connected neighborhood/);
  assert.match(html, /Show matching papers/);
  assert.match(html, /Papers for Deep Learning/);
  assert.match(html, /Keyword selection does not filter this index/);
  assert.match(html, /51 matching papers/);
  assert.match(html, /aria-label="Clear keyword filter: deep-learning"/);
  assert.match(html, /View matching papers/);
  assert.match(html, /aria-current="page">Deep Learning/);
});

test("a keyword link without a topic resolves to the network-assigned topic path", () => {
  const html = render("/?keyword=deep-learning");
  assert.match(html, /Keywords within AI/);
  assert.match(html, /<span>AI<\/span><small>topic<\/small>/);
  assert.match(html, /class="tree-branch"[^]*?Author keywords[^]*?Deep Learning/);
  assert.match(html, /class="keyword-list"[^]*?class="active" aria-pressed="true"[^]*?Deep Learning/);
  assert.match(html, /Paper results and the map follow this topic → keyword path/);
});

test("exact keyword entry selects its true topic and clearing retains that topic", () => {
  const nodes = [
    { id: "reinforcement-learning", label: "Reinforcement Learning", count: 199, topic: "learning" },
    { id: "motion-planning", label: "Motion Planning", count: 153, topic: "navigation" },
  ];
  const match = findExactKeyword(" reinforcement learning ", nodes);
  assert.deepEqual(match, nodes[0]);
  assert.equal(findExactKeyword("reinforcement", nodes), undefined);
  assert.equal(resolveKeywordTopic("reinforcement-learning", "control", nodes), "learning");
  const selected = selectKeywordScope(new URLSearchParams("topic=control&offset=24"), match!);
  assert.equal(selected.toString(), "topic=learning&keyword=reinforcement-learning");
  const cleared = clearKeywordScope(selected, "learning");
  assert.equal(cleared.toString(), "topic=learning");
});

test("topic pages retain parent filter in keyword paper links", () => {
  const html = render("/topics/ai");
  assert.match(html, /href="\/papers\?topic=ai&amp;keyword=deep-learning"/);
});

test("empty keyword drilldown keeps paper discovery available", () => {
  const html = render("/?topic=ai", []);
  assert.match(html, /No author keywords are available for this topic/);
  assert.match(html, /261 matching papers/);
  assert.doesNotMatch(html, /Loading keywords within this topic/);
});
