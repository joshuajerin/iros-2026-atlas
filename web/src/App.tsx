import { FormEvent, type KeyboardEvent as ReactKeyboardEvent, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import * as echarts from "echarts";
import { api, type RankingItem } from "./api";
import type { Network, Paper } from "./types";

const label = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const fmt = new Intl.NumberFormat("en-US");
const topicColors = ["#236d65", "#497caf", "#a36d3d", "#7c65a8", "#ae5a77", "#548477"];
type KeywordNode = Network["nodes"][number];

// The network is the source of truth for a keyword's assigned topic. Keeping this
// logic here prevents a stale URL topic from making the dashboard look inconsistent.
export function resolveKeywordTopic(keyword: string, requestedTopic: string, nodes: KeywordNode[]) {
  return keyword ? nodes.find((node) => node.id === keyword)?.topic ?? requestedTopic : requestedTopic;
}

export function findExactKeyword(value: string, nodes: KeywordNode[]) {
  const normalized = value.trim().toLocaleLowerCase();
  return normalized ? nodes.find((node) => node.label.toLocaleLowerCase() === normalized || node.id.toLocaleLowerCase() === normalized) : undefined;
}

export function selectKeywordScope(params: URLSearchParams, node: KeywordNode) {
  const next = new URLSearchParams(params);
  next.set("topic", node.topic);
  next.set("keyword", node.id);
  next.delete("offset");
  return next;
}

export function clearKeywordScope(params: URLSearchParams, topic: string) {
  const next = new URLSearchParams(params);
  if (topic) next.set("topic", topic);
  next.delete("keyword");
  next.delete("offset");
  return next;
}

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
  return <header className="topbar"><span aria-hidden="true" /> <Link to="/" className="brand">IROS Atlas</Link>
    <form className="command" onSubmit={submit}><span>⌕</span><input aria-label="Search IROS papers" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search papers, people, methods"/><kbd>↵</kbd></form>
  </header>;
}

