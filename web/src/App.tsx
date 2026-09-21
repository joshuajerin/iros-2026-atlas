import { FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type RankingItem } from "./api";
import type { Network, Paper } from "./types";

const label = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const fmt = new Intl.NumberFormat("en-US");
const topicColors = ["#43e0d0", "#c6f36f", "#ffbe5c", "#9da8ff", "#fb7ca7", "#6cb8ff"];

function App() {
  return <><Header /><main><Routes>
    <Route path="/" element={<Home />} />
    <Route path="/rankings" element={<Rankings />} />
    <Route path="/topics/:slug" element={<Topic />} />
    <Route path="/institutions" element={<EntityRankings kind="institutions" />} />
    <Route path="/researchers" element={<EntityRankings kind="researchers" />} />
    <Route path="/papers" element={<PaperExplorer />} />
    <Route path="/papers/:id" element={<PaperPage />} />
    <Route path="/connect" element={<Connect />} />
    <Route path="*" element={<Home />} />
  </Routes></main><Footer /></>;
}

function Header() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); if (query.trim()) navigate(`/papers?q=${encodeURIComponent(query.trim())}`); };
  return <header className="topbar"><Link to="/" className="brand"><span className="brand-mark">△</span><span>IROS 2026 <b>Atlas</b></span></Link>
    <nav aria-label="Primary"><NavLink to="/rankings">Rankings</NavLink><NavLink to="/institutions">Institutions</NavLink><NavLink to="/researchers">Researchers</NavLink><NavLink to="/connect">Connect agent</NavLink></nav>
    <form className="command" onSubmit={submit}><span>⌕</span><input aria-label="Search IROS papers" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search papers, people, methods"/><kbd>↵</kbd></form>
  </header>;
}

function Home() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const network = useQuery({ queryKey: ["network"], queryFn: api.network });
  const institutions = useQuery({ queryKey: ["institutions", 6], queryFn: () => api.rankings("institutions", "limit=6") });
  const researchers = useQuery({ queryKey: ["researchers", 6], queryFn: () => api.rankings("researchers", "limit=6") });
  if (overview.isLoading || !overview.data) return <Loading label="Calibrating the Atlas" />;
  const data = overview.data;
  return <>
    <section className="atlas-hero"><div className="hero-copy"><p className="eyebrow">IROS 2026 · Pittsburgh · September 28–30</p><h1>See where robotics research <em>connects.</em></h1><p className="lede">A live map of the complete conference program, scored with source-visible evidence and ready for your own research agent.</p><div className="hero-actions"><Link className="button primary" to="/papers">Explore every paper</Link><Link className="button ghost" to="/connect">Connect an agent <span>↗</span></Link></div></div>
      <KeywordMap network={network.data} />
      <div className="stat-strip">{[["papers", "Indexed papers"], ["keywords", "Author keywords"], ["institutions", "Institutions"], ["authors", "Source authors"]].map(([key, text]) => <div key={key}><strong>{fmt.format(data.stats[key as keyof typeof data.stats])}</strong><span>{text}</span></div>)}</div>
    </section>
    <section className="section signal-section"><div className="section-heading"><div><p className="eyebrow">Evidence, not hype</p><h2>Atlas signals</h2></div><Link to="/rankings">View full ranking →</Link></div><div className="signal-grid">{data.top_papers.map((paper, index) => <PaperSignal key={paper.paper_number} paper={paper} index={index + 1} />)}</div></section>
    <section className="split-section"><div className="topic-panel"><p className="eyebrow">Topic terrain</p><h2>Where the program is dense</h2><div className="topic-bars">{data.topics.slice(0, 8).map((topic, index) => <Link key={topic.topic} to={`/topics/${topic.topic}`} className="topic-row"><span className="topic-swatch" style={{ background: topicColors[index % topicColors.length] }} /><span>{topic.label}</span><i style={{ width: `${Math.max(10, topic.paper_count / data.topics[0].paper_count * 100)}%` }} /><b>{topic.paper_count}</b></Link>)}</div></div>
      <div className="method-panel"><p className="eyebrow">One score, inspectable</p><h2>Ranked without pretending certainty.</h2><p>The Atlas Score separates official recognition from citation, artifact, and confirmation evidence. Low-confidence papers remain searchable but do not enter the leaderboard.</p><Link to="/connect#methodology" className="text-link">Read the method <span>→</span></Link></div>
    </section>
    <section className="section entity-zone"><div className="entity-block"><div className="section-heading"><div><p className="eyebrow">Research presence</p><h2>Institutions</h2></div><Link to="/institutions">Full index →</Link></div><EntityRows items={institutions.data?.items ?? []} /></div>
      <div className="entity-block"><div className="section-heading"><div><p className="eyebrow">Fractional score</p><h2>Researchers</h2></div><Link to="/researchers">Full index →</Link></div><EntityRows items={researchers.data?.items ?? []} /></div>
    </section>
    <section className="search-anchor" id="paper-search"><div className="section-heading"><div><p className="eyebrow">All 1,933 papers</p><h2>Paper search</h2><p>Search title, author, abstract, keyword, topic, or institution.</p></div></div><PaperExplorer embedded /></section>
  </>;
}

