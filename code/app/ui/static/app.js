const { useEffect, useMemo, useState } = React;
const h = React.createElement;
const API = "/api/v1";
const cx = (...v) => v.filter(Boolean).join(" ");
const fmtDate = v => v ? new Date(v).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
const short = (v, n=8) => String(v || "").length > n ? `${String(v).slice(0,n)}…` : (v || "—");
const pretty = v => String(v ?? "—").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
const lobFor = f => {
  const s = `${f?.taxonomy_id || ""} ${f?.rule_id || ""} ${f?.finding_type || ""} ${f?.title || ""}`.toUpperCase();
  if (/\bWC\b|WORKERS.?COMP|PAYROLL|EXPERIENCE.?MOD|N15-CLASS-RATE|CLASS\s+\d{3,4}\s*\(/.test(s)) return "Workers’ comp";
  if (/FLEET|AUTO|VEHICLE|DRIVER|VIN|MVR|GARAGING/.test(s)) return "Commercial auto";
  if (/PROPERTY|BUILDING|LOCATION|SOV|TIV|OCCUPANCY/.test(s)) return "Property";
  if (/GL\b|LIABILITY|OPERATIONS|SALES|ACORD.?126/.test(s)) return "General liability";
  return "Submission-wide";
};
const lobShort = s => ({"Workers’ comp":"WC", "Commercial auto":"Auto", "Property":"Property", "General liability":"GL", "Submission-wide":"General"}[s] || "General");
const esc = v => typeof v === "object" ? JSON.stringify(v, null, 2) : String(v ?? "—");

function Icon({name, size=18}) {
  const paths = {
    grid:"M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z", briefcase:"M3 7h18v14H3zM8 7V4h8v3M3 12h18M10 12v2h4v-2",
    pulse:"M3 12h4l3-8 4 16 3-8h4", search:"M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16ZM21 21l-4.4-4.4", settings:"M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM19 13.5a7.6 7.6 0 0 0 0-3l2-1.5-2-3.5-2.4 1a8 8 0 0 0-2.6-1.5L13.6 2h-3.2L10 5a8 8 0 0 0-2.6 1.5L5 5.5 3 9l2 1.5a7.6 7.6 0 0 0 0 3L3 15l2 3.5 2.4-1a8 8 0 0 0 2.6 1.5l.4 3h3.2l.4-3a8 8 0 0 0 2.6-1.5l2.4 1 2-3.5z",
    arrow:"M5 12h14m-7-7 7 7-7 7", swap:"M4 7h15m-4-4 4 4-4 4M20 17H5m4 4-4-4 4-4", chevron:"m9 18 6-6-6-6", back:"m15 18-6-6 6-6", file:"M5 3h9l5 5v13H5zM14 3v5h5M8 13h8M8 17h8", check:"m5 12 4 4L19 6", clock:"M12 8v4l3 2m6-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z", spark:"m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3ZM19 15l1 2.5 2.5 1-2.5 1L19 22l-1-2.5-2.5-1 2.5-1z", external:"M14 4h6v6m-11 4L20 4M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6", warning:"M12 3 2 21h20L12 3Zm0 7v4m0 3h.01", shield:"M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"
  };
  return h("svg", {width:size,height:size,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:1.7,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":"true"}, h("path", {d:paths[name]||paths.grid}));
}
function Badge({children, tone="neutral"}) { return h("span", {className:`badge badge-${String(tone).toLowerCase().replaceAll("/","-")}`}, children); }
function ScoreBadge({score}) { if(!score) return h("span",{className:"score-badge score-none"},"—"); const grade=String(score.grade||"").toLowerCase(); return h("span",{className:`score-badge grade-${grade}`,title:"Submission quality score (0-100)"},`${Math.round(score.score)}`,h("em",null,score.grade)); }
function ProgressBar({job}) { const pct=Math.max(2,Math.min(100,job.percent||0)); const failed=job.state==="FAILED"; return h("div",{className:cx("progress",failed&&"progress-failed")},h("div",{className:"progress-track",role:"progressbar","aria-valuenow":Math.round(job.percent||0)},h("span",{className:"progress-fill",style:{width:`${pct}%`}})),h("div",{className:"progress-meta"},h("span",null,failed?"Run failed: ":"",job.message||job.stage_name||"Starting…"),h("strong",null,failed?"FAILED":`${Math.round(job.percent||0)}%`))); }
function Button({children,onClick,kind="secondary",icon,disabled=false,title}) { return h("button", {className:`button button-${kind}`,onClick,disabled,title}, icon && h(Icon,{name:icon,size:16}), children); }
function Empty({title,copy,action}) { return h("div",{className:"empty-state"},h("span",{className:"empty-icon"},h(Icon,{name:"file",size:22})),h("h3",null,title),h("p",null,copy),action); }

function App() {
  const [runs,setRuns]=useState([]), [runData,setRunData]=useState({}), [active,setActive]=useState(null), [tab,setTab]=useState("overview"), [selectedFinding,setSelectedFinding]=useState(null);
  const [sidebarHidden,setSidebarHidden]=useState(()=>{try{return localStorage.getItem("spectra_sidebar_hidden")==="true"}catch{return false}});
  const [reviewerActions,setReviewerActions]=useState(()=>{try{return JSON.parse(localStorage.getItem("spectra_reviewer_actions")||"{}")}catch{return{}}});
  const [loading,setLoading]=useState(true), [error,setError]=useState(""), [query,setQuery]=useState(""), [statusFilter,setStatusFilter]=useState("All status"), [lobFilter,setLobFilter]=useState("All lines"), [token,setToken]=useState(sessionStorage.getItem("spectra_api_token")||""), [settingsOpen,setSettingsOpen]=useState(false), [refreshing,setRefreshing]=useState(false);
  const [accounts,setAccounts]=useState([]), [jobs,setJobs]=useState({});
  const headers = useMemo(()=> token ? {Authorization:`Bearer ${token}`} : {},[token]);
  const request=async path=>{const r=await fetch(`${API}${path}`,{headers}); if(!r.ok) throw new Error(r.status===401?"API token required or invalid. Open Settings to add it.":`API request failed (${r.status})`); return r.json();};
  const refresh=async()=>{setRefreshing(true);setError("");try{const [d,ad]=await Promise.all([request(`/reviews`),request(`/accounts`).catch(()=>({accounts:[]}))]);const rs=d.runs||[];setRuns(rs);setAccounts(ad.accounts||[]);if(rs.length){const batch=await Promise.all(rs.map(async x=>{try{return [x.run_id,await request(`/reviews/${encodeURIComponent(x.run_id)}`)]}catch{return [x.run_id,null]}}));setRunData(Object.fromEntries(batch.filter(x=>x[1])))}else setRunData({});}catch(e){setError(e.message)}finally{setLoading(false);setRefreshing(false)}};
  const pollProgress=(account,runId)=>{const tick=async()=>{try{const p=await request(`/runs/${encodeURIComponent(runId)}/progress`);setJobs(j=>({...j,[account]:p}));if(p.state==="COMPLETED"||p.state==="FAILED"){clearInterval(timer);await refresh();setTimeout(()=>setJobs(j=>{const n={...j};delete n[account];return n}),8000)}}catch(e){clearInterval(timer);setJobs(j=>({...j,[account]:{...(j[account]||{run_id:runId,account}),state:"FAILED",message:e.message}}))}};const timer=setInterval(tick,900);tick()};
  const startRun=async account=>{setError("");setJobs(j=>({...j,[account]:{run_id:"",account,state:"QUEUED",percent:0,stage_name:"queued",message:"Queued"}}));try{const r=await fetch(`${API}/accounts/${encodeURIComponent(account)}/run?deterministic_only=false`,{method:"POST",headers});if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||`Could not start run (${r.status})`)}const d=await r.json();setJobs(j=>({...j,[account]:{...(j[account]||{}),run_id:d.run_id}}));pollProgress(account,d.run_id)}catch(e){setJobs(j=>({...j,[account]:{...(j[account]||{account}),state:"FAILED",message:e.message}}))}};
  useEffect(()=>{refresh()},[headers]);
  useEffect(()=>{const parse=()=>{const parts=location.hash.slice(1).split("/").map(decodeURIComponent);const [kind,id,sub,fid]=parts;if(kind==="run"&&id){setActive(id);setSelectedFinding(sub==="finding"?(fid||null):null)}else{setActive(null);setSelectedFinding(null)}};parse();window.addEventListener("hashchange",parse);return()=>window.removeEventListener("hashchange",parse)},[]);
  const openRun=id=>{location.hash=`run/${encodeURIComponent(id)}`;setTab("overview")};
  const openFinding=(runId,id)=>{location.hash=`run/${encodeURIComponent(runId)}/finding/${encodeURIComponent(id)}`;setTab("findings")};
  const toggleSidebar=()=>setSidebarHidden(hidden=>{const next=!hidden;try{localStorage.setItem("spectra_sidebar_hidden",String(next))}catch{}return next});
  const saveReviewerAction=(runId,findingId,record)=>setReviewerActions(previous=>{const next={...previous,[`${runId}:${findingId}`]:record};try{localStorage.setItem("spectra_reviewer_actions",JSON.stringify(next))}catch{}return next});
  const back=()=>{if(selectedFinding)location.hash=`run/${encodeURIComponent(active)}`;else location.hash=""};
  const current=runData[active];
  const filteredRuns=runs.filter(r=>`${r.submission_id} ${r.run_id} ${r.status}`.toLowerCase().includes(query.toLowerCase())&&(statusFilter==="All status"||r.status===statusFilter));
  const withReviewerAction=(rid,f)=>{const reviewerAction=reviewerActions[`${rid}:${f.finding_id}`];return {...f,...(reviewerAction?.closed?{status:"CLOSED"}:{}),reviewerAction};};
  const allFindings=Object.entries(runData).flatMap(([rid,d])=>(d?.findings||[]).map(f=>({...withReviewerAction(rid,f),_runId:rid,_submissionId:d.submission_id})));
  const openFindings=allFindings.filter(f=>["OPEN","NEEDS_REVIEW"].includes(f.status));
  const topFindings=(current?.findings||[]).map(f=>withReviewerAction(active,f)).sort((a,b)=>severityRank(a.severity)-severityRank(b.severity));
  const activeFinding=selectedFinding&&current?.findings?.find(x=>x.finding_id===selectedFinding);
  const activeReviewerAction=activeFinding&&reviewerActions[`${active.run_id||active}:${selectedFinding}`];
  const lobNames=["All lines","Workers’ comp","General liability","Commercial auto","Property","Submission-wide"];
  const shownFindings=topFindings.filter(f=>lobFilter==="All lines"||lobFor(f)===lobFilter);
  const counts={critical:allFindings.filter(f=>f.severity==="CRITICAL").length,high:allFindings.filter(f=>f.severity==="HIGH").length,open:openFindings.length,findings:allFindings.length};
  const tabs=[["overview","Overview","grid"],["findings","Findings","warning"],["workflow","Workflow","pulse"],["evidence","Evidence","file"],["checks","Checks & exports","shield"]];
  return h("div",{className:cx("app-shell",sidebarHidden&&"sidebar-hidden")},
    h("aside",{className:"sidebar"},
      h("div",{className:"brand"},h("div",{className:"brand-mark"},"S"),h("div",{className:"brand-copy"},h("strong",null,"spectra"),h("span",null,"SUBMISSION INTELLIGENCE")),h("button",{className:"sidebar-collapse-toggle",onClick:toggleSidebar,title:"Hide sidebar","aria-label":"Hide sidebar"},h(Icon,{name:"back",size:16}))),
      h("div",{className:"workspace-label"},"WORKSPACE"),
      h("button",{className:cx("nav-item",!active&&"selected"),onClick:()=>location.hash=""},h(Icon,{name:"grid"}),h("span",null,"Overview"),h("span",{className:"nav-count"},runs.length)),
      h("div",{className:"workspace-label nav-section-label"},"REVIEW QUEUE"),
      h("button",{className:cx("nav-item",active&&"selected"),onClick:()=>active?setTab("findings"):document.getElementById("run-list")?.scrollIntoView({behavior:"smooth"})},h(Icon,{name:"briefcase"}),h("span",null,"Account runs"),h("span",{className:"nav-count"},runs.length)),
      h("div",{className:"sidebar-bottom"},h("div",{className:"status-card"},h("span",{className:"status-dot"}),h("div",null,h("strong",null,"Review engine"),h("span",null,error?"Connection issue":"Connected to local API"))),h("button",{className:"nav-item settings-link",onClick:()=>setSettingsOpen(true)},h(Icon,{name:"settings"}),h("span",null,"Settings"))),
    ),
    h("main",{className:"main-area"},
      h("header",{className:"topbar"},h("div",{className:"topbar-leading"},sidebarHidden&&h("button",{className:"sidebar-show-toggle",onClick:toggleSidebar,title:"Show sidebar","aria-label":"Show sidebar"},h(Icon,{name:"grid",size:17})),h("div",{className:"breadcrumbs"},h("span",null,"Workspace"),h(Icon,{name:"chevron",size:14}),h("span",{className:active?"":"crumb-current",onClick:()=>location.hash=""},active?"Account runs":"Overview"),active&&h(React.Fragment,null,h(Icon,{name:"chevron",size:14}),h("span",{className:"crumb-current"},current?.submission_id||"Loading account")))),
        h("div",{className:"top-actions"},h("span",{className:"live-label"},h("span",{className:"status-dot"}),"LIVE DATA"),h("button",{className:"icon-button",title:"Refresh",onClick:refreshing?undefined:refresh},h(Icon,{name:"pulse"})),h("button",{className:"avatar-button",onClick:()=>setSettingsOpen(true)},"R"))),
      error&&h("div",{className:"alert alert-error"},h(Icon,{name:"warning"}),h("span",null,error),h("button",{onClick:()=>setSettingsOpen(true)},"Configure API access")),
      active ? current ? h("section",{className:"workspace-content"},
        selectedFinding ? h(FindingDetail,{finding:activeFinding?withReviewerAction(active,activeFinding):null,runId:active,onBack:back,headers,request,reviewerAction:activeReviewerAction,onSaveDisposition:record=>saveReviewerAction(active,selectedFinding,record)}) : h(React.Fragment,null,
          h("div",{className:"run-heading"},h("div",null,h("button",{className:"back-link",onClick:back},h(Icon,{name:"back",size:15}),"All account runs"),h("div",{className:"title-row"},h("h1",null,current.submission_id||"Account review"),h(Badge,{tone:current.status},pretty(current.status)),current.quality_score&&h(ScoreBadge,{score:current.quality_score})),h("div",{className:"run-subtitle"},"Run ",h("code",null,active),h("span",{className:"bullet"},"·"),"Started ",fmtDate(current.created_at),h("span",{className:"bullet"},"·"),current.partial?"Partial review":"Review complete")),
            h("div",{className:"heading-actions"},h(Button,{kind:"secondary",icon:"external",onClick:()=>setTab("checks")} ,"Export report"),h(Button,{kind:"primary",icon:"pulse",onClick:()=>setTab("workflow")},"View agent trace"))),
          h("div",{className:"tabbar"},tabs.map(([id,label,icon])=>h("button",{key:id,className:cx("tab",tab===id&&"active"),onClick:()=>setTab(id)},h(Icon,{name:icon,size:16}),label,id==="findings"&&current.findings?.length?h("span",{className:"tab-count"},current.findings.length):null))),
          tab==="overview"&&h(Overview,{run:current,findings:topFindings,onFinding:id=>openFinding(active,id),setTab}),
          tab==="findings"&&h(Findings,{findings:shownFindings,lobFilter,setLobFilter,lobNames,onFinding:id=>openFinding(active,id)}),
          tab==="workflow"&&h(Workflow,{run:current,onFinding:id=>openFinding(active,id)}),
          tab==="evidence"&&h(Evidence,{findings:topFindings,onFinding:id=>openFinding(active,id)}),
          tab==="checks"&&h(Checks,{run:current,runId:active,headers})
        )) : h(LoadingPanel,{message:"Loading account review…"}) : h("section",{className:"workspace-content dashboard"},
        h("div",{className:"page-heading"},h("div",null,h("div",{className:"eyebrow"},"ACCOUNT WORKSPACE"),h("h1",null,"Accounts"),h("p",null,"Select an account to start a review run. A new run replaces the account's previous run.")),h(Button,{kind:"secondary",icon:"pulse",onClick:refreshing?undefined:refresh},refreshing?"Refreshing…":"Refresh data")),
        h("div",{className:"welcome-banner"},h("div",{className:"banner-copy"},h("span",{className:"banner-kicker"},h(Icon,{name:"spark",size:14}),"RISK REVIEW AT A GLANCE"),h("h2",null,"Evidence-led reviews,\nwith every decision in view."),h("p",null,"Follow the review from first pass to final validation. Every finding links back to its source."),h("button",{className:"banner-link",onClick:()=>document.getElementById("run-list")?.scrollIntoView({behavior:"smooth"})},"Explore account runs",h(Icon,{name:"arrow",size:16}))),h("div",{className:"banner-art"},h("div",{className:"orbit orbit-a"}),h("div",{className:"orbit orbit-b"}),h("div",{className:"orbit orbit-c"}),h("div",{className:"orbit-core"},h(Icon,{name:"shield",size:35})),h("span",{className:"orbit-tag tag-top"},"SOURCE DATA"),h("span",{className:"orbit-tag tag-right"},"AGENT REVIEW"),h("span",{className:"orbit-tag tag-bottom"},"VALIDATED"))),
        h("div",{className:"section-header"},h("div",null,h("h2",null,"Portfolio snapshot"),h("span",null,"Across loaded review runs")),h("span",{className:"as-of"},refreshing?"Updating…":"Updated "+(runs[0]?fmtDate(runs[0].created_at):"just now"))),
        h("div",{className:"metric-grid"},metric("Total reviews",runs.length,"This account",Icon,{name:"briefcase"}),metric("Open findings",counts.open,"Require attention",Icon,{name:"warning",tone:"amber"}),metric("High severity",counts.high+counts.critical,"Critical + high",Icon,{name:"pulse",tone:"rose"}),metric("Evidence citations",allFindings.reduce((n,f)=>n+(f.evidence?.length||0),0),"Across loaded runs",Icon,{name:"file",tone:"blue"})),
        h("div",{className:"section-header"},h("div",null,h("h2",null,"Accounts"),h("span",null,"Start a review run; the latest run per account is kept")),h("span",{className:"run-total"},`${accounts.length} account${accounts.length===1?"":"s"}`)),
        accounts.length?h("div",{className:"account-grid"},accounts.map(a=>{const job=jobs[a.account];const running=job&&job.state!=="COMPLETED"&&job.state!=="FAILED";return h("div",{className:cx("account-card",running&&"running"),key:a.account},h("div",{className:"account-card-top"},h("span",{className:"account-avatar"},(a.label||"A").slice(0,1).toUpperCase()),h("div",{className:"account-card-title"},h("strong",null,a.label||a.account),h("span",null,`${a.document_count} submission document${a.document_count===1?"":"s"}`)),a.latest_run?h(Badge,{tone:a.latest_run.status},pretty(a.latest_run.status)):h(Badge,{tone:"neutral"},"Not run")),a.latest_run&&h("div",{className:"account-card-meta"},h("span",null,"Quality ",h(ScoreBadge,{score:a.latest_run.quality_score})),h("span",null,"Last run ",fmtDate(a.latest_run.created_at)),h("button",{className:"text-link",onClick:()=>openRun(a.latest_run.run_id)},"Open latest run ",h(Icon,{name:"arrow",size:14}))),job&&h(ProgressBar,{job}),h(Button,{kind:"primary",icon:"pulse",disabled:!!running,onClick:()=>startRun(a.account)},running?"Running…":a.latest_run?"Run again (replaces prior run)":"Start review run"))})):h("div",{className:"quiet-state"},loading?"Loading accounts…":"No account folders found under the configured accounts directory."),
        h("div",{className:"section-header runlist-heading",id:"run-list"},h("div",null,h("h2",null,"Account runs"),h("span",null,"Select a submission to inspect its review")),h("span",{className:"run-total"},`${runs.length} ${runs.length===1?"run":"runs"}`)),
        h("div",{className:"run-toolbar"},h("div",{className:"searchbox"},h(Icon,{name:"search",size:17}),h("input",{value:query,onChange:e=>setQuery(e.target.value),placeholder:"Search accounts or run IDs…"})),h("select",{value:statusFilter,onChange:e=>setStatusFilter(e.target.value)},["All status",...new Set(runs.map(r=>r.status))].map(s=>h("option",{key:s},s)))),
        loading?h(LoadingPanel,{message:"Loading review workspace…"}):filteredRuns.length?h("div",{className:"run-table-wrap"},h("table",{className:"run-table"},h("thead",null,h("tr",null,h("th",null,"ACCOUNT / SUBMISSION"),h("th",null,"REVIEW STATUS"),h("th",null,"QUALITY"),h("th",null,"FINDINGS"),h("th",null,"STARTED"),h("th",null,"RUN ID"),h("th",null))),h("tbody",null,filteredRuns.map(r=>{const d=runData[r.run_id],fs=d?.findings||[];return h("tr",{key:r.run_id,onClick:()=>openRun(r.run_id)},h("td",null,h("div",{className:"account-cell"},h("span",{className:"account-avatar"},(r.submission_id||"A").slice(0,1).toUpperCase()),h("div",null,h("strong",null,r.submission_id||"Unnamed submission"),h("span",null,"Review submission")))),h("td",null,h(Badge,{tone:r.status},pretty(r.status))),h("td",null,h(ScoreBadge,{score:d?.quality_score})),h("td",null,h("span",{className:"finding-count"},fs.length),h("span",{className:"finding-count-label"}," findings"),fs.filter(f=>["OPEN","NEEDS_REVIEW"].includes(f.status)).length>0&&h("span",{className:"open-dot"})),h("td",null,h("span",{className:"date-cell"},fmtDate(r.created_at))),h("td",null,h("code",{className:"run-code"},short(r.run_id,12))),h("td",null,h("button",{className:"row-arrow",onClick:e=>{e.stopPropagation();openRun(r.run_id)}},h(Icon,{name:"chevron",size:17}))) )})))):h(Empty,{title:loading?"Loading reviews":"No account runs found",copy:error?"Check API access in Settings and retry.":runs.length?"Adjust the search or status filter to see more runs.":"Create a review through the API or CLI to see it appear here."}),
        h("footer",{className:"page-footer"},h("span",null,"SPECTRA · EVIDENCE-LED SUBMISSION REVIEW"),h("span",null,"Review decisions remain traceable to source evidence"))
      )
    ), settingsOpen&&h(SettingsModal,{token,setToken,onClose:()=>setSettingsOpen(false),onSave:t=>{sessionStorage.setItem("spectra_api_token",t);setToken(t);setSettingsOpen(false)}})
  );
}
function severityRank(s){return ({CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3,INFORMATIONAL:4}[s]??9)}
function metric(title,value,note,IconComp,props){return h("div",{className:"metric-card",key:title},h("div",{className:"metric-top"},h("span",null,title),h("span",{className:`metric-icon ${props.tone||""}`},h(IconComp,props))),h("strong",{className:"metric-value"},value),h("span",{className:"metric-note"},note))}
function LoadingPanel({message}){return h("div",{className:"loading-panel"},h("span",{className:"spinner"}),message)}

function Overview({run,findings,onFinding,setTab}){
  const groups={};findings.forEach(f=>groups[lobFor(f)]=(groups[lobFor(f)]||0)+1);
  const trace=run.stages?.flatMap(s=>s.metrics?.agent_trace||[])||[];
  const citations=findings.reduce((n,f)=>n+(f.evidence?.length||0),0);
  const high=findings.filter(f=>["HIGH","CRITICAL"].includes(f.severity));
  return h("div",{className:"tab-content"},
    h("div",{className:"overview-grid"},h("div",{className:"overview-main"},
      h("div",{className:"panel-card health-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"REVIEW OUTCOME"),h("h2",null,"Decision summary")),h(Badge,{tone:run.status},pretty(run.status))),h("div",{className:"outcome-line"},h("span",{className:`outcome-mark ${String(run.status).toLowerCase().replace("/","-")}`},run.status==="PASS"?h(Icon,{name:"check",size:23}):h(Icon,{name:"warning",size:23})),h("div",null,h("strong",null,run.partial?"Review completed with partial inputs":run.status==="PASS"?"No blocking issues identified":`${findings.length} finding${findings.length===1?"":"s"} identified`),h("span",null,`${citations} source citations · ${trace.length} agent steps recorded`))),h("div",{className:"outcome-foot"},h("span",null,"Started ",h("strong",null,fmtDate(run.created_at))),h("span",null,"Completed ",h("strong",null,fmtDate(run.completed_at))))),
      h("div",{className:"panel-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"PRIORITIZED REVIEW"),h("h2",null,"Needs attention")),h("button",{className:"text-link",onClick:()=>setTab("findings")},"All findings ",h(Icon,{name:"arrow",size:15}))),high.length?high.slice(0,4).map(f=>h(FindingRow,{key:f.finding_id,f,onClick:()=>onFinding(f.finding_id)})):h("div",{className:"quiet-state"},h(Icon,{name:"check",size:17}),"No high or critical findings in this run.")),
      h("div",{className:"panel-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"LINES OF BUSINESS"),h("h2",null,"Finding distribution")),h("button",{className:"text-link",onClick:()=>setTab("findings")},"View by LOB ",h(Icon,{name:"arrow",size:15}))),Object.keys(groups).length?h("div",{className:"lob-bars"},Object.entries(groups).sort((a,b)=>b[1]-a[1]).map(([lob,count])=>h("div",{className:"lob-bar-row",key:lob},h("span",null,lob),h("div",{className:"bar-track"},h("span",{style:{width:`${Math.max(7,count/findings.length*100)}%`}})),h("strong",null,count)))):h("div",{className:"quiet-state"},"No findings to group."))),
    h("div",{className:"overview-rail"},h(QualityPanel,{score:run.quality_score}),h("div",{className:"panel-card rail-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"AGENTIC REVIEW"),h("h2",null,"Workflow activity")),h("span",{className:"live-pill"},h("span",{className:"status-dot"}),"TRACE")),h("div",{className:"mini-trace"},trace.length?trace.slice(0,5).map((ev,i)=>h("div",{className:"mini-trace-row",key:i},h("span",{className:cx("trace-dot",ev.status)},h(Icon,{name:ev.node==="external_research_tool"?"search":ev.node==="feedback_agent"||ev.node==="scope_auditor"?"spark":"check",size:13})),h("div",null,h("strong",null,pretty(ev.node)),h("span",null,ev.reason||`${ev.evidence_count??0} evidence items across ${ev.document_count??0} documents · ${ev.calls??0} model calls`)),h(Badge,{tone:ev.status},pretty(ev.status)))):h("div",{className:"quiet-state"},"No agent trace recorded for this run.")),h("button",{className:"rail-link",onClick:()=>setTab("workflow")},"Open full workflow ",h(Icon,{name:"arrow",size:15}))),h("div",{className:"panel-card rail-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"REVIEW SIGNALS"),h("h2",null,"At a glance"))),h("div",{className:"signal-list"},signal("Findings",findings.length,"warning"),signal("Citations",citations,"file"),signal("Agent steps",trace.length,"pulse"),signal("GPT calls",run.token_usage?.calls||0,"spark"))),h("div",{className:"note-card"},h("span",null,h(Icon,{name:"shield",size:17})),h("div",null,h("strong",null,"Evidence stays connected"),h("p",null,"Every finding keeps its source values and location attached for review.")))))
  );
}
function signal(label,value,icon){return h("div",{className:"signal-row",key:label},h("span",null,h(Icon,{name:icon,size:15}),label),h("strong",null,value))}
function QualityPanel({score}){if(!score)return null;return h("div",{className:"panel-card rail-card quality-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"SUBMISSION QUALITY"),h("h2",null,"Quality score")),h(ScoreBadge,{score})),h("div",{className:"quality-meter"},h("div",{className:"progress-track"},h("span",{className:`progress-fill grade-${String(score.grade).toLowerCase()}`,style:{width:`${Math.max(2,score.score)}%`}})),h("strong",null,`${score.score} / 100`)),score.components?.length?h("div",{className:"quality-components"},score.components.map((c,i)=>h("div",{className:"quality-row",key:c.name||i},h("div",{className:"quality-description"},h("strong",null,pretty(c.name)),h("span",null,c.detail)),h("div",{className:"quality-points"},c.source==="LLM"&&h("span",{className:"quality-source"},"LLM POINTS"),h("span",{className:"quality-lost"},`−${Number(c.points_lost).toFixed(1)}`))))):h("div",{className:"quiet-state"},"No quality deductions — complete, well-cited submission."),h("div",{className:"trace-footnote"},score.method||"spectra-quality-score"));}
function FindingRow({f,onClick,compact=false}){return h("button",{className:cx("finding-row",compact&&"compact"),onClick},h("span",{className:`severity-stripe sev-${String(f.severity).toLowerCase()}`}),h("div",{className:"finding-row-main"},h("div",{className:"finding-title-line"},h("strong",null,f.title||f.finding_type),h("span",{className:"finding-row-badges"},f.status==="CLOSED"&&h(Badge,{tone:"closed"},"Closed"),h(Badge,{tone:f.severity},pretty(f.severity)))),h("div",{className:"finding-row-meta"},h("span",{className:`lob-chip lob-${lobShort(lobFor(f)).toLowerCase()}`},lobFor(f)),f.issue_nature&&h("span",{className:"issue-nature-tag"},pretty(f.issue_nature)),h("span",null,short(f.rule_id,24)),h("span",null,(f.evidence||[]).length+" citations"))),h("span",{className:"row-chevron"},h(Icon,{name:"chevron",size:17})));}

function Findings({findings,lobFilter,setLobFilter,lobNames,onFinding}){
  const grouped=groupFindings(findings);
  const filterBar=h("div",{className:"filter-pills"},lobNames.map(l=>{
    const count=l==="All lines"?findings.length:findings.filter(f=>lobFor(f)===l).length;
    return h("button",{key:l,className:cx("filter-pill",lobFilter===l&&"active"),onClick:()=>setLobFilter(l)},l,h("span",{className:"filter-count"},count));
  }));
  const categories=findings.length?grouped.map(([lob,families])=>{
    const total=families.reduce((n,entry)=>n+entry[1].length,0);
    const subgroupNodes=families.map(([key,items])=>{
      const high=items.filter(f=>["HIGH","CRITICAL"].includes(f.severity)).length;
      const rows=items.map(f=>h(FindingRow,{f,key:f.finding_id,onClick:()=>onFinding(f.finding_id)}));
      return h("details",{className:"finding-subgroup",key},
        h("summary",null,
          h("span",{className:"subgroup-chevron"},h(Icon,{name:"chevron",size:15})),
          h("span",{className:"subgroup-name"},items[0].issue_group_title||friendlyGroupTitle(items[0])),
          h("span",{className:"subgroup-count"},items.length),
          high>0?h("span",{className:"subgroup-severity"},`${high} high`):null,
          h("span",{className:"subgroup-hint"},"Open findings")),
        h("div",{className:"finding-list"},rows));
    });
    return h("section",{className:"finding-group finding-category",key:lob},
      h("div",{className:"group-title"},
        h("span",{className:`lob-mark lob-${lobShort(lob).toLowerCase()}`},lobShort(lob)),
        h("strong",null,lob),h("span",null,`${total} findings · ${families.length} issue families`)),
      h("div",{className:"finding-subgroups"},subgroupNodes));
  }):h(Empty,{title:"No findings in this view",copy:"Try another line of business or inspect the account overview."});
  return h("div",{className:"tab-content"},
    h("div",{className:"section-header inner-section"},
      h("div",null,h("h2",null,"Review findings"),h("span",null,"Choose a line of business, then expand an issue family to inspect individual findings")),
      h("span",{className:"run-total"},`${findings.length} findings`)),
    filterBar,categories);
}
function friendlyGroupTitle(f){const titles={HEAVY_VEHICLE_LICENSE_CONFLICT:"Heavy-vehicle driver qualification",GARAGING_STATE_CONFLICT:"Fleet garaging address and state",LOCATION_COUNT_CONFLICT:"Location count reconciliation",LOCATION_SET_CONFLICT:"Location identity and address",EMPLOYEE_COUNT_CONFLICT:"Employee count reconciliation",DECLARED_EXPOSURE_MISMATCH:"Declared exposure versus support",EXPERIENCE_MOD_MISMATCH:"Experience modification factor",ANNUAL_CLAIM_COUNT_MISMATCH:"Annual claim count reconciliation",WC_STATE_COVERAGE_QUESTION:"Workers’ compensation state allocation",GL_SALES_BASIS_UNRECONCILED:"General liability sales and area basis",GL_AREA_BASIS_UNRECONCILED:"General liability sales and area basis",EXTERNAL_RECORD_POSSIBLE_MATCH:"External record identity review",UNDECLARED_OPERATION_SIGNAL:"Undeclared operation signal",MISSING_REQUIRED_DOCUMENT:"Missing submission evidence"};return titles[f.finding_type]||pretty(f.finding_type||"Issue family");}
function groupFindings(fs){const lines=new Map();fs.forEach(f=>{const lob=lobFor(f),key=f.issue_group_key||f.finding_type||f.finding_id;if(!lines.has(lob))lines.set(lob,new Map());const families=lines.get(lob);if(!families.has(key))families.set(key,[]);families.get(key).push(f)});return [...lines.entries()].map(([lob,families])=>[lob,[...families.entries()].sort((a,b)=>b[1].length-a[1].length||String(a[0]).localeCompare(String(b[0])))]).sort((a,b)=>a[0].localeCompare(b[0]));}

function Workflow({run,onFinding}){
  const stages=run.stages||[];
  const trace=stages.flatMap(s=>(s.metrics?.agent_trace||[]).map(e=>({...e,_stage:s.stage})));
  const research=trace.filter(e=>e.node==="external_research_tool");
  return h("div",{className:"tab-content"},
    h("div",{className:"section-header inner-section"},
      h("div",null,h("h2",null,"Agent workflow trace"),h("span",null,"Execution path, tool use, and validation outcomes")),
      h("span",{className:"live-pill"},h("span",{className:"status-dot"}),"LANGGRAPH")),
    h("div",{className:"workflow-layout"},
      h("div",{className:"workflow-main"},
        h("div",{className:"panel-card workflow-card"},
          h("div",{className:"workflow-head"},h("div",null,h("span",{className:"eyebrow"},"ORCHESTRATED RUN"),h("h3",null,"Review orchestration")),h(Badge,{tone:run.status},pretty(run.status))),
          h("div",{className:"flow-line"}),
          trace.length?trace.map((ev,i)=>h(TraceStep,{key:`${ev.node}-${i}`,event:ev,index:i,total:trace.length,onFinding})):h(Empty,{title:"No agent trace available",copy:"This run may have completed without an agentic workflow trace."})),
        h("div",{className:"panel-card workflow-stage-card"},
          h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"REVIEW PIPELINE"),h("h3",null,"Stage status"))),
          stages.length?h("div",{className:"stage-list"},stages.map((s,i)=>h("div",{className:"stage-row",key:`${s.stage}-${i}`},h("span",{className:cx("stage-node",`stage-${String(s.status).toLowerCase()}`)},s.status==="COMPLETED"?h(Icon,{name:"check",size:13}):String(i+1).padStart(2,"0")),h("div",null,h("strong",null,pretty(s.stage)),h("span",null,fmtDate(s.finished_at||s.started_at))),h(Badge,{tone:s.status},pretty(s.status))))):h("div",{className:"quiet-state"},"Stage details unavailable."),
          run.token_usage&&h("div",{className:"usage-card"},h("span",null,"MODEL USAGE"),h("div",null,h("strong",null,run.token_usage.calls||0)," calls"),h("div",null,(run.token_usage.input_tokens||0).toLocaleString()," in · ",(run.token_usage.output_tokens||0).toLocaleString()," out"),run.token_usage.estimated_cost_usd!=null&&h("div",null,"Estimated ",h("strong",null,`$${Number(run.token_usage.estimated_cost_usd).toFixed(4)}`))))),
      h("div",{className:"workflow-side"},
        h("div",{className:"panel-card"},
          h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"TOOL ACCESS"),h("h3",null,"External research"))),
          research.length?research.map((e,i)=>h("div",{className:"source-access",key:i},h("span",{className:"source-icon"},h(Icon,{name:"search",size:17})),h("div",null,h("strong",null,pretty(e.status)),h("span",null,e.reason||`${e.evidence_count||0} records returned`)),h(Badge,{tone:e.status},e.live_calls?"Live":"Local"),h("div",{className:"source-metrics"},h("div",null,h("strong",null,e.cache_hits||0),h("span",null,"cache hits")),h("div",null,h("strong",null,e.live_calls||0),h("span",null,"live calls"))))):h("div",{className:"quiet-state"},"No external research event."),
          h("div",{className:"trace-footnote"},"External source availability and tool errors are recorded with this run.")),
        h("div",{className:"note-card"},h("span",null,h(Icon,{name:"clock",size:17})),h("div",null,h("strong",null,"Trace is run-specific"),h("p",null,"Agent steps, model calls, cache use, and linked findings come from the persisted review stage metrics."))))));
}

function TraceStep({event:ev,index,total,onFinding}){
  const names={deterministic_candidate_generation:"Candidate rule checks",experience_mod_calculation:"Experience-mod calculation",external_research_tool:"External research",intake_agent:"Intake agent",wc_agent:"Workers’ comp agent",gl_agent:"General liability agent",auto_agent:"Auto agent",property_agent:"Property agent",feedback_agent:"Critic & feedback",scope_auditor:"LLM issue judge"};
  const label=names[ev.node]||pretty(ev.node);
  const icon=ev.node==="external_research_tool"?"search":ev.node==="feedback_agent"||ev.node==="scope_auditor"?"spark":"check";
  const caption=ev.node==="deterministic_candidate_generation"?"Candidate generation only; GPT decides what is surfaced":ev.node==="experience_mod_calculation"?"Date-qualified factor calculation for GPT review":ev.node==="external_research_tool"?"Source lookup and cache":ev.node==="feedback_agent"?"Independent critic pass":ev.node==="scope_auditor"?"LLM-as-judge: scope, issue type, severity, and false-positive review":"Specialist evidence review";
  const reason=ev.reason||ev.error||`${ev.evidence_count??0} evidence items considered${ev.model_tier?` · ${pretty(ev.model_tier)} model`:""}`;
  const metrics=[];
  if(ev.calls!==undefined)metrics.push(h("span",{key:"calls"},h(Icon,{name:"spark",size:13}),`${ev.calls} model calls`));
  if(ev.document_count!==undefined)metrics.push(h("span",{key:"documents"},h(Icon,{name:"file",size:13}),`${ev.document_count} source documents`));
  if(ev.cache_hits!==undefined)metrics.push(h("span",{key:"cache"},h(Icon,{name:"clock",size:13}),`${ev.cache_hits} cache hits`));
  if(ev.live_calls!==undefined)metrics.push(h("span",{key:"live"},h(Icon,{name:"external",size:13}),`${ev.live_calls} live calls`));
  if(ev.memory_feedback_count!==undefined)metrics.push(h("span",{key:"memory"},`${ev.memory_feedback_count} memory signals`));
  if(ev.candidate_count!==undefined)metrics.push(h("span",{key:"candidates"},`${ev.candidate_count} candidates`));
  if(ev.accepted_findings!==undefined)metrics.push(h("span",{key:"accepted"},`${ev.accepted_findings} retained · ${ev.dropped_findings||0} dropped`));
  if(ev.calculated_value!==undefined&&ev.calculated_value!==null)metrics.push(h("span",{key:"calculated"},`Calculated factor ${ev.calculated_value}`));
  if(ev.input_findings!==undefined)metrics.push(h("span",{key:"judged"},`${ev.input_findings} candidates judged`));
  if(ev.finding_ids?.length)metrics.push(h("button",{key:"findings",className:"step-findings",onClick:()=>onFinding(ev.finding_ids[0])},`${ev.finding_ids.length} linked finding${ev.finding_ids.length===1?"":"s"} →`));
  return h("div",{className:"workflow-step"},
    h("div",{className:"step-rail"},h("span",{className:cx("step-icon",`step-${String(ev.status).toLowerCase()}`)},h(Icon,{name:icon,size:16})),index<total-1&&h("span",{className:"step-connector"})),
    h("div",{className:"step-content"},
      h("div",{className:"step-heading"},h("div",null,h("h4",null,label),h("span",{className:"step-caption"},caption)),h(Badge,{tone:ev.status},pretty(ev.status))),
      h("p",{className:"step-reason"},reason),
      h("div",{className:"step-metrics"},metrics),
      (ev.formula||ev.severity_distribution||ev.issue_nature_distribution)&&h("details",{className:"trace-notes"},h("summary",null,ev.formula?"Calculation & judge details":"Judge results"),ev.formula&&h("p",null,ev.formula),ev.severity_distribution&&h("p",null,"Severity: ",Object.entries(ev.severity_distribution).map(([k,v])=>`${pretty(k)} ${v}`).join(" · ")),ev.issue_nature_distribution&&h("p",null,"Issue types: ",Object.entries(ev.issue_nature_distribution).map(([k,v])=>`${pretty(k)} ${v}`).join(" · "))),
      ev.document_names?.length>0&&h("details",{className:"trace-notes"},h("summary",null,"Documents considered"),h("ul",null,ev.document_names.map((name,j)=>h("li",{key:j},String(name))))),
      ev.notes?.length>0&&h("details",{className:"trace-notes"},h("summary",null,"Agent notes"),h("ul",null,ev.notes.map((n,j)=>h("li",{key:j},String(n)))))));
}

function Evidence({findings,onFinding}){const all=findings.flatMap(f=>(f.evidence||[]).map(e=>({e,f})));return h("div",{className:"tab-content"},h("div",{className:"section-header inner-section"},h("div",null,h("h2",null,"Evidence & citations"),h("span",null,"Source snapshots attached to review findings")),h("span",{className:"run-total"},`${all.length} citations`)),all.length?h("div",{className:"evidence-grid"},all.map(({e,f},i)=>h("article",{className:"evidence-card",key:e.evidence_id||i},h("div",{className:"evidence-card-top"},h("span",{className:"doc-icon"},h(Icon,{name:"file",size:16})),h("div",null,h("strong",null,e.document_name||e.document_id||"Source document"),h("span",null,e.document_id||"Document id unavailable")),h("span",{className:cx("availability",e.source_location?.available&&"available")},e.source_location?.available?"LOCATED":"SNAPSHOT")),h("div",{className:"evidence-values"},h("div",null,h("span",null,"Source value"),h("strong",null,esc(e.raw_value))),h("div",null,h("span",null,"Normalized"),h("strong",null,esc(e.normalized_value)))),e.snippet&&h("blockquote",null,"“",e.snippet,"”"),h("div",{className:"location-chips"},locationTags(e.source_location).map((tag,j)=>h("span",{key:j},tag)),e.unit&&h("span",null,e.unit),e.period&&h("span",null,e.period),!e.source_location?.available&&h("span",{className:"muted-tag"},"Location not supplied")),h("button",{className:"linked-finding",onClick:()=>onFinding(f.finding_id)},h("span",{className:`lob-mark lob-${lobShort(lobFor(f)).toLowerCase()}`},lobShort(lobFor(f))),h("span",null,f.title||f.finding_type),h(Icon,{name:"arrow",size:14}))))):h(Empty,{title:"No evidence citations found",copy:"This review did not return evidence snapshots on its findings."}));}
function locationTags(l){if(!l)return[];return [l.page&&`Page ${l.page}`,l.sheet&&`Sheet ${l.sheet}`,l.row&&`Row ${l.row}`,l.line_start&&`Lines ${l.line_start}${l.line_end&&l.line_end!==l.line_start?`–${l.line_end}`:""}`,l.cell&&`Cell ${l.cell}`,l.column&&`Column ${l.column}`,l.record_id&&`Record ${l.record_id}`,l.json_path&&`Path ${short(l.json_path,48)}`].filter(Boolean)}

function Checks({run,runId,headers}){const checks=run.checks||[];const download=async(e,format)=>{e.preventDefault();const r=await fetch(`${API}/reviews/${encodeURIComponent(runId)}/export?format=${format}`,{headers});if(!r.ok){alert(r.status===401?"Add a valid API token in Settings before exporting.":"Export request failed.");return}const blob=await r.blob();const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=`spectra-${runId}.${format==="markdown"?"md":format}`;a.click();URL.revokeObjectURL(a.href)};return h("div",{className:"tab-content"},h("div",{className:"section-header inner-section"},h("div",null,h("h2",null,"Checks & exports"),h("span",null,"System checks, stage validations, and report downloads"))),h("div",{className:"checks-grid"},h("div",{className:"panel-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"VALIDATION"),h("h3",null,"Review checks")),h("span",{className:"run-total"},`${checks.length} checks`)),checks.length?h("div",{className:"check-list"},checks.map((c,i)=>h("div",{className:"check-row",key:`${c.check_id}-${i}`},h("span",{className:cx("check-mark",`check-${String(c.status).toLowerCase()}`)},c.status==="COMPLETED"?h(Icon,{name:"check",size:14}):h(Icon,{name:"warning",size:14})),h("div",null,h("strong",null,pretty(c.check_id)),h("span",null,c.detail||"No additional check details provided"),c.finding_ids?.length>0&&h("small",null,`${c.finding_ids.length} linked findings`)),h(Badge,{tone:c.status},pretty(c.status))))):h(Empty,{title:"No checks returned",copy:"There are no top-level check outcomes in this run."})),h("div",{className:"panel-card export-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"DOWNLOAD"),h("h3",null,"Export this review"))),h("p",null,"Download the current account review and its evidence in a format suited to your next step."),[...["json","csv","markdown"].map((f,i)=>h("a",{className:"export-link",key:f,href:`${API}/reviews/${encodeURIComponent(runId)}/export?format=${f}`,onClick:e=>download(e,f)},h("span",{className:"export-icon"},h(Icon,{name:"file",size:17})),h("div",null,h("strong",null,["JSON review","CSV findings","Markdown report"][i]),h("span",null,["Full structured result","Tabular finding rows","Readable review summary"][i])),h(Icon,{name:"arrow",size:15})))],h("div",{className:"config-list"},h("span",null,"RULESET ",h("strong",null,run.config_versions?.ruleset||"—")),h("span",null,"PROFILE ",h("strong",null,run.config_versions?.profile||"—"))))));}

function FindingDetail({finding:f,runId,onBack,request,headers,reviewerAction,onSaveDisposition}){
  const [action,setAction]=useState(reviewerAction?.action||"CONFIRM"),[reviewer,setReviewer]=useState(reviewerAction?.reviewer||""),[rationale,setRationale]=useState(reviewerAction?.rationale||""),[actionModalOpen,setActionModalOpen]=useState(false),[sourceView,setSourceView]=useState(null);
  if(!f)return h("div",{className:"workspace-content"},h(Button,{icon:"back",onClick:onBack},"Back to account"),h(Empty,{title:"Finding no longer available",copy:"The selected finding was not included in this review result."}));
  const evidence=f.evidence||[],comp=f.comparison||{};
  const submit=e=>{e.preventDefault();const closeAfterSubmit=e.nativeEvent.submitter?.value==="close";const record={action,reviewer:reviewer.trim(),rationale:rationale.trim(),closed:closeAfterSubmit||!!reviewerAction?.closed,updated_at:new Date().toISOString()};onSaveDisposition?.(record);setActionModalOpen(false)};
  const sourceApiUrl=url=>url.startsWith("/api/")?url:`${API}${url}`;
  const openSource=async e=>{setSourceView({e,loading:true});try{const r=await fetch(`${API}/reviews/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(e.evidence_id)}/source`,{headers});const meta=await r.json();if(!r.ok)throw new Error(meta.detail||"Could not open source citation");let imageUrl=null;if(meta.preview_url){const image=await fetch(sourceApiUrl(meta.preview_url),{headers});if(!image.ok)throw new Error("Could not render cited PDF page");imageUrl=URL.createObjectURL(await image.blob())}setSourceView({e,meta,imageUrl})}catch(error){setSourceView({e,error:error.message})}};
  const closeSource=()=>{if(sourceView?.imageUrl)URL.revokeObjectURL(sourceView.imageUrl);setSourceView(null)};
  const downloadSource=async()=>{if(!sourceView?.meta?.download_url)return;const r=await fetch(sourceApiUrl(sourceView.meta.download_url),{headers});if(!r.ok)return;const url=URL.createObjectURL(await r.blob());const a=document.createElement("a");a.href=url;a.download=sourceView.meta.file_name||"source-document";a.click();URL.revokeObjectURL(url)};
  const comparison=comp.sources?.length?comp.sources.map((s,i)=>{const linked=evidence.filter(e=>(s.evidence_ids||[]).includes(e.evidence_id));return h("div",{className:cx("comparison-value",s.status==="NOT_LISTED"&&"comparison-missing",s.status==="CONFLICTING"&&"comparison-conflict"),key:`${s.document_id}-${i}`},h("span",null,s.document_name||s.label||`Source ${i+1}`),h("strong",null,esc(s.value)),h("small",null,s.label||pretty(s.status||"Compared")),linked.length>0&&h("div",{className:"comparison-evidence-values"},linked.map((e,j)=>{const path=e.source_location?.json_path||"";const field=path.split(".").pop()?.replace(/\[\d+\]$/," ").trim()||"Source value";return h("span",{key:j},h("small",null,field),esc(e.raw_value))})),linked.length>0&&h("button",{className:"comparison-citation-link",onClick:()=>openSource(linked[0])},`View ${linked.length} cited source${linked.length===1?"":"s"}`))}):comp.values_compared?.length?comp.values_compared.map((v,i)=>h("div",{className:"comparison-value",key:i},h("span",null,evidence[i]?.source_filename||evidence[i]?.document_name||`Compared value ${i+1}`),h("strong",null,esc(v)),evidence[i]?.normalized_value!==undefined&&h("small",null,"Normalized: ",esc(evidence[i].normalized_value)))):evidence.map((e,i)=>h("div",{className:"comparison-value",key:i},h("span",null,e.source_filename||e.document_name||`Evidence ${i+1}`),h("strong",null,esc(e.raw_value)),h("small",null,"Normalized: ",esc(e.normalized_value))));
  const comparisonFlow=h(ComparisonFlow,{finding:f,comparison:comp,evidence,onOpen:openSource});
  const citations=evidence.map((e,i)=>h(CitationCard,{key:e.evidence_id||i,e,index:i,onOpen:openSource}));
  const calculationPanel=comp.calculation&&h(CalculationPanel,{calculation:comp.calculation,evidence,onOpen:openSource});
  const provenance=[kv("Issue nature",f.issue_nature?pretty(f.issue_nature):"Not classified"),kv("Rule",f.rule_id),kv("Rule version",f.rule_version),kv("Taxonomy",f.taxonomy_id),kv("Classification",pretty(f.classification)),kv("Conclusion",pretty(f.conclusion_state)),kv("Confidence basis",pretty(f.confidence?.basis)),kv("Confidence",f.confidence?.value==null?"Not calibrated":`${Math.round(f.confidence.value*100)}%`),kv("Finding ID",f.finding_id)];
  return h("div",{className:"tab-content finding-detail"},
    h("button",{className:"back-link",onClick:onBack},h(Icon,{name:"back",size:15}),"Back to ",f.submission_id||"account"),
    h("div",{className:"finding-detail-head"},h("div",{className:"finding-detail-copy"},h("div",{className:"detail-tags"},h("span",{className:`lob-chip lob-${lobShort(lobFor(f)).toLowerCase()}`},lobFor(f)),f.issue_nature&&h(Badge,{tone:"neutral",children:pretty(f.issue_nature)}),h(Badge,{tone:f.severity},pretty(f.severity)),h(Badge,{tone:f.status},pretty(f.status))),h("h1",null,cleanInlineText(f.title||f.finding_type,evidence)),h("p",null,cleanInlineText(f.explanation||f.uncertainty_reason||"This finding was identified from the review evidence and associated rule.",evidence))),h("div",{className:"finding-detail-actions"},h("button",{className:"button button-primary reviewer-action-button",onClick:()=>setActionModalOpen(true)},"Reviewer action",h(Icon,{name:"arrow",size:15})),h("span",{className:`severity-medallion sev-${String(f.severity).toLowerCase()}`},h(Icon,{name:"warning",size:22}),pretty(f.severity)))),
    h("div",{className:"finding-detail-grid"},
      h("div",{className:"finding-detail-main"},
        h("section",{className:"panel-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"WHY THIS WAS FLAGGED"),h("h2",null,"Finding analysis"))),h("p",{className:"analysis-copy"},cleanInlineText(f.explanation||f.uncertainty_reason||f.possible_consequence||"The review identified an evidence-supported issue. Compare the values below with their cited sources.",evidence)),f.severity_rationale&&h("div",{className:"severity-reason"},h("strong",null,`${pretty(f.severity)} severity`),h("p",null,f.severity_rationale)),comparisonFlow,comparison.length?h("div",{className:"comparison-grid"},comparison):h("div",{className:"quiet-state"},"No compared source values were attached."),ComparisonMeta({comparison:comp}),f.recommended_action&&h("div",{className:"recommendation"},h("span",null,h(Icon,{name:"spark",size:16}),"RECOMMENDED ACTION"),h("p",null,f.recommended_action))),
        calculationPanel,
        h("section",{className:"panel-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"SOURCE SNAPSHOTS"),h("h2",null,"Evidence trail")),h("span",{className:"run-total"},`${evidence.length} citations`)),evidence.length?h("div",{className:"citation-list"},citations):h(Empty,{title:"No citations attached",copy:"The finding does not include source evidence references."})),
        h("section",{className:"panel-card"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"PROVENANCE"),h("h2",null,"Rule & confidence"))),h("div",{className:"provenance-grid"},provenance))),
      reviewerAction&&h("div",{className:"reviewer-action-summary"},h(Badge,{tone:reviewerAction.closed?"closed":"neutral"},reviewerAction.closed?"Disposition closed":"Disposition submitted"),h("strong",null,`${pretty(reviewerAction.action)} · ${reviewerAction.reviewer}`),h("span",null,fmtDate(reviewerAction.updated_at)),h("p",null,reviewerAction.rationale))),
    actionModalOpen&&h(ReviewerActionModal,{finding:f,action,setAction,reviewer,setReviewer,rationale,setRationale,closed:!!reviewerAction?.closed,onSubmit:submit,onClose:()=>setActionModalOpen(false)}),
    sourceView&&h(SourceViewer,{view:sourceView,onClose:closeSource,onDownload:downloadSource})
  );
}
function ReviewerActionModal({finding,action,setAction,reviewer,setReviewer,rationale,setRationale,closed,onSubmit,onClose}){
  return h("div",{className:"reviewer-action-backdrop",onMouseDown:e=>e.target===e.currentTarget&&onClose()},
    h("section",{className:"reviewer-action-modal",role:"dialog","aria-modal":"true","aria-labelledby":"reviewer-action-title"},
      h("header",{className:"reviewer-action-head"},
        h("div",null,h("span",{className:"eyebrow"},"HUMAN REVIEW · LOCAL RECORD"),h("h2",{id:"reviewer-action-title"},"Reviewer action"),h("p",null,"Record a decision for this finding. This action stays in this browser and does not update the review backend.")),
        h("button",{className:"modal-close",type:"button",onClick:onClose,"aria-label":"Close reviewer action"},"×")),
      h("div",{className:"reviewer-action-context"},
        h("span",{className:`lob-chip lob-${lobShort(lobFor(finding)).toLowerCase()}`},lobFor(finding)),
        h("strong",null,finding.title||finding.finding_type),
        h(Badge,{tone:closed?"closed":finding.severity},closed?"Closed":pretty(finding.severity))),
      h("form",{className:"reviewer-action-form",onSubmit:onSubmit},
        h("label",null,"Disposition",h("select",{value:action,onChange:e=>setAction(e.target.value)},["CONFIRM","REJECT_FALSE_POSITIVE","RESOLVE","REQUEST_CLARIFICATION","REFER_SPECIALIST","WAIVE","MARK_NOT_APPLICABLE"].map(x=>h("option",{key:x,value:x},pretty(x))))),
        h("label",null,"Reviewer name",h("input",{required:true,autoComplete:"name",value:reviewer,onChange:e=>setReviewer(e.target.value),placeholder:"Enter reviewer name"})),
        h("label",null,"Decision rationale",h("textarea",{required:true,minLength:3,value:rationale,onChange:e=>setRationale(e.target.value),rows:5,placeholder:"Explain the decision and any supporting details"})),
        h("div",{className:"reviewer-action-meta"},h(Icon,{name:"clock",size:14}),"Timestamp is recorded when you submit."),
        h("footer",{className:"reviewer-action-buttons"},
          h("button",{className:"button button-secondary",type:"submit",value:"save"},closed?"Save changes":"Submit"),
          h("button",{className:"button button-secondary",type:"button",onClick:onClose},"Cancel"),
          h("button",{className:"button button-primary",type:"submit",value:"close"},closed?"Save & keep closed":"Submit & close disposition")))));
}
function CitationCard({e,index,onOpen}){return h("article",{className:"citation-card"},h("div",{className:"citation-number"},String(index+1).padStart(2,"0")),h("div",{className:"citation-body"},h("div",{className:"citation-title"},h("strong",null,e.source_filename||e.document_name||e.document_id||"Source document"),h("code",null,e.evidence_id||"evidence")),e.snippet&&h("blockquote",null,"“",e.snippet,"”"),h("div",{className:"citation-values"},h("span",null,"Source value: ",h("strong",null,esc(e.raw_value))),h("span",null,"Normalized: ",h("strong",null,esc(e.normalized_value)))),h("div",{className:"location-chips"},locationTags(e.source_location).map((x,j)=>h("span",{key:j},x)),e.unit&&h("span",null,e.unit),e.period&&h("span",null,e.period)),h("button",{className:"source-open-button",onClick:()=>onOpen(e)},h(Icon,{name:"file",size:14}),"View original source")));}
function cleanInlineText(value,evidence=[]){let text=String(value||"");for(const e of evidence){if(e.document_id&&e.source_filename)text=text.split(e.document_id).join(e.source_filename)}return text.replace(/GEN-<inline:\d+>/g,"the other submission document").replace(/<inline:\d+>/g,"submission document")}
function SourceViewer({view,onClose,onDownload}){
  const [zoom,setZoom]=useState(100);
  const m=view.meta||{},w=m.workbook_preview||{};
  const textLines=String(m.text_preview||"").split("\n");
  const textPreview=h("div",{className:"text-source-preview"},textLines.map((line,i)=>{
    const n=(m.preview_start_line||1)+i;
    return h("div",{className:cx("text-source-line",n>=m.line_start&&n<=m.line_end&&"text-source-line-highlight"),key:n},h("span",{className:"text-source-line-number"},n),h("code",null,line));
  }));
  const body=m.kind==="pdf"?(view.imageUrl
    ?h("div",{className:"pdf-preview-frame"},h("img",{src:view.imageUrl,alt:`${m.file_name}, page ${m.page||"section"}`,style:{width:`${zoom}%`,maxWidth:"none",maxHeight:"none"}}))
    :h("div",{className:"source-error"},"A PDF page image is not available for this citation."))
    :m.kind==="text"?textPreview
    :h("div",{className:"workbook-preview"},h("div",{className:"sheet-label"},m.sheet||"Workbook sheet",m.cell&&h("strong",null,`Citation cell ${m.cell}`)),w.rows?.length?w.rows.map(row=>h("div",{className:"sheet-row",key:row.row},h("span",{className:"sheet-row-number"},row.row),row.cells.map(cell=>h("div",{className:cx("sheet-cell",cell.coordinate===w.highlight_cell&&"sheet-cell-highlight"),key:cell.coordinate},h("small",null,cell.coordinate),h("strong",null,esc(cell.value)))))):h("div",{className:"source-error"},"No nearby workbook rows are available."));
  const location=m.kind==="pdf"?(m.page?`Page ${m.page}${m.page_range?` · section pages ${m.page_range[0]}–${m.page_range[1]}`:""}`:m.page_range?`Page location not exact · section pages ${m.page_range[0]}–${m.page_range[1]}`:"Page not supplied")
    :m.kind==="text"?`Lines ${m.line_start||1}–${m.line_end||m.line_start||1}`
    :m.sheet?`Sheet ${m.sheet}${m.cell?` · cell ${m.cell}`:""}${m.row?` · row ${m.row}`:""}`:"Source location unavailable";
  const pdfControls=m.kind==="pdf"&&!view.loading&&!view.error&&view.imageUrl&&h("div",{className:"pdf-zoom-toolbar","role":"group","aria-label":"PDF zoom controls"},
    h("span",{className:"pdf-zoom-label"},"ZOOM"),
    h("button",{type:"button",className:"pdf-zoom-button",disabled:zoom<=50,onClick:()=>setZoom(value=>Math.max(50,value-25)),title:"Zoom out","aria-label":"Zoom out"},"−"),
    h("span",{className:"pdf-zoom-level","aria-live":"polite"},`${zoom}%`),
    h("button",{type:"button",className:"pdf-zoom-button",disabled:zoom>=300,onClick:()=>setZoom(value=>Math.min(300,value+25)),title:"Zoom in","aria-label":"Zoom in"},"+"),
    h("button",{type:"button",className:"pdf-zoom-fit",disabled:zoom===100,onClick:()=>setZoom(100),title:"Fit page to viewer"},"Fit page"));
  return h("div",{className:"source-modal-backdrop",onMouseDown:e=>e.target===e.currentTarget&&onClose()},h("section",{className:"source-modal"},
    h("header",{className:"source-modal-head"},h("div",null,h("span",{className:"eyebrow"},"ORIGINAL SOURCE"),h("h2",null,m.file_name||view.e?.source_filename||view.e?.document_name||"Citation preview"),h("p",null,location)),h("button",{className:"modal-close",onClick:onClose,"aria-label":"Close source preview"},"×")),
    pdfControls,
    view.loading?h("div",{className:"source-loading"},"Loading cited source…"):view.error?h("div",{className:"source-error"},view.error):body,
    h("footer",{className:"source-modal-footer"},h("span",null,"Cited value: ",h("strong",null,esc(view.e?.raw_value))),h("button",{className:"button button-secondary",onClick:onDownload},"Download original file"))));
}
function CalculationPanel({calculation:c,evidence,onOpen}){const refs=Object.fromEntries(evidence.map(e=>[e.evidence_id,e]));return h("section",{className:"panel-card calculation-panel"},h("div",{className:"card-heading"},h("div",null,h("span",{className:"eyebrow"},"CALCULATION & BASIS"),h("h2",null,c.label||"Calculation"))),h("div",{className:"calculation-result"},h("div",null,h("small",null,"Formula used"),h("p",null,c.formula)),h("div",null,h("small",null,"Calculated value"),h("strong",null,c.result==null?"Not determinable from supplied sources":esc(c.result)))),c.rationale&&h("p",{className:"calculation-rationale"},c.rationale),c.inputs?.length>0&&h("div",{className:"calculation-inputs"},h("strong",null,"Observed inputs"),c.inputs.map((input,i)=>h("div",{className:"calculation-input",key:`${input.label}-${i}`},h("span",null,input.label),h("b",null,esc(input.value)),(input.evidence_ids||[]).filter(id=>refs[id]).map(id=>h("button",{className:"comparison-citation-link",key:id,onClick:()=>onOpen(refs[id])},`Source · ${refs[id].source_filename||refs[id].document_name||"citation"}${refs[id].source_location?.sheet?` · ${refs[id].source_location.sheet}`:""}${refs[id].source_location?.cell?` · ${refs[id].source_location.cell}`:refs[id].source_location?.page?` · page ${refs[id].source_location.page}`:""}`))))),c.limitations?.length>0&&h("div",{className:"calculation-limitations"},h("strong",null,"Limitations"),h("ul",null,c.limitations.map((item,i)=>h("li",{key:i},item)))));}
function ComparisonMeta({comparison:c}){if(c.delta==null&&c.delta_pct==null&&c.tolerance==null&&!c.entity_alignment)return null;return h("div",{className:"comparison-meta"},c.delta!=null&&h("span",null,"Delta ",h("strong",null,c.delta)),c.delta_pct!=null&&h("span",null,"Delta % ",h("strong",null,`${c.delta_pct}%`)),c.tolerance!=null&&h("span",null,"Tolerance ",h("strong",null,c.tolerance)),c.entity_alignment&&h("span",null,"Entity alignment ",h("strong",null,pretty(c.entity_alignment))));}
function ComparisonFlow({finding,comparison,evidence,onOpen}){
  const byId=Object.fromEntries(evidence.map(e=>[e.evidence_id,e]));
  let sources=(comparison.sources||[]).map((source,i)=>({
    key:`${source.document_id||i}-${i}`,name:source.document_name||source.label||`Source ${i+1}`,
    value:source.value,status:source.status,evidence:(source.evidence_ids||[]).map(id=>byId[id]).filter(Boolean)
  }));
  if(!sources.length){
    const grouped=new Map();
    evidence.forEach(e=>{const key=e.document_id||e.document_name||e.evidence_id;if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(e)});
    sources=[...grouped.entries()].map(([key,items],i)=>({key,name:items[0].source_filename||items[0].document_name||key,value:items.map(e=>e.raw_value).filter(v=>v!=null).join(" · "),status:null,evidence:items}));
  }
  const mismatch=["CONFIRMED_DATA_CONFLICT","POSSIBLE_MISMATCH"].includes(finding.conclusion_state)||sources.some(s=>s.status==="CONFLICTING");
  const evidenceGap=["EVIDENCE_GAP","CHECK_INCOMPLETE"].includes(finding.conclusion_state);
  const needsAttention=mismatch||evidenceGap;
  const outcome=pretty(finding.conclusion_state||finding.issue_nature||"REVIEW");
  const method=comparison.calculation?.formula||comparison.delta_formula||"Normalize fields, align scope and period, then compare";
  const flowNodes=[];
  sources.forEach((source,i)=>{
    const linked=source.evidence;
    flowNodes.push(h("article",{className:cx("flow-source-node",source.status==="CONFLICTING"&&"flow-conflict"),key:source.key},
      h("div",{className:"flow-node-heading"},h("span",{className:"flow-icon flow-icon-source"},h(Icon,{name:"file",size:16})),h("div",null,h("small",null,`SOURCE ${String(i+1).padStart(2,"0")}`),h("strong",null,source.name)),source.status&&h(Badge,{tone:source.status},pretty(source.status))),
      h("div",{className:"flow-source-value"},h("span",null,"Observed value"),h("strong",null,esc(source.value??(linked.map(e=>e.raw_value).filter(v=>v!=null).join(" · ")||"Value not attached")))),
      linked.length>0&&h("div",{className:"flow-locations"},linked.slice(0,3).map((e,j)=>h("span",{key:j},locationTags(e.source_location).join(" · ")||e.evidence_id))),
      linked.length>0&&h("button",{className:"flow-citation-button",onClick:()=>onOpen(linked[0])},h(Icon,{name:"external",size:13}),`Open ${linked.length>1?`${linked.length} source citations`:"source citation"}`)
    ));
    flowNodes.push(h("div",{className:"flow-connector",key:`connector-${source.key}`,"aria-hidden":"true"},h("span",null),h(Icon,{name:"arrow",size:16})));
  });
  flowNodes.push(h("article",{className:"flow-process-node",key:"process"},h("div",{className:"flow-node-heading"},h("span",{className:"flow-icon flow-icon-process"},h(Icon,{name:"pulse",size:16})),h("div",null,h("small",null,"REVIEW PROCESS"),h("strong",null,"Normalize & compare"))),h("p",null,method),h("div",{className:"flow-process-meta"},comparison.entity_alignment&&h("span",null,`Entity · ${pretty(comparison.entity_alignment)}`),comparison.period_alignment&&h("span",null,`Period · ${pretty(comparison.period_alignment)}`),comparison.delta!=null&&h("span",null,`Delta · ${comparison.delta}`),comparison.delta_pct!=null&&h("span",null,`Δ ${comparison.delta_pct}%`))));
  flowNodes.push(h("div",{className:"flow-connector",key:"connector-result","aria-hidden":"true"},h("span",null),h(Icon,{name:"arrow",size:16})));
  flowNodes.push(h("article",{className:cx("flow-outcome-node",mismatch&&"flow-outcome-conflict",evidenceGap&&"flow-outcome-caution"),key:"outcome"},h("div",{className:"flow-node-heading"},h("span",{className:cx("flow-icon",needsAttention?"flow-icon-alert":"flow-icon-result")},h(Icon,{name:needsAttention?"warning":"check",size:16})),h("div",null,h("small",null,"REVIEW OUTCOME"),h("strong",null,outcome))),h("p",null,finding.severity_rationale||finding.uncertainty_reason||finding.possible_consequence||"Decision is grounded in the cited source records."),h(Badge,{tone:finding.severity},`${pretty(finding.severity)} severity`)));
  const scrollTrack=(event,direction)=>event.currentTarget.closest(".comparison-flow")?.querySelector(".comparison-flow-track")?.scrollBy({left:direction*330,behavior:"smooth"});
  return h("section",{className:"comparison-flow"},h("div",{className:"comparison-flow-heading"},h("div",null,h("span",{className:"eyebrow"},"EVIDENCE → DECISION"),h("h3",null,"How the sources lead to this finding")),h("div",{className:"comparison-flow-controls"},h("span",{className:"flow-step-count"},`${sources.length} source${sources.length===1?"":"s"} · 1 analysis`),h("div",{className:"flow-scroll-buttons"},h("button",{type:"button",className:"flow-scroll-button",onClick:e=>scrollTrack(e,-1),title:"Scroll sources left","aria-label":"Scroll sources left"},h(Icon,{name:"back",size:15})),h("button",{type:"button",className:"flow-scroll-button",onClick:e=>scrollTrack(e,1),title:"Scroll sources right","aria-label":"Scroll sources right"},h(Icon,{name:"arrow",size:15}))))),h("div",{className:"comparison-flow-track"},flowNodes));
}
function kv(k,v){return h("div",{className:"kv",key:k},h("span",null,k),h("strong",null,v||"—"))}
function SettingsModal({token,setToken,onClose,onSave}){const [value,setValue]=useState(token);return h("div",{className:"modal-backdrop",onMouseDown:e=>e.target===e.currentTarget&&onClose()},h("section",{className:"settings-modal"},h("button",{className:"modal-close",onClick:onClose},"×"),h("div",{className:"brand-mark modal-mark"},"S"),h("span",{className:"eyebrow"},"CONNECTION SETTINGS"),h("h2",null,"API access"),h("p",null,"If this workspace requires a Spectra API token, store it for this browser session. The token is sent only to this Spectra API."),h("label",null,"Spectra API token",h("input",{type:"password",autoComplete:"off",value,onChange:e=>setValue(e.target.value),placeholder:"Paste bearer token"})),h("div",{className:"modal-actions"},h(Button,{onClick:()=>{sessionStorage.removeItem("spectra_api_token");setToken("");onClose()}},"Clear token"),h(Button,{kind:"primary",onClick:()=>onSave(value.trim())},"Save connection"))));}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