function Home() {
  const [params, setParams] = useSearchParams();
  const topic = params.get("topic") ?? "";
  const keyword = params.get("keyword") ?? "";
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const network = useQuery({ queryKey: ["network"], queryFn: api.network });
  const activeTopic = resolveKeywordTopic(keyword, topic, network.data?.nodes ?? []);
  const topicDetail = useQuery({ queryKey: ["topic", activeTopic], queryFn: () => api.topic(activeTopic), enabled: !!activeTopic });
  const researchers = useQuery({ queryKey: ["researchers", 6, activeTopic], queryFn: () => api.rankings("researchers", new URLSearchParams({ limit: "6", ...(activeTopic ? { topic: activeTopic } : {}) }).toString()) });
  const institutions = useQuery({ queryKey: ["institutions", 6, activeTopic], queryFn: () => api.rankings("institutions", new URLSearchParams({ limit: "6", ...(activeTopic ? { topic: activeTopic } : {}) }).toString()) });
  const paperRankings = useQuery({ queryKey: ["home-paper-rankings"], queryFn: () => api.rankings("papers", "limit=6") });
  const [expandedIndex, setExpandedIndex] = useState<"researchers" | "institutions" | null>(null);
  const researchersExpandTrigger = useRef<HTMLButtonElement | null>(null);
  const institutionsExpandTrigger = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    // Normalize direct and stale links after the network has loaded, so all
    // downstream API requests and the address bar agree on the same path.
    if (keyword && activeTopic && topic !== activeTopic) {
      const next = new URLSearchParams(params);
      next.set("topic", activeTopic);
      setParams(next, { preventScrollReset: true });
    }
  }, [activeTopic, keyword, params, setParams, topic]);
  const select = (key: "topic" | "keyword", value: string) => {
    const next = new URLSearchParams(params);
    next.get(key) === value ? next.delete(key) : next.set(key, value);
    if (key === "topic") next.delete("keyword");
    next.delete("offset");
    setParams(next, { preventScrollReset: true });
  };
  const selectKeyword = (value: string, parentTopic = "") => {
    const node = network.data?.nodes.find((item) => item.id === value);
    const next = node ? selectKeywordScope(params, node) : new URLSearchParams(params);
    if (!node) {
      if (parentTopic) next.set("topic", parentTopic);
      next.set("keyword", value);
      next.delete("offset");
    }
    setParams(next, { preventScrollReset: true });
  };
  const clearTopic = () => { const next = new URLSearchParams(params); next.delete("topic"); next.delete("keyword"); next.delete("offset"); setParams(next, { preventScrollReset: true }); };
  const clearKeyword = () => setParams(clearKeywordScope(params, activeTopic), { preventScrollReset: true });
  if (overview.isError) return <section className="page"><h1>IROS Atlas</h1><QueryError label="The conference overview could not be loaded." retry={() => overview.refetch()} /></section>;
  if (!overview.data) return <Loading label="Loading the research dashboard" />;
  const data = overview.data;
  const topics = [...data.topics].sort((a, b) => b.paper_count - a.paper_count);
  const chartItems = activeTopic ? (topicDetail.data?.keywords ?? []).map((item) => ({ ...item, id: item.slug })) : topics.map((item) => ({ ...item, id: item.topic }));
  const largest = Math.max(1, ...chartItems.map((item) => item.paper_count));
  const denominator = activeTopic ? topicDetail.data?.paper_count ?? 0 : data.stats.papers;
  const topicLabel = topicDetail.data?.label ?? topics.find((item) => item.topic === activeTopic)?.label ?? label(activeTopic);
  const keywordLabel = topicDetail.data?.keywords.find((item) => item.slug === keyword)?.label ?? network.data?.nodes.find((item) => item.id === keyword)?.label ?? keyword;
  return <div className={`dashboard-shell${keyword ? " is-keyword-scoped" : ""}`}>
    <aside className="dashboard-sidebar" aria-label="Research tools">
      <KeywordSidebar network={network.data} loading={network.isLoading} error={network.isError} retry={() => network.refetch()} selected={keyword} topic={activeTopic} topicLabel={topicLabel} topicKeywords={topicDetail.data?.keywords ?? []} onSelect={selectKeyword} onClearTopic={clearTopic} onClearKeyword={clearKeyword} />
    </aside>
    <div className="dashboard-content">
      <AgentCard />
      <div className="stat-strip">{[["papers", "Indexed papers"], ["keywords", "Author keywords"], ["institutions", "Institutions"], ["authors", "Source authors"]].map(([key, text]) => <div key={key}><strong>{fmt.format(data.stats[key as keyof typeof data.stats])}</strong><span>{text}</span></div>)}</div>
      <section className={`dashboard-panel${keyword ? " scope-accent" : ""}`} aria-labelledby="landscape-title"><div className="section-heading"><div><h2 id="landscape-title">{activeTopic ? `Keywords within ${topicLabel}` : "Most represented topics"}</h2><p>{activeTopic ? "Explore the most represented author keywords in this topic. Select one to narrow the papers below." : "Select a topic to explore its keywords, focus papers and researchers, and highlight the mind map."}</p></div><span className="section-note">Papers / share of {activeTopic ? "topic" : "program"}</span></div>
        {activeTopic && <nav className="breadcrumb" aria-label="Research landscape drilldown"><button onClick={clearTopic}>← All topics</button><span aria-hidden="true">/</span><span>{topicLabel}</span>{keyword && <><span aria-hidden="true">/</span><span aria-current="page">{keywordLabel}</span></>}</nav>}
        {activeTopic && topicDetail.isError ? <QueryError label="Keywords for this topic could not be loaded." retry={() => topicDetail.refetch()} /> : activeTopic && topicDetail.isLoading ? <Loading label="Loading keywords within this topic" /> : <div className="topic-bars">{chartItems.map((item, index) => <button key={item.id} className="topic-row" aria-pressed={activeTopic ? keyword === item.id : false} onClick={() => activeTopic ? selectKeyword(item.id, activeTopic) : select("topic", item.id)}><span className="topic-name">{item.label}</span><span className="bar-track" aria-hidden="true"><i style={{ width: `${item.paper_count / largest * 100}%`, background: topicColors[index % topicColors.length] }} /></span><span className="topic-count"><b>{fmt.format(item.paper_count)}</b><small>{denominator ? (item.paper_count / denominator * 100).toFixed(1) : "0.0"}%</small></span></button>)}</div>}
        {!chartItems.length && !(activeTopic && (topicDetail.isLoading || topicDetail.isError)) && <p className="empty">{activeTopic ? "No author keywords are available for this topic. Its papers are still searchable below." : "No topic counts are available yet."}</p>}
        {keyword && <div className="filter-row"><button className="filter-pill" onClick={() => select("keyword", keyword)}>Keyword: {keywordLabel} × Clear</button><a className="text-link" href="#paper-search">View matching papers ↓</a></div>}
      </section>
      <section className={`dashboard-panel${keyword ? " scope-accent" : ""}`} aria-labelledby="mind-map-title"><div className="section-heading"><div><h2 id="mind-map-title">Keyword mind map</h2><p>{keyword ? <><b>{keywordLabel}</b> is in focus. Its recorded links stay bright while unrelated nodes recede.</> : activeTopic ? `Highlighted nodes have ${label(activeTopic)} as their assigned topic. The network remains conference-wide.` : "Explore connections between author-declared keywords."}</p></div><span className="section-note">Drag to pan · scroll to zoom</span></div>{network.isError ? <QueryError label="The keyword map could not be loaded." retry={() => network.refetch()} /> : network.isLoading ? <Loading label="Loading keyword connections" /> : <KeywordMap network={network.data} topic={activeTopic} selected={keyword} onSelect={selectKeyword} />}</section>
      <section className={`dashboard-panel search-anchor${keyword ? " scope-accent" : ""}`} id="paper-search" aria-labelledby="paper-search-title"><div className="section-heading"><div><h2 id="paper-search-title">{keyword ? `Papers for ${keywordLabel}` : "Find your next read"}</h2><p>{keyword ? `This result set is filtered to ${activeTopic ? `${topicLabel} → ` : ""}${keywordLabel}.` : "Search titles, authors, abstracts, and keywords. Results use Atlas Score order."}</p></div></div><PaperExplorer embedded /></section>
      <section className="dashboard-panel home-overview" aria-labelledby="rankings-title"><div className="section-heading"><div><h2 id="rankings-title">Top papers</h2><p>Evidence-weighted Atlas Score across the catalog.</p></div></div>{paperRankings.isError ? <QueryError label="Ranked papers could not be loaded." retry={() => paperRankings.refetch()} /> : paperRankings.isLoading ? <Loading label="Loading ranked papers" /> : <PaperTable papers={(paperRankings.data?.items ?? []) as Paper[]} />}</section>
      <section className={`dashboard-panel home-overview-grid${keyword ? " scope-accent scope-limited" : ""}`} aria-label="Research community overviews"><section aria-labelledby="researchers-title"><div className="section-heading section-heading-action"><div><h2 id="researchers-title">Researchers</h2><p>Fractional Atlas Score{activeTopic ? ` within ${label(activeTopic)}` : " across the conference"}{keyword ? ". Keyword selection does not filter this index." : "."}</p></div><button ref={researchersExpandTrigger} className="expand-button" aria-haspopup="dialog" onClick={() => setExpandedIndex("researchers")}>Expand researchers</button></div>{researchers.isError ? <QueryError label="Researchers could not be loaded." retry={() => researchers.refetch()} /> : researchers.isLoading ? <Loading label="Loading researchers" /> : researchers.data?.items.length ? <EntityRows items={researchers.data.items} /> : <p className="empty">No researchers match this topic.</p>}</section><section aria-labelledby="institutions-title"><div className="section-heading section-heading-action"><div><h2 id="institutions-title">Institutions</h2><p>Research presence{activeTopic ? ` within ${label(activeTopic)}` : " across the conference"}{keyword ? ". Keyword selection does not filter this index." : "."}</p></div><button ref={institutionsExpandTrigger} className="expand-button" aria-haspopup="dialog" onClick={() => setExpandedIndex("institutions")}>Expand institutions</button></div>{institutions.isError ? <QueryError label="Institutions could not be loaded." retry={() => institutions.refetch()} /> : institutions.isLoading ? <Loading label="Loading institutions" /> : institutions.data?.items.length ? <EntityRows items={institutions.data.items} /> : <p className="empty">No institutions match this topic.</p>}</section></section>
      <EntityRankingDialog kind="researchers" topic={activeTopic} open={expandedIndex === "researchers"} triggerRef={researchersExpandTrigger} onClose={() => setExpandedIndex(null)} />
      <EntityRankingDialog kind="institutions" topic={activeTopic} open={expandedIndex === "institutions"} triggerRef={institutionsExpandTrigger} onClose={() => setExpandedIndex(null)} />
    </div>
  </div>;
}