function KeywordMap({ network }: { network?: Network }) {
  const navigate = useNavigate();
  const points = useMemo(() => (network?.nodes ?? []).slice(0, 28).map((node, index) => {
    const angle = index * 2.399963; const radius = 22 + Math.sqrt(index) * 26;
    return { ...node, x: 260 + Math.cos(angle) * radius, y: 235 + Math.sin(angle) * radius, color: topicColors[index % topicColors.length] };
  }), [network]);
  const byId = new Map(points.map((point) => [point.id, point]));
  const edges = (network?.edges ?? []).filter((edge) => byId.has(edge.source) && byId.has(edge.target)).slice(0, 45);
  return <div className="keyword-map" aria-label="Interactive author keyword map"><div className="map-label"><span className="pulse"/> CLICK A CONSTELLATION</div><svg viewBox="0 0 520 470" role="img" aria-label="Keyword co-occurrence constellation">
    <defs><radialGradient id="glow"><stop stopColor="#43e0d0" stopOpacity=".16"/><stop offset="1" stopColor="#07111a" stopOpacity="0"/></radialGradient></defs><circle cx="260" cy="235" r="215" fill="url(#glow)"/>
    {edges.map((edge, index) => { const a = byId.get(edge.source)!; const b = byId.get(edge.target)!; return <line key={`${edge.source}-${edge.target}-${index}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#79a8ad" strokeOpacity=".22" strokeWidth={Math.min(2.4, .5 + edge.count / 4)} />; })}
    {points.map((point) => <g key={point.id} className="map-node" tabIndex={0} role="button" aria-label={`Filter papers by ${point.label}`} onClick={() => navigate(`/papers?keyword=${point.id}`)} onKeyDown={(event) => event.key === "Enter" && navigate(`/papers?keyword=${point.id}`)}><circle cx={point.x} cy={point.y} r={Math.max(6, Math.min(17, point.count / 13))} fill={point.color}/><text x={point.x} y={point.y + 25} textAnchor="middle">{point.label.length > 20 ? `${point.label.slice(0, 18)}…` : point.label}</text></g>)}
  </svg><div className="map-caption">Author-declared keywords · edge weight is co-occurrence</div></div>;
}

function PaperSignal({ paper, index }: { paper: Paper; index: number }) {
  return <Link to={`/papers/${paper.paper_number}`} className="signal-card"><div className="signal-top"><span>#{String(index).padStart(2, "0")}</span><Score score={paper.score} /></div><h3>{paper.title}</h3><p>{paper.authors.slice(0, 3).join(" · ")}</p><div className="tags">{paper.topics.slice(0, 2).map((topic) => <span key={topic}>{label(topic)}</span>)}</div></Link>;
}

function Score({ score }: { score?: Paper["score"] }) { return score ? <span className="score"><b>{score.score.toFixed(1)}</b><small>Atlas Score</small></span> : null; }

function EntityRows({ items }: { items: RankingItem[] }) { return <div className="entity-rows">{items.map((item, index) => <div className="entity-row" key={`${item.id ?? item.name}`}><span className="rank">{String(index + 1).padStart(2, "0")}</span><div><b>{item.name}</b><small>{item.paper_count} papers · {item.topic_breadth} topic{item.topic_breadth === 1 ? "" : "s"}</small></div><strong>{item.score?.toFixed(1)}</strong></div>)}</div>; }

function Rankings() { const query = useQuery({ queryKey: ["paper-rankings"], queryFn: () => api.rankings("papers", "limit=50") }); return <section className="page"><PageIntro eyebrow="Evidence-weighted leaderboard" title="Paper rankings" text="A single Atlas Score, with the signals and confidence behind each placement."/><PaperTable papers={(query.data?.items ?? []) as Paper[]} /></section>; }
function EntityRankings({ kind }: { kind: "institutions" | "researchers" }) { const query = useQuery({ queryKey: [kind], queryFn: () => api.rankings(kind, "limit=100") }); const copy = kind === "institutions" ? "IROS 2026 research presence, never institutional prestige." : "Fractional paper scores by source author name. Ambiguous identities remain unmerged."; return <section className="page"><PageIntro eyebrow="Atlas index" title={kind === "institutions" ? "Institutions" : "Researchers"} text={copy}/><EntityRows items={query.data?.items ?? []}/></section>; }

function Topic() { const { slug = "" } = useParams(); const query = useQuery({ queryKey: ["topic", slug], queryFn: () => api.topic(slug) }); if (!query.data) return <Loading label="Plotting topic terrain"/>; const data = query.data; return <section className="page"><PageIntro eyebrow={`${data.paper_count} papers`} title={data.label} text="Author keywords and score-ranked papers within this IROS 2026 topic."/><div className="keyword-cloud">{data.keywords.map((keyword) => <Link key={keyword.slug} to={`/papers?keyword=${keyword.slug}`}>{keyword.label}<b>{keyword.paper_count}</b></Link>)}</div><PaperTable papers={data.rankings}/></section>; }

function PaperExplorer({ embedded = false }: { embedded?: boolean }) { const [params, setParams] = useSearchParams(); const navigate = useNavigate(); const [draft, setDraft] = useState(params.get("q") ?? ""); const query = useQuery({ queryKey: ["papers", params.toString()], queryFn: () => api.papers(params) }); const submit = (event: FormEvent) => { event.preventDefault(); const next = new URLSearchParams(params); draft ? next.set("q", draft) : next.delete("q"); next.delete("offset"); setParams(next); };
  const clear = () => { setDraft(""); setParams(new URLSearchParams()); };
  return <div className={embedded ? "explorer embedded" : "page explorer-page"}>{!embedded && <PageIntro eyebrow="Search the program" title="Paper explorer" text="Every indexed paper stays visible, whether or not it has enough external evidence to rank."/>}<form className="search-form" onSubmit={submit}><input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Search title, author, abstract, keyword..."/><button className="button primary">Search</button><button type="button" className="button ghost" onClick={clear}>Clear</button></form>
    <div className="filter-row"><span>{query.data ? `${fmt.format(query.data.count)} papers` : "Searching catalog…"}</span>{params.get("keyword") && <button className="filter-pill" onClick={() => { const next = new URLSearchParams(params); next.delete("keyword"); setParams(next); }}>Keyword: {params.get("keyword")} ×</button>}{params.get("q") && <button className="filter-pill" onClick={() => clear()}>Query: {params.get("q")} ×</button>}</div>
    {query.isError ? <p className="error">The catalog query could not be completed.</p> : <PaperTable papers={query.data?.papers ?? []} onOpen={(paper) => navigate(`/papers/${paper.paper_number}`)} />}</div>;
}

function PaperTable({ papers, onOpen }: { papers: Paper[]; onOpen?: (paper: Paper) => void }) { return <div className="paper-table">{papers.map((paper) => <article className="paper-row" key={paper.paper_number} onClick={() => onOpen?.(paper)}><div className="paper-meta"><span>{paper.day ?? "IROS"}</span><span>{paper.session_type ?? "Program"}</span></div><div className="paper-main"><h3>{paper.title}</h3><p>{paper.authors.slice(0, 4).join(" · ")}</p><div className="tags">{paper.keywords.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div></div><Score score={paper.score}/><Link to={`/papers/${paper.paper_number}`} className="row-arrow" aria-label={`Open ${paper.title}`}>↗</Link></article>)}</div>; }

function PaperPage() { const { id = "" } = useParams(); const query = useQuery({ queryKey: ["paper", id], queryFn: () => api.paper(id) }); if (query.isLoading || !query.data) return <Loading label="Opening paper record"/>; const paper = query.data; return <section className="page paper-detail"><Link className="back" to="/papers">← Back to explorer</Link><div className="detail-head"><div><p className="eyebrow">IROS 2026 · #{paper.paper_number}</p><h1>{paper.title}</h1><p className="detail-authors">{paper.authors.join(" · ")}</p></div><Score score={paper.score}/></div><div className="detail-grid"><article><h2>Abstract</h2><p className="abstract">{paper.abstract || "No verified abstract is attached to this record yet."}</p><h2>Research signals</h2><ScoreBreakdown paper={paper}/></article><aside><h2>Program record</h2><dl><dt>Session</dt><dd>{paper.session_name || paper.session_type || "Program"}</dd><dt>When</dt><dd>{[paper.day, paper.time, paper.room].filter(Boolean).join(" · ") || "See official record"}</dd><dt>Institutions</dt><dd>{paper.affiliations?.join(" · ") || "Not listed"}</dd></dl><div className="link-stack">{paper.official_record_url && <a href={paper.official_record_url} target="_blank" rel="noreferrer">Official IROS record ↗</a>}{paper.public_page_url && <a href={paper.public_page_url} target="_blank" rel="noreferrer">Verified public page ↗</a>}{paper.pdf_url && <a href={paper.pdf_url} target="_blank" rel="noreferrer">Open-access PDF ↗</a>}</div></aside></div><h2 className="related-title">Related in the program</h2><PaperTable papers={paper.related_papers ?? []}/></section>; }

function ScoreBreakdown({ paper }: { paper: Paper }) { const parts = paper.score?.components ?? {}; return <div className="breakdown">{Object.entries(parts).map(([key, value]) => <div key={key}><span>{label(key)}</span><i><b style={{ width: `${value}%` }}/></i><strong>{value.toFixed(0)}</strong></div>)}<p>Confidence {Math.round((paper.score?.confidence ?? 0) * 100)}% · v{paper.score?.version}</p></div>; }

function Connect() { const method = useQuery({ queryKey: ["methodology"], queryFn: api.methodology }); return <section className="page connect"><PageIntro eyebrow="Bring your own agent" title="Connect to Atlas" text="Atlas is a read-only research source. Your agent does the reasoning; Atlas supplies the cited conference evidence."/><div className="connect-grid"><article><p className="eyebrow">Hosted MCP</p><h2>One URL. Nine research tools.</h2><code>{window.location.origin}/mcp/</code><p>Use Streamable HTTP in an MCP-compatible client. Tools include paper search, rankings, keyword exploration, topic comparison, researcher lookup, and deterministic reading lists.</p><a href="/openapi.json" target="_blank">OpenAPI schema ↗</a></article><article><p className="eyebrow">REST fallback</p><h2>Any client can query the same catalog.</h2><code>{window.location.origin}/api/v1/papers?q=diffusion</code><p>REST and MCP share a service layer. Paper identifiers, scores, confidence, and evidence URLs are identical in both interfaces.</p><Link to="/papers">Try a query →</Link></article></div><section id="methodology" className="methodology"><p className="eyebrow">Atlas Score · v{method.data?.version ?? "…"}</p><h2>Every rank explains itself.</h2><div className="weights">{Object.entries(method.data?.weights ?? {}).map(([key, value]) => <div key={key}><span>{label(key)}</span><b>{Math.round(value * 100)}%</b></div>)}</div><ul>{method.data?.rules.map((rule) => <li key={rule}>{rule}</li>)}</ul></section></section>; }

function PageIntro({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) { return <div className="page-intro"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{text}</p></div>; }
function Loading({ label }: { label: string }) { return <div className="loading"><span className="pulse"/>{label}</div>; }
function Footer() { return <footer><span>Independent explorer. Not an official IROS website.</span><span>Built from the official IROS Paper & Author Index · <Link to="/connect#methodology">Methodology</Link></span></footer>; }

export default App;