function AgentCard() {
  const endpoint = `${window.location.origin}/mcp/`;
  const [status, setStatus] = useState("");
  const copy = async () => { try { await navigator.clipboard.writeText(endpoint); setStatus("Copied MCP URL."); } catch { setStatus("Copy unavailable. Select the URL and copy it manually."); } };
  return <section className="agent-card agent-card-header" aria-labelledby="agent-title">
    <div className="agent-copy"><p className="eyebrow">IROS Atlas</p><h2 id="agent-title">Connect your agent</h2><p>Bring this research catalog into your MCP-compatible client.</p></div>
    <div className="agent-endpoint"><label htmlFor="mcp-url">Streamable HTTP endpoint</label><div className="agent-url"><input id="mcp-url" value={endpoint} readOnly onFocus={(event) => event.target.select()} /><button className="copy-button" onClick={copy} aria-label="Copy MCP URL" title="Copy MCP URL"><CopyIcon /></button></div><p className="copy-status" role="status" aria-live="polite">{status}</p></div>
    <Link className="agent-setup-link" to="/connect">Setup & methodology →</Link>
  </section>;
}

function CopyIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="9" y="8" width="10" height="12" rx="2"/><path d="M15 8V6a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h2"/></svg>; }

function KeywordSidebar({ network, loading, error, retry, selected, topic, topicLabel, topicKeywords, onSelect, onClearTopic, onClearKeyword }: { network?: Network; loading: boolean; error: boolean; retry: () => void; selected: string; topic: string; topicLabel: string; topicKeywords: { slug: string; label: string; paper_count: number }[]; onSelect: (id: string, parentTopic?: string) => void; onClearTopic: () => void; onClearKeyword: () => void }) {
  const [filter, setFilter] = useState("");
  const [preview, setPreview] = useState<Network["nodes"][number] | null>(null);
  const [pinned, setPinned] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const allNodes = network?.nodes ?? [];
  const nodes = allNodes.filter((node) => node.label.toLowerCase().includes(filter.toLowerCase()));
  const selectedNode = allNodes.find((node) => node.id === selected);
  const selectedLabel = topicKeywords.find((item) => item.slug === selected)?.label ?? selectedNode?.label ?? selected;
  // Keep a selected keyword visible in the topic branch even when it falls just
  // outside the compact list of common keywords.
  const treeKeywords = useMemo(() => {
    const compact = topicKeywords.slice(0, 8);
    const selectedItem = topicKeywords.find((item) => item.slug === selected) ?? (selectedNode && selectedNode.topic === topic ? { slug: selectedNode.id, label: selectedNode.label, paper_count: selectedNode.count } : undefined);
    return selectedItem && !compact.some((item) => item.slug === selectedItem.slug) ? [...compact, selectedItem] : compact;
  }, [selected, selectedNode, topic, topicKeywords]);
  const previewQuery = useQuery({ queryKey: ["keyword-preview", preview?.id, topic], queryFn: () => api.papers(new URLSearchParams({ keyword: preview!.id, ...(topic ? { topic } : {}), limit: "3", sort: "atlas_score" })), enabled: !!preview });
  const close = () => { setPreview(null); setPinned(false); };
  const submitExactKeyword = () => {
    const match = findExactKeyword(filter, allNodes);
    if (!match) return;
    setPreview(match);
    setPinned(true);
    onSelect(match.id, match.topic);
  };
  return <section className="keywords-section" aria-labelledby="keywords-title" onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); trigger.current?.focus(); close(); } }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) close(); }} onMouseLeave={() => { if (!pinned && !document.activeElement?.closest(".keywords-section")) close(); }}>
    <div className="sidebar-title"><h2 id="keywords-title">Keywords</h2><span>{network?.nodes.length ?? "—"}</span></div><p className="sidebar-note">Most frequent author keywords</p><input className="keyword-search" aria-label="Find a keyword" aria-describedby="keyword-search-help" placeholder="Find a keyword…" value={filter} onChange={(event) => { setFilter(event.target.value); close(); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); submitExactKeyword(); } }} /><span id="keyword-search-help" className="sr-only">Enter selects an exact keyword and opens its topic path. Use the matching keyword buttons below for partial searches.</span>
    {(topic || selected) && <nav className={`sidebar-tree${selected ? " has-keyword-selection" : ""}`} aria-label="Current keyword scope">{topic ? <><button className="tree-back" onClick={onClearTopic}>← All topics</button><div className="tree-parent active" aria-label={`Selected topic: ${topicLabel}`}><span>{topicLabel}</span><small>topic</small></div><div className="tree-branch"><span className="tree-branch-label">Author keywords</span>{treeKeywords.length ? treeKeywords.map((item) => <button key={item.slug} className={selected === item.slug ? "active" : ""} aria-current={selected === item.slug ? "page" : undefined} onClick={() => onSelect(item.slug, topic)}><span>{item.label}</span><small>{fmt.format(item.paper_count)}</small></button>) : <span className="tree-empty">No recorded keywords</span>}</div></> : <div className="tree-parent keyword-wide-parent"><span>IROS 2026</span><small>keyword-wide</small></div>}{selected && <div className="scope-notice"><span className="scope-notice-kicker">Selected keyword</span><b>{selectedLabel}</b><p>{topic ? "Paper results and the map follow this topic → keyword path." : "Paper results and the map follow this keyword across the conference."}</p><button className="scope-clear" onClick={onClearKeyword}>Clear selection ×</button></div>}</nav>}
    <div className="keyword-list-label" aria-hidden="true"><span>All keywords</span><span>Scroll ↕</span></div>
    {error ? <QueryError label="Keywords unavailable." retry={retry} /> : loading ? <Loading label="Loading keywords" /> : <div className="keyword-list" aria-label="Scrollable keyword list" tabIndex={0}>{nodes.map((node) => <button key={node.id} className={selected === node.id ? "active" : ""} aria-pressed={selected === node.id} aria-expanded={preview?.id === node.id} aria-controls={preview?.id === node.id ? "keyword-preview" : undefined} onFocus={(event) => { trigger.current = event.currentTarget; setPreview(node); setPinned(true); }} onMouseEnter={() => { if (!pinned) setPreview(node); }} onClick={(event) => { trigger.current = event.currentTarget; setPreview(node); setPinned(true); onSelect(node.id, node.topic); }}><span>{node.label}</span><small>{fmt.format(node.count)}</small></button>)}{!nodes.length && <p className="empty">No matching keywords.</p>}</div>}
    {preview && <div id="keyword-preview" className="keyword-popover" role="region" aria-label={`Top papers for ${preview.label}`}><div className="popover-heading"><div><p className="eyebrow">Keyword spotlight</p><h3>{preview.label}</h3></div><button className="close-button" aria-label="Close keyword preview" onClick={() => { trigger.current?.focus(); close(); }}>×</button></div><p className="sidebar-note">Highest Atlas Scores{topic ? ` in ${label(topic)}` : ""}. Search results may include papers not eligible for the leaderboard.</p>{previewQuery.isLoading ? <Loading label="Loading top papers" /> : previewQuery.isError ? <QueryError label="Paper preview unavailable." retry={() => previewQuery.refetch()} /> : <ol className="preview-papers">{previewQuery.data?.papers.map((paper) => <li key={paper.paper_number}><Link to={`/papers/${paper.paper_number}`}>{paper.title}</Link><span>{paper.score ? `${paper.score.score.toFixed(1)} Atlas Score${paper.score.eligible ? "" : " · not ranked"}` : "Score unavailable"}</span></li>)}</ol>}{previewQuery.data?.count === 0 && <p className="empty">No papers match this keyword{topic ? " and topic" : ""}.</p>}<Link className="text-link" to={`/papers?${new URLSearchParams({ keyword: preview.id, ...(topic ? { topic } : {}) })}`}>View all matching papers →</Link></div>}
  </section>;
}

function KeywordMap({ network, topic = "", selected = "", onSelect }: { network?: Network; topic?: string; selected?: string; onSelect: (id: string, parentTopic?: string) => void }) {
  const [edgeThreshold, setEdgeThreshold] = useState(1);
  const [nodeSearch, setNodeSearch] = useState("");
  const [focusedId, setFocusedId] = useState(selected);
  const [reducedMotion, setReducedMotion] = useState(false);
  const chartElement = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const nodes = useMemo(() => [...(network?.nodes ?? []).sort((a, b) => b.count - a.count).slice(0, 84)], [network]);
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const availableEdges = useMemo(() => (network?.edges ?? []).filter((edge) => byId.has(edge.source) && byId.has(edge.target)).sort((a, b) => b.count - a.count).slice(0, 220), [network, byId]);
  const maxEdgeCount = Math.max(1, ...availableEdges.map((edge) => edge.count));
  const edges = useMemo(() => availableEdges.filter((edge) => edge.count >= edgeThreshold), [availableEdges, edgeThreshold]);
  const found = nodeSearch.trim() ? nodes.find((node) => node.label.toLowerCase().includes(nodeSearch.trim().toLowerCase()) || node.id === nodeSearch.trim()) : undefined;
  const active = byId.get(selected || focusedId) ?? found;
  const neighbors = useMemo(() => !active ? [] : availableEdges.filter((edge) => edge.source === active.id || edge.target === active.id).map((edge) => ({ edge, node: byId.get(edge.source === active.id ? edge.target : edge.source)! })).filter((item) => !!item.node).sort((a, b) => b.edge.count - a.edge.count).slice(0, 8), [active, availableEdges, byId]);
  const onSelectRef = useRef(onSelect);
  const nodesRef = useRef(nodes);
  const byIdRef = useRef(byId);

  useEffect(() => { setFocusedId(selected); }, [selected]);
  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);
  useEffect(() => { nodesRef.current = nodes; byIdRef.current = byId; }, [nodes, byId]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!chartElement.current) return;
    const chart = echarts.init(chartElement.current, undefined, { renderer: "svg" });
    chartRef.current = chart;
    const resize = () => chart.resize();
    const observer = new ResizeObserver(resize); observer.observe(chartElement.current);
    const click = (event: any) => { if (event.dataType !== "node") return; const node = byIdRef.current.get(event.data.id); if (node) { setFocusedId(node.id); setNodeSearch(node.label); onSelectRef.current(node.id, node.topic); } };
    const resetOnBackgroundDoubleClick = (event: any) => { if (!event.target) chart.dispatchAction({ type: "restore" }); };
    chart.on("click", click); chart.getZr().on("dblclick", resetOnBackgroundDoubleClick);
    return () => { observer.disconnect(); chart.off("click", click); chart.getZr().off("dblclick", resetOnBackgroundDoubleClick); chart.dispose(); chartRef.current = null; };
  }, []);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !nodes.length) return;
    const topicIds = [...new Set(nodes.map((node) => node.topic))];
    const categoryIndex = new Map(topicIds.map((id, index) => [id, index]));
    const maxCount = Math.max(1, ...nodes.map((node) => node.count));
    chart.setOption({
      animation: !reducedMotion,
      animationDuration: reducedMotion ? 0 : 550,
      animationDurationUpdate: reducedMotion ? 0 : 260,
      aria: { enabled: true, label: { description: "Interactive keyword co-occurrence network. Use the accessible node index after the map to select a keyword with the keyboard." } },
      tooltip: { trigger: "item", confine: true, formatter: (params: any) => params.dataType === "node" ? `<b>${params.data.name}</b><br/>${fmt.format(params.data.value)} papers<br/>Topic: ${label(params.data.topic)}<br/>${params.data.neighborCount} recorded neighbors` : `${params.data.value} shared papers` },
      series: [{ type: "graph", layout: "force", roam: true, draggable: true, cursor: "grab", emphasis: { focus: "adjacency", lineStyle: { width: 3, opacity: 1 } }, blur: { itemStyle: { opacity: .28 }, lineStyle: { opacity: .08 } }, label: { show: true, position: "right", color: "#2f4a3b", fontSize: 10, formatter: (params: any) => params.data.value >= 75 ? params.data.name : "" }, force: { initLayout: "circular", repulsion: 250, gravity: 0.085, edgeLength: [55, 165], friction: 0.72, layoutAnimation: !reducedMotion }, data: nodes.map((node, index) => {
        const neighborCount = availableEdges.filter((edge) => edge.source === node.id || edge.target === node.id).length;
        return { id: node.id, name: node.label, value: node.count, topic: node.topic, neighborCount, category: categoryIndex.get(node.topic), draggable: true, symbolSize: 13 + Math.sqrt(node.count / maxCount) * 25, itemStyle: { color: topicColors[index % topicColors.length], borderColor: "#fff", borderWidth: 1.5, shadowBlur: 3, shadowColor: "#1f604933" } };
      }), links: edges.map((edge) => ({ source: edge.source, target: edge.target, value: edge.count, lineStyle: { color: "#5b9489", opacity: Math.min(.72, .16 + edge.count / maxEdgeCount), width: Math.min(4, .7 + edge.count / maxEdgeCount * 3) } })), categories: topicIds.map((id, index) => ({ name: label(id), itemStyle: { color: topicColors[index % topicColors.length] } })), lineStyle: { curveness: .08 } }]
    }, { notMerge: true, lazyUpdate: true });
  }, [availableEdges, edges, maxEdgeCount, nodes, reducedMotion]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.dispatchAction({ type: "downplay", seriesIndex: 0 });
    if (!active) return;
    const dataIndex = nodes.findIndex((node) => node.id === active.id);
    if (dataIndex < 0) return;
    chart.dispatchAction({ type: "focusNodeAdjacency", seriesIndex: 0, dataIndex });
    chart.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex });
  }, [active?.id, nodes]);

  if (!nodes.length) return <p className="empty">No keyword connections are available yet.</p>;
  const focus = () => { if (found) { setFocusedId(found.id); setNodeSearch(found.label); chartRef.current?.dispatchAction({ type: "focusNodeAdjacency", seriesIndex: 0, dataIndex: nodes.findIndex((node) => node.id === found.id) }); onSelect(found.id, found.topic); } };
  const zoom = (amount: number) => chartRef.current?.dispatchAction({ type: "graphRoam", seriesIndex: 0, zoom: amount });
  const reset = () => { setNodeSearch(""); setFocusedId(""); setEdgeThreshold(1); chartRef.current?.dispatchAction({ type: "restore" }); };
  const showPapers = () => document.getElementById("paper-search")?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
  return <div className="keyword-map lively-keyword-map" aria-label="Interactive author keyword map">
    <div className="map-toolbar"><form onSubmit={(event) => { event.preventDefault(); focus(); }}><label className="sr-only" htmlFor="map-node-search">Focus a keyword in the network</label><input id="map-node-search" list="map-keywords" value={nodeSearch} onChange={(event) => setNodeSearch(event.target.value)} placeholder="Focus a keyword"/><datalist id="map-keywords">{nodes.map((node) => <option key={node.id} value={node.label}/>)}</datalist><button className="map-control" type="submit" disabled={!found}>Focus</button></form><div className="map-zoom" aria-label="Map zoom controls"><button className="map-control" type="button" onClick={() => zoom(.84)} aria-label="Zoom out">−</button><span>Zoom</span><button className="map-control" type="button" onClick={() => zoom(1.18)} aria-label="Zoom in">+</button><button className="map-control" type="button" onClick={reset} aria-label="Reset map controls">Reset</button></div></div>
    <div className="map-technical-controls"><label>Minimum co-occurrences <input type="range" min="1" max={maxEdgeCount} value={Math.min(edgeThreshold, maxEdgeCount)} onChange={(event) => setEdgeThreshold(Number(event.target.value))}/><b>{Math.min(edgeThreshold, maxEdgeCount)}</b></label><span>{nodes.length} keywords · {edges.length} displayed links</span>{selected && <button className="map-clear" type="button" onClick={() => { setFocusedId(""); onSelect(selected, ""); }}>Clear selected keyword</button>}</div>
    <p className="map-explanation">Drag bubbles to rearrange the network. Drag the background to pan, scroll to zoom, and select a bubble to inspect its real connections. Double-click empty space to reset the view.</p>
    <div ref={chartElement} className="force-map-canvas" role="img" aria-label="Interactive force-directed keyword co-occurrence network" />
    <aside className="map-details" aria-live="polite">{active ? <><div><span className="eyebrow">Connected neighborhood</span><h3>{active.label}</h3><p>{fmt.format(active.count)} papers · assigned topic: {label(active.topic)} · {neighbors.length} displayed neighbor{neighbors.length === 1 ? "" : "s"}</p><button className="map-show-papers" type="button" onClick={showPapers}>Show matching papers</button></div><div><b>Strongest co-occurrences</b>{neighbors.length ? <ol>{neighbors.map(({ edge, node }) => <li key={node.id}><button type="button" onClick={() => { setFocusedId(node.id); setNodeSearch(node.label); onSelect(node.id, node.topic); }}>{node.label}</button><span>{edge.count} shared paper{edge.count === 1 ? "" : "s"}</span></li>)}</ol> : <p>No recorded co-occurrences are displayed at this threshold.</p>}</div></> : <p>Select a bubble to see its paper frequency, topic, and strongest recorded co-occurrences.</p>}</aside>
    <div className="map-a11y-list"><span className="sr-only">Accessible keyword index</span>{nodes.map((node) => <button key={node.id} type="button" aria-pressed={active?.id === node.id} onClick={() => { setFocusedId(node.id); setNodeSearch(node.label); onSelect(node.id, node.topic); }}>{node.label} — {fmt.format(node.count)} papers, {label(node.topic)}</button>)}</div>
  </div>;
}

function PaperSignal({ paper, index }: { paper: Paper; index: number }) {
  return <Link to={`/papers/${paper.paper_number}`} className="signal-card"><div className="signal-top"><span>#{String(index).padStart(2, "0")}</span><Score score={paper.score} /></div><h3>{paper.title}</h3><p>{paper.authors.slice(0, 3).join(" · ")}</p><div className="tags">{paper.topics.slice(0, 2).map((topic) => <span key={topic}>{label(topic)}</span>)}</div></Link>;
}

function Score({ score }: { score?: Paper["score"] }) { return score ? <span className="score"><b>{score.score.toFixed(1)}</b><small>Atlas Score</small></span> : null; }

function EntityRows({ items, start = 0 }: { items: RankingItem[]; start?: number }) { return <div className="entity-rows">{items.map((item, index) => <div className="entity-row" key={`${item.id ?? item.name}`}><span className="rank">{String(start + index + 1).padStart(2, "0")}</span><div><b>{item.name || "Unnamed entry"}</b><small>{fmt.format(item.paper_count ?? 0)} paper{item.paper_count === 1 ? "" : "s"} · {fmt.format(item.topic_breadth ?? 0)} topic{item.topic_breadth === 1 ? "" : "s"}</small></div><strong>{typeof item.score === "number" ? item.score.toFixed(1) : "—"}</strong></div>)}</div>; }

function EntityRankingDialog({ kind, topic, open, triggerRef, onClose }: { kind: "institutions" | "researchers"; topic: string; open: boolean; triggerRef: RefObject<HTMLButtonElement | null>; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const title = kind === "institutions" ? "Institutions" : "Researchers";
  const scope = topic ? ` within ${label(topic)}` : " across the conference";
  const pageSize = 50;
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<RankingItem[]>([]);
  const [total, setTotal] = useState(0);
  const query = useQuery({ queryKey: [kind, "expanded", topic, offset], queryFn: () => api.rankings(kind, new URLSearchParams({ limit: String(pageSize), offset: String(offset), ...(topic ? { topic } : {}) }).toString()), enabled: open });

  useEffect(() => { setOffset(0); setItems([]); setTotal(0); }, [kind, topic, open]);
  useEffect(() => {
    if (!query.data) return;
    setTotal(query.data.count ?? 0);
    setItems((current) => offset === 0 ? query.data.items : [...current, ...query.data.items.filter((item) => !current.some((existing) => existing.id === item.id))]);
  }, [offset, query.data]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.querySelector<HTMLButtonElement>("[data-dialog-close]")?.focus();
    return () => { document.body.style.overflow = previousOverflow; triggerRef.current?.focus(); };
  }, [open, triggerRef]);

  if (!open) return null;
  const hasMore = items.length < total;
  const loadMore = () => { if (hasMore && !query.isFetching) setOffset(items.length); };
  const keepFocusInDialog = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [])].filter((element) => !element.hasAttribute("hidden"));
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  return <div className="entity-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialogRef} className="entity-dialog" role="dialog" aria-modal="true" aria-labelledby={`${kind}-dialog-title`} aria-describedby={`${kind}-dialog-description`} onKeyDown={keepFocusInDialog}>
      <div className="entity-dialog-header"><div><p className="eyebrow">Atlas index</p><h2 id={`${kind}-dialog-title`}>{title}</h2><p id={`${kind}-dialog-description`}>Ranked{scope}. Scroll to inspect the available index.</p></div><button data-dialog-close className="close-button" type="button" onClick={onClose} aria-label={`Close ${title} list`}>×</button></div>
      <div className="entity-dialog-body" tabIndex={0} aria-label={`Scrollable ${title.toLowerCase()} rankings`} onScroll={(event) => { const element = event.currentTarget; if (element.scrollHeight - element.scrollTop - element.clientHeight < 120) loadMore(); }}>
        {query.isError && !items.length ? <QueryError label={`The ${title.toLowerCase()} index could not be loaded.`} retry={() => query.refetch()} /> : query.isLoading && !items.length ? <Loading label={`Loading ${title.toLowerCase()} rankings`} /> : items.length ? <EntityRows items={items} /> : <p className="empty">No {title.toLowerCase()} match this topic.</p>}
        {query.isError && items.length ? <QueryError label={`More ${title.toLowerCase()} could not be loaded.`} retry={() => query.refetch()} /> : null}
        {hasMore ? <div className="entity-dialog-more"><button className="button ghost" type="button" onClick={loadMore} disabled={query.isFetching}>{query.isFetching ? "Loading more…" : `Load more ${title.toLowerCase()}`}</button><span>{fmt.format(items.length)} of {fmt.format(total)}</span></div> : items.length ? <p className="entity-dialog-end" role="status">All {fmt.format(total)} {title.toLowerCase()} loaded.</p> : null}
      </div>
      <p className="entity-dialog-footnote">Results are ranked by the Atlas index{topic ? ` for ${label(topic)}` : ""}. More entries load as you scroll.</p>
    </div>
  </div>;
}

function Rankings() { const query = useQuery({ queryKey: ["paper-rankings"], queryFn: () => api.rankings("papers", "limit=50") }); return <section className="page"><PageIntro eyebrow="Evidence-weighted leaderboard" title="Paper rankings" text="A single Atlas Score, with the signals and confidence behind each placement."/><PaperTable papers={(query.data?.items ?? []) as Paper[]} /></section>; }
function EntityRankings({ kind }: { kind: "institutions" | "researchers" }) { const [params, setParams] = useSearchParams(); const topic = params.get("topic") ?? ""; const query = useQuery({ queryKey: [kind, topic], queryFn: () => api.rankings(kind, new URLSearchParams({ limit: "100", ...(topic ? { topic } : {}) }).toString()) }); const copy = kind === "institutions" ? "IROS 2026 research presence, never institutional prestige." : "Fractional paper scores by source author name. Ambiguous identities remain unmerged."; return <section className="page"><PageIntro eyebrow="Atlas index" title={kind === "institutions" ? "Institutions" : "Researchers"} text={copy}/>{topic && <button className="filter-pill" onClick={() => setParams({})}>Topic: {label(topic)} × Clear</button>}{query.isError ? <QueryError label="The index could not be loaded." retry={() => query.refetch()} /> : query.isLoading ? <Loading label="Loading index" /> : query.data?.items.length ? <EntityRows items={query.data.items}/> : <p className="empty">No matching entries.</p>}</section>; }

function Topic() { const { slug = "" } = useParams(); const query = useQuery({ queryKey: ["topic", slug], queryFn: () => api.topic(slug) }); if (query.isError) return <section className="page"><QueryError label="This topic could not be loaded." retry={() => query.refetch()} /></section>; if (!query.data) return <Loading label="Plotting topic terrain"/>; const data = query.data; return <section className="page"><PageIntro eyebrow={`${data.paper_count} papers`} title={data.label} text="Author keywords and score-ranked papers within this IROS 2026 topic."/><div className="keyword-cloud">{data.keywords.map((keyword) => <Link key={keyword.slug} to={`/papers?${new URLSearchParams({ topic: data.slug, keyword: keyword.slug })}`}>{keyword.label}<b>{keyword.paper_count}</b></Link>)}</div><PaperTable papers={data.rankings}/></section>; }

function PaperExplorer({ embedded = false }: { embedded?: boolean }) {
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const queryText = params.get("q") ?? "";
  useEffect(() => setDraft(queryText), [queryText]);
  const pageSize = 12;
  const offset = Math.min(10000, Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0));
  const search = new URLSearchParams(params);
  search.set("limit", String(pageSize));
  search.set("offset", String(offset));
  const query = useQuery({ queryKey: ["papers", search.toString()], queryFn: () => api.papers(search) });
  const submit = (event: FormEvent) => { event.preventDefault(); const next = new URLSearchParams(params); draft.trim() ? next.set("q", draft.trim()) : next.delete("q"); next.delete("offset"); setParams(next, { preventScrollReset: true }); };
  const remove = (key: string) => { const next = new URLSearchParams(params); next.delete(key); if (key === "topic") next.delete("keyword"); next.delete("offset"); setParams(next, { preventScrollReset: true }); };
  const changePage = (value: number) => { const next = new URLSearchParams(params); next.set("offset", String(value)); setParams(next, { preventScrollReset: true }); };
  const clear = () => { setDraft(""); setParams(new URLSearchParams(), { preventScrollReset: true }); };
  return <div className={embedded ? "explorer embedded" : "page explorer-page"}>{!embedded && <PageIntro eyebrow="Search the program" title="Paper explorer" text="Every indexed paper stays visible, whether or not it has enough external evidence to rank."/>}<form className="search-form" onSubmit={submit}><input aria-label="Search paper catalog" value={draft} maxLength={200} onChange={(event) => setDraft(event.target.value)} placeholder="Search title, author, abstract, keyword…"/><button className="button primary">Search</button><button type="button" className="button ghost" onClick={clear}>Clear all</button></form>
    <div className="filter-row"><span role="status">{query.isError ? "Search unavailable" : query.data ? `${fmt.format(query.data.count)} matching papers` : "Searching catalog…"}</span>{["topic", "keyword", "q", "institution"].map((key) => params.get(key) && <button key={key} className="filter-pill" onClick={() => remove(key)} aria-label={`Clear ${key === "q" ? "search" : key} filter: ${params.get(key)}`}>{key === "q" ? "Search" : label(key)}: {key === "topic" ? label(params.get(key)!) : params.get(key)} ×</button>)}</div>
    {query.isError ? <QueryError label="The catalog query could not be completed." retry={() => query.refetch()} /> : query.isLoading ? <Loading label="Loading papers" /> : query.data?.papers.length ? <PaperTable papers={query.data.papers} /> : <p className="empty">No papers match these filters. Try removing a filter or changing your search.</p>}
    {query.data && query.data.count > pageSize && <nav className="pagination" aria-label="Paper result pages"><button className="button ghost" disabled={offset === 0 || query.isFetching} onClick={() => changePage(Math.max(0, offset - pageSize))}>← Previous</button><span>{offset + 1}–{Math.min(offset + pageSize, query.data.count)} of {fmt.format(query.data.count)}</span><button className="button ghost" disabled={offset + pageSize >= query.data.count || offset + pageSize > 10000 || query.isFetching} onClick={() => changePage(offset + pageSize)}>Next →</button></nav>}
  </div>;
}

function PaperTable({ papers }: { papers: Paper[] }) { return <div className="paper-table">{papers.map((paper) => <article className="paper-row" key={paper.paper_number}><Link className="paper-row-link" to={`/papers/${paper.paper_number}`} aria-label={`Open paper: ${paper.title}`}><div className="paper-meta"><span>{paper.day ?? "IROS"}</span><span>{paper.session_type ?? "Program"}</span></div><div className="paper-main"><h3>{paper.title}</h3><p>{paper.authors.slice(0, 4).join(" · ")}</p><div className="tags">{paper.keywords.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div></div><Score score={paper.score}/><span className="row-arrow" aria-hidden="true">↗</span></Link></article>)}</div>; }

function PaperPage() { const { id = "" } = useParams(); const query = useQuery({ queryKey: ["paper", id], queryFn: () => api.paper(id) }); if (query.isLoading || !query.data) return <Loading label="Opening paper record"/>; const paper = query.data; return <section className="page paper-detail"><Link className="back" to="/papers">← Back to explorer</Link><div className="detail-head"><div><p className="eyebrow">IROS 2026 · #{paper.paper_number}</p><h1>{paper.title}</h1><p className="detail-authors">{paper.authors.join(" · ")}</p></div><Score score={paper.score}/></div><div className="detail-grid"><article><h2>Abstract</h2><p className="abstract">{paper.abstract || "No verified abstract is attached to this record yet."}</p><h2>Research signals</h2><ScoreBreakdown paper={paper}/></article><aside><h2>Program record</h2><dl><dt>Session</dt><dd>{paper.session_name || paper.session_type || "Program"}</dd><dt>When</dt><dd>{[paper.day, paper.time, paper.room].filter(Boolean).join(" · ") || "See official record"}</dd><dt>Institutions</dt><dd>{paper.affiliations?.join(" · ") || "Not listed"}</dd></dl><div className="link-stack">{paper.official_record_url && <a href={paper.official_record_url} target="_blank" rel="noreferrer">Official IROS record ↗</a>}{paper.public_page_url && <a href={paper.public_page_url} target="_blank" rel="noreferrer">Verified public page ↗</a>}{paper.pdf_url && <a href={paper.pdf_url} target="_blank" rel="noreferrer">Open-access PDF ↗</a>}</div></aside></div><h2 className="related-title">Related in the program</h2><PaperTable papers={paper.related_papers ?? []}/></section>; }

function ScoreBreakdown({ paper }: { paper: Paper }) { const parts = paper.score?.components ?? {}; return <div className="breakdown">{Object.entries(parts).map(([key, value]) => <div key={key}><span>{label(key)}</span><i><b style={{ width: `${value}%` }}/></i><strong>{value.toFixed(0)}</strong></div>)}<p>Confidence {Math.round((paper.score?.confidence ?? 0) * 100)}% · v{paper.score?.version}</p></div>; }

function CopyableSetup({ label, value, language = "text" }: { label: string; value: string; language?: string }) {
  const [status, setStatus] = useState("");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setStatus(`Copied ${label}.`);
    } catch {
      setStatus("Copy unavailable. Select the text and copy it manually.");
    }
  };
  return <section className="setup-snippet" aria-label={label}>
    <div className="setup-snippet-heading"><h3>{label}</h3><button className="copy-button setup-copy-button" onClick={copy}>Copy</button></div>
    <pre><code className={`language-${language}`}>{value}</code></pre>
    <p className="copy-status" role="status" aria-live="polite">{status}</p>
  </section>;
}

function Connect() {
  const method = useQuery({ queryKey: ["methodology"], queryFn: api.methodology });
  const endpoint = `${window.location.origin}/mcp/`;
  const jsonConfiguration = `{
  "mcpServers": {
    "iros-atlas": {
      "type": "http",
      "url": "${endpoint}"
    }
  }
}`;
  return <section className="page connect">
    <PageIntro eyebrow="Bring your own agent" title="Connect to Atlas" text="Atlas is a read-only research source. Your agent does the reasoning; Atlas supplies the cited conference evidence."/>
    <div className="connect-grid">
      <article className="mcp-connect-card">
        <p className="eyebrow">Hosted MCP</p>
        <h2>Connect with Streamable HTTP</h2>
        <p>Bring this research catalog into any MCP-compatible client.</p>
        <dl className="connection-details">
          <div><dt>Transport</dt><dd>Streamable HTTP</dd></div>
          <div><dt>Authentication</dt><dd>none</dd></div>
        </dl>
        <CopyableSetup label="MCP endpoint" value={endpoint} />
        <p className="endpoint-note">Use this exact URL, including the trailing slash.</p>
      </article>
      <article>
        <p className="eyebrow">REST fallback</p>
        <h2>Any client can query the same catalog.</h2>
        <code>{window.location.origin}/api/v1/papers?q=diffusion</code>
        <p>REST and MCP share a service layer. Paper identifiers, scores, confidence, and evidence URLs are identical in both interfaces.</p>
        <Link to="/papers">Try a query →</Link>
      </article>
    </div>
    <section className="setup-examples" aria-labelledby="setup-examples-title">
      <div><p className="eyebrow">Generic setup</p><h2 id="setup-examples-title">Configure your MCP client</h2></div>
      <div className="setup-snippets">
        <CopyableSetup label="JSON configuration" value={jsonConfiguration} language="json" />
      </div>
    </section>
    <section id="methodology" className="methodology"><p className="eyebrow">Atlas Score · v{method.data?.version ?? "…"}</p><h2>Every rank explains itself.</h2><div className="weights">{Object.entries(method.data?.weights ?? {}).map(([key, value]) => <div key={key}><span>{label(key)}</span><b>{Math.round(value * 100)}%</b></div>)}</div><ul>{method.data?.rules.map((rule) => <li key={rule}>{rule}</li>)}</ul></section>
  </section>;
}

function PageIntro({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) { return <div className="page-intro"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{text}</p></div>; }
function Loading({ label }: { label: string }) { return <div className="loading"><span className="pulse"/>{label}</div>; }
function QueryError({ label, retry }: { label: string; retry: () => void }) { return <div className="error" role="alert"><p>{label}</p><button className="button ghost" onClick={retry}>Try again</button></div>; }
function Footer() { return <footer><span>Independent explorer. Not an official IROS website.</span><span>Built from the official IROS Paper & Author Index · <Link to="/connect#methodology">Methodology</Link></span></footer>; }

export default App;
