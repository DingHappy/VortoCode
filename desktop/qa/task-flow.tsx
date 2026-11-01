import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowLeft, ArrowRight, ArrowUp, Bot, CalendarCheck, Check, CheckCircle2, ChevronDown,
  Circle, Clock3, FileCheck2, Globe2, Info, Laptop, Link2, ListTodo, Loader2, MessageSquare,
  Monitor, PauseCircle, Plus, RotateCcw, ShieldCheck, Square, X } from "lucide-react";
import "../src/styles/tokens.css";
import "./task-flow.css";

type Route = "home" | "task" | "tasks" | "connections";
type Stage = "host" | "connection" | "login" | "confirm" | "running" | "signed" | "already" | "mismatch" | "unknown" | "cancelled";
type Scenario = "normal" | "already" | "mismatch" | "unknown";
const SITE = "https://www.tangpt.top/";
const SAMPLE_PROMPT = "检查唐帕今天的签到状态，没签的话帮我签到。";
const stageText: Record<Stage, string> = { host: "需要选择执行电脑", connection: "等你连接网站", login: "等你核对登录",
  confirm: "等你确认", running: "正在处理", signed: "已结束", already: "无需重复签到", mismatch: "账号需要核对",
  unknown: "结果待核对", cancelled: "已停止" };

function App() {
  const [route, setRoute] = useState<Route>("home");
  const [stage, setStage] = useState<Stage>("host");
  const [scenario, setScenario] = useState<Scenario>("normal");
  const [started, setStarted] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [taskPrompt, setTaskPrompt] = useState(SAMPLE_PROMPT);
  const [host, setHost] = useState<"resident" | "mac">("resident");
  const [hostMenu, setHostMenu] = useState(false);
  const [url, setUrl] = useState(SITE);
  const [label, setLabel] = useState("我的唐帕");
  const [connected, setConnected] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [checkedAccount, setCheckedAccount] = useState(false);
  const [correctedAccount, setCorrectedAccount] = useState(false);
  const [submissions, setSubmissions] = useState(0);
  const [readback, setReadback] = useState(false);
  const [humanResolved, setHumanResolved] = useState(false);
  const [pageSigned, setPageSigned] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const composer = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const mismatch = scenario === "mismatch" && !correctedAccount;
  const finished = stage === "signed" || stage === "already";
  const active = started && !finished && stage !== "cancelled";
  const hostLocked = started && (submissions > 0 || finished || stage === "cancelled");
  const modalOpen = loginOpen || infoOpen;
  let siteValid = false;
  try { const value = new URL(url); siteValid = value.origin === "https://www.tangpt.top" && !value.username && !value.password; } catch { /* Keep invalid input visible. */ }

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    if (stage !== "running") return;
    const timer = window.setTimeout(() => { if (scenario !== "unknown") setPageSigned(true); setStage(scenario === "unknown" ? "unknown" : "signed"); }, 1600);
    return () => window.clearTimeout(timer);
  }, [stage, scenario]);
  useEffect(() => {
    if (!modalOpen) return;
    returnFocus.current = document.activeElement as HTMLElement;
    modalRef.current?.querySelector<HTMLElement>("button, input")?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setLoginOpen(false); setInfoOpen(false); }
      if (event.key !== "Tab") return;
      const controls = [...(modalRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), a[href]") || [])];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", trap); returnFocus.current?.focus(); };
  }, [modalOpen]);

  function reset(nextScenario = scenario) {
    setScenario(nextScenario); setRoute("home"); setStage("host"); setStarted(false); setPrompt("");
    setTaskPrompt(SAMPLE_PROMPT); setHost("resident"); setHostMenu(false); setConnected(false);
    setLoginOpen(false); setCheckedAccount(false); setCorrectedAccount(false); setSubmissions(0);
    setReadback(false); setHumanResolved(false); setPageSigned(false); setEvidenceOpen(false); setUrl(SITE); setLabel("我的唐帕");
  }
  function start(text = prompt) {
    if (active) { setRoute("task"); return; }
    if (!text.trim()) return;
    setTaskPrompt(text.trim()); setStarted(true); setRoute("task"); setPrompt("");
    setSubmissions(0); setCheckedAccount(false); setReadback(false); setHumanResolved(false); setEvidenceOpen(false);
    setStage(host === "resident" ? "host" : connected ? "login" : "connection");
  }
  function openLogin() { setCheckedAccount(false); setStage("login"); setLoginOpen(true); }
  function completeLogin() {
    if (mismatch) { setStage("mismatch"); setRoute("task"); setLoginOpen(false); return; }
    if (!checkedAccount) return;
    setLoginOpen(false); setRoute("task"); setStage(scenario === "already" || pageSigned ? "already" : "confirm");
  }
  function approve() {
    if (stage !== "confirm" || submissions !== 0 || !connected || !checkedAccount || mismatch) return;
    setSubmissions(1); setStage("running");
  }
  function stop() { if (stage === "unknown") return; setStage(stage === "running" ? "unknown" : "cancelled"); setLoginOpen(false); }
  function showConnections() { setRoute("connections"); setHostMenu(false); }
  const phaseIndex = stage === "host" ? 0 : stage === "connection" ? 1 : ["login", "mismatch"].includes(stage) ? 2 : stage === "confirm" ? 3 : 4;

  function HostButton() {
    return <div className="tf-host-wrap"><button className="tf-host-button" aria-expanded={hostMenu} onClick={() => setHostMenu(!hostMenu)}>
      {host === "resident" ? <Monitor/> : <Laptop/>}{host === "resident" ? "常驻电脑" : "这台 Mac"}<ChevronDown/></button>
      {hostMenu && <div className="tf-host-menu"><strong>这件事在哪里做</strong>
        <button onClick={() => { setHost("resident"); setHostMenu(false); if (started && !submissions) setStage("host"); }} disabled={hostLocked}><Monitor/><span>常驻电脑<small>当前未提供网站操作</small></span>{host === "resident" && <Check/>}</button>
        <button onClick={() => { setHost("mac"); setHostMenu(false); if (started && !submissions) setStage(connected ? "login" : "connection"); }} disabled={hostLocked}><Laptop/><span>这台 Mac<small>使用独立浏览器，先由你连接</small></span>{host === "mac" && <Check/>}</button>
      </div>}
    </div>;
  }

  function ConnectionForm() {
    return <div className="tf-connection-body"><div className="tf-section-heading"><span className="tf-icon-tile"><Globe2/></span><div><h2>连接唐帕</h2><p>在这台 Mac 的专用浏览器中使用。</p></div></div>
      <label className="tf-field">网站<input type="url" value={url} onChange={event => setUrl(event.target.value)} aria-invalid={!siteValid}/></label>
      {!siteValid && <p className="tf-field-error">此预览只演示 https://www.tangpt.top/，请使用这个地址。</p>}
      <label className="tf-field">连接名称<input value={label} maxLength={40} onChange={event => setLabel(event.target.value)}/><small>方便你认出这条连接，不是网站实际登录账号。</small></label>
      <div className="tf-scope-note"><ShieldCheck/><p>连接仅在这台 Mac 的个人助理中使用，可随时断开。登录由你完成，每次提交仍单独确认。</p></div>
      <button className="tf-primary" disabled={!siteValid || !label.trim()} onClick={() => { setConnected(true); setHost("mac"); openLogin(); }}>建立连接并核对登录<ArrowRight/></button>
      <p className="tf-demo-note">这是交互演示，不会打开真实网站或读取登录信息。</p></div>;
  }

  function TaskContent() {
    if (stage === "host") return <><span className="tf-section-kicker">先选一个可用的执行位置</span><h2>常驻电脑暂时不能操作网站</h2><p>这件事可以在这台 Mac 的专用浏览器中继续。你需要先连接唐帕，再核对登录账号。</p>
      <div className="tf-device-choice"><Laptop/><div><strong>在这台 Mac 上继续</strong><span>登录留在本机 · 不复制到常驻电脑</span></div></div>
      <div className="tf-actions"><button className="tf-primary" onClick={() => { setHost("mac"); setStage("connection"); }}>改在本机继续<ArrowRight/></button><button className="tf-text-button" onClick={stop}>稍后处理</button></div></>;
    if (stage === "connection") return <><span className="tf-section-kicker">这件事需要网站连接</span><h2>先接上你的唐帕账号</h2><p>助手会等待你完成登录。账号和站点日期核对后，再决定是否需要签到。</p><button className="tf-primary" onClick={showConnections}><Link2/>连接唐帕</button></>;
    if (stage === "login" || stage === "mismatch") return <><span className="tf-section-kicker">{stage === "mismatch" ? "助手已保持暂停" : "现在交给你"}</span><h2>{stage === "mismatch" ? "当前账号和预期不一致" : "请在专用窗口核对登录"}</h2><p>{stage === "mismatch" ? "预期是“示例账号”，页面显示“另一个示例账号”。切换到正确账号之前，不会提交签到。" : "核对网站账号和站点日期，完成后再把这件事交回助手。"}</p><button className="tf-primary" onClick={openLogin}>重新核对账号<ArrowRight/></button></>;
    if (stage === "confirm") return <><span className="tf-section-kicker">只需决定这一步</span><h2>允许为这个账号签到一次？</h2><p>示例页面显示尚未签到。网站账号和日期已由你核对。</p><dl className="tf-review-fields"><div><dt>网站</dt><dd>www.tangpt.top</dd></div><div><dt>页面账号</dt><dd>示例账号 <small>由你核对</small></dd></div><div><dt>执行位置</dt><dd>这台 Mac</dd></div><div><dt>操作范围</dt><dd>仅本次签到提交</dd></div></dl>
      <p className="tf-consent-note">允许后会向网站提交一次，不会顺带开启每日执行。</p><div className="tf-actions"><button className="tf-primary" onClick={approve}>允许这次签到<ArrowRight/></button><button className="tf-text-button" onClick={stop}>暂不提交</button></div></>;
    if (stage === "running") return <><span className="tf-running-mark"><Loader2/></span><h2>正在核对网站返回</h2><p>本次提交已发出，正在读取页面结果。你可以离开这个页面，稍后回到任务查看。</p><p className="tf-inline-muted">若此时停止，提交可能已经生效，需要先核对结果。</p></>;
    if (stage === "unknown") return <><span className="tf-section-kicker">先核对，再决定下一步</span><h2>提交已发出，结果还无法确认</h2><p>没有收到明确回执，助手已暂停后续提交。不能把超时当作没签成功，也不会自动再点一次。</p>
      {readback ? <div className="tf-readback"><FileCheck2/><div><strong>示例页面显示已签到</strong><p>这是只读核对的结果，原操作仍需由你确认。</p></div></div> : null}
      <div className="tf-actions">{!readback ? <button className="tf-primary" onClick={() => { setReadback(true); setPageSigned(true); }}>只读核对页面<ArrowRight/></button> : <button className="tf-primary" onClick={() => { setHumanResolved(true); setStage("signed"); }}>我已核对原操作<Check/></button>}</div><p className="tf-demo-note">本分支演示“结果不明”的处理；读取页面不会再次提交。</p></>;
    if (stage === "cancelled") return <><span className="tf-result-icon neutral"><PauseCircle/></span><h2>这件事已停止</h2><p>尚未提交签到。连接可以保留，之后交代任务时再使用。</p><button className="tf-secondary" onClick={() => setRoute("home")}>回到新对话</button></>;
    return <><span className="tf-result-icon"><CheckCircle2/></span><span className="tf-section-kicker">{stage === "already" ? "检查后直接结束" : "结果已记录"}</span><h2>{stage === "already" ? "页面已显示签到，无需再提交" : "示例签到流程已结束"}</h2><p>{stage === "already" ? "本次只读取了页面状态，没有点击签到入口。" : humanResolved ? "你已核对原操作及页面结果，没有重复提交。" : "模拟页面返回“今日已签到”。真实任务需要以网站的实际返回为依据。"}</p>
      <div className="tf-result-summary"><FileCheck2/><div><strong>{stage === "already" ? "已有签到奖励记录" : "示例页面：今日已签到"}</strong><span>示例账号 · 这台 Mac · 11 月 1 日（演示）</span></div><button className="tf-text-button" onClick={() => setEvidenceOpen(!evidenceOpen)} aria-expanded={evidenceOpen}>{evidenceOpen ? "收起依据" : "查看依据"}<ChevronDown/></button></div>
      {evidenceOpen && <div className="tf-evidence"><dl><div><dt>页面证据</dt><dd>{stage === "already" ? "[签到已得10000, 补签卡: 88]" : "今日已签到（模拟返回）"}</dd></div><div><dt>提交次数</dt><dd>{submissions} 次（演示）</dd></div><div><dt>账号与日期</dt><dd>由你在接管步骤核对</dd></div></dl><p>这里是合成示例，不是唐帕真实签到记录。</p></div>}
      <div className="tf-next-action"><CalendarCheck/><div><strong>以后每天都做？</strong><p>常驻端的网站操作尚未接通，暂不能开启每日自动签到。</p></div><button className="tf-secondary" onClick={() => setInfoOpen(true)}>了解条件</button></div><button className="tf-text-button" onClick={() => reset()}>再走一遍<ArrowRight/></button></>;
  }

  return <div className="tf-app" data-stage={stage} data-submissions={submissions} data-started={started}>
    <aside className="tf-sidebar"><a className="tf-brand" aria-label="VortoCode 首页" href="#" onClick={event => { event.preventDefault(); setRoute("home"); }}><Bot/><strong>VortoCode</strong></a>
      <button className="tf-new" onClick={() => setRoute("home")}><Plus/>交代新任务</button>
      <nav aria-label="主要导航">{([["home", MessageSquare, "对话"], ["tasks", ListTodo, "任务"], ["connections", Link2, "连接与技能"]] as const).map(([value, Icon, name]) => <button key={value} className={route === value || (route === "task" && value === "tasks") ? "selected" : ""} onClick={() => { setRoute(value); setHostMenu(false); }} aria-label={name} aria-current={route === value || (route === "task" && value === "tasks") ? "page" : undefined}><Icon/><span>{name}</span>{value === "tasks" && active && <span className="tf-nav-count">1</span>}</button>)}</nav>
      <div className="tf-sidebar-recent"><span>最近的事</span>{started ? <button onClick={() => setRoute("task")}><CalendarCheck/><span>唐帕签到</span>{finished && <Check/>}</button> : <p>开始后会留在这里</p>}</div>
      <div className="tf-sidebar-bottom"><div className="tf-assistant"><Bot/><div><strong>个人助理</strong><span>任务、进度和结果在一起</span></div></div><button className="tf-text-button" aria-label="预览说明" onClick={() => setInfoOpen(true)}><Info/><span>预览说明</span></button></div>
    </aside>
    <section className="tf-workspace"><div className="tf-preview-bar"><span>交互预览 <span className="tf-preview-sub">· 全部为示例，不会访问网站</span></span><div><label>示例场景<select aria-label="示例场景" title="切换将重置演示" value={scenario} onChange={event => reset(event.target.value as Scenario)}><option value="normal">未签到</option><option value="already">已经签到</option><option value="mismatch">账号不符</option><option value="unknown">结果不明</option></select></label><button className="tf-icon-button" aria-label="切换深浅主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}><Monitor/></button><button className="tf-icon-button" aria-label="重置演示" onClick={() => reset()}><RotateCcw/></button></div></div>
      <header className="tf-header"><div>{route === "task" || route === "connections" ? <button className="tf-icon-button" aria-label="返回对话" onClick={() => setRoute("home")}><ArrowLeft/></button> : null}<span>{route === "home" ? "新对话" : route === "tasks" ? "任务" : route === "connections" ? "网站连接" : "唐帕签到"}</span></div>{HostButton()}</header>
      <main>
        {route === "home" && <div className="tf-home"><div className="tf-greeting"><span className="tf-eyebrow">个人助理</span><h1>今天想交代点什么？</h1><p>把一件事说清楚。进度、需要你决定的地方和结果，都在同一处。</p></div>
          <form className="tf-composer" onSubmit={event => { event.preventDefault(); start(); }}><textarea ref={composer} aria-label="交代任务" placeholder="例如：检查唐帕今天的签到状态，没签的话帮我签到" value={prompt} onChange={event => setPrompt(event.target.value)}/><div><span><ShieldCheck/>需要登录或提交时，会先找到你</span><button className="tf-send" aria-label={active ? "继续当前任务" : "交给助理"} disabled={!active && !prompt.trim()}><ArrowUp/></button></div></form>
          <button className="tf-suggestion" onClick={() => { setPrompt(SAMPLE_PROMPT); composer.current?.focus(); }}><CalendarCheck/><span><strong>从唐帕签到试试看</strong><small>先检查状态，需要时再连接账号</small></span><ArrowRight/></button>
          {started && <button className="tf-resume" onClick={() => setRoute("task")}><Clock3/><span>继续刚才的唐帕任务</span><small>{stageText[stage]}</small><ArrowRight/></button>}
        </div>}
        {route === "task" && <div className="tf-task"><div className="tf-task-heading"><div><span className="tf-eyebrow">网页任务</span><h1>唐帕签到</h1><p>{taskPrompt}</p></div><span className={`tf-status ${finished ? "done" : ["unknown", "cancelled"].includes(stage) ? "unknown" : stage === "running" ? "working" : "waiting"}`}>{stage === "running" ? <Loader2/> : finished ? <Check/> : <Circle/>}{stageText[stage]}</span></div>
          <div className="tf-task-grid"><section className="tf-task-main" aria-live="polite">{TaskContent()}</section><aside className="tf-task-context"><span className="tf-section-kicker">这件事的上下文</span><dl><div><dt>执行电脑</dt><dd>{host === "resident" ? "常驻电脑" : "这台 Mac"}</dd></div><div><dt>网站</dt><dd>www.tangpt.top</dd></div><div><dt>页面账号</dt><dd>{checkedAccount && !mismatch ? "示例账号 · 你已核对" : "等待你核对"}</dd></div></dl>{stage !== "cancelled" && <ol>{["选择执行位置", "连接网站", "核对登录", "确认这次操作", "读取并核对结果"].map((text, index) => {
            const skipped = stage === "already" && index === 3;
            const complete = !skipped && (finished || index < phaseIndex);
            return <li key={text} className={skipped ? "skipped" : complete ? "complete" : index === phaseIndex ? "current" : ""}>{complete ? <Check/> : <Circle/>}<span>{skipped ? "无需提交" : text}</span></li>;
          })}</ol>}{active && stage !== "unknown" && <button className="tf-text-button" onClick={stop}><Square/>停止这件事</button>}</aside></div>
        </div>}
        {route === "connections" && <div className="tf-connections"><span className="tf-eyebrow">连接跟随执行电脑</span><h1>让这件事用上正确的账号</h1><p className="tf-page-intro">只管理这台 Mac 的连接。常驻电脑的连接不会被一起修改。</p><div className="tf-host-summary"><Laptop/><span>这台 Mac · 专用浏览器</span><span className="tf-status">{connected ? "已建立 · 登录需核对" : "尚未连接"}</span></div>{!started ? <div className="tf-empty"><Globe2/><h2>从一件需要网站的事开始</h2><p>连接会随任务一起建立，无需先研究一整页开关。</p><button className="tf-primary" onClick={() => { setHost("mac"); setTaskPrompt(SAMPLE_PROMPT); setStarted(true); setStage("connection"); }}>开始唐帕连接示例<ArrowRight/></button></div> : connected ? <div className="tf-connection-body"><div className="tf-section-heading"><span className="tf-icon-tile"><Globe2/></span><div><h2>{label}</h2><p>www.tangpt.top · 仅这台 Mac</p></div></div><p>连接名称不代表网站实际账号。继续任务前，仍会核对页面和你的确认。</p><div className="tf-actions"><button className="tf-primary" onClick={() => setRoute("task")}>回到唐帕任务<ArrowRight/></button><button className="tf-text-button" disabled={submissions > 0 && !finished} onClick={() => { setConnected(false); if (!finished && stage !== "cancelled") { setCheckedAccount(false); setStage("connection"); } }}>断开示例连接</button></div></div> : finished || stage === "cancelled" ? <div className="tf-empty"><Link2/><h2>从下一件事建立连接</h2><p>已结束任务的记录会保留。交代新的任务时，再连接这个网站。</p><button className="tf-primary" onClick={() => setRoute("home")}>交代新的任务<ArrowRight/></button></div> : ConnectionForm()}</div>}
        {route === "tasks" && <div className="tf-task-list"><span className="tf-eyebrow">有始有终</span><h1>交给助理的事</h1><p className="tf-page-intro">一件事一个入口，回来看进展、处理确认或找结果。</p>{started ? <button className="tf-task-row" onClick={() => setRoute("task")}><span className="tf-icon-tile"><CalendarCheck/></span><span><strong>唐帕签到</strong><small>{host === "resident" ? "常驻电脑" : "这台 Mac"} · {stageText[stage]}</small></span><span className={`tf-status ${finished ? "done" : ""}`}>{finished ? "已结束" : "查看进展"}</span><ArrowRight/></button> : <div className="tf-empty"><ListTodo/><h2>还没有交代的事</h2><p>从一件小事开始，结果会留在这里。</p><button className="tf-primary" onClick={() => setRoute("home")}>交代第一件事<ArrowRight/></button></div>}</div>}
      </main>
    </section>
    {modalOpen && <div className="tf-modal-backdrop"><div className="tf-modal" role="dialog" aria-modal="true" aria-labelledby="tf-dialog-title" ref={modalRef}><button className="tf-modal-close tf-icon-button" aria-label="关闭对话框" onClick={() => { setLoginOpen(false); setInfoOpen(false); }}><X/></button>
      {loginOpen ? <><span className="tf-section-kicker">助手正在等你</span><h2 id="tf-dialog-title">核对唐帕登录</h2><p>真实使用时，这一步会交给专用浏览器。这里只演示交接，不收集密码、Cookie 或验证码。</p><div className="tf-login-window"><div><Globe2/><span>www.tangpt.top</span><span>示例窗口</span></div><div className="tf-login-content"><Bot/><span>页面显示的账号</span><strong>{mismatch ? "另一个示例账号" : "示例账号"}</strong><span>示例站点日期：2026 年 11 月 1 日</span></div></div>
        {mismatch ? <><p className="tf-account-warning">与预期的“示例账号”不同，助手不会继续提交。</p><div className="tf-actions"><button className="tf-primary" onClick={completeLogin}>返回任务，暂不继续</button><button className="tf-text-button" onClick={() => { setCorrectedAccount(true); setCheckedAccount(false); }}>演示切换到正确账号</button></div></> : <><label className="tf-check"><input type="checkbox" checked={checkedAccount} onChange={event => setCheckedAccount(event.target.checked)}/><span>我已核对，这是本次要用的账号和站点日期</span></label><button className="tf-primary tf-full" disabled={!checkedAccount} onClick={completeLogin}>已核对，继续任务<ArrowRight/></button></>}
      </> : <><span className="tf-section-kicker">关于这份预览</span><h2 id="tf-dialog-title">先把一件事顺畅地办完</h2><p>这是一份独立的交互原型。所有账号、页面、日期和执行结果均为合成示例，不连接真实服务，不修改现有助手。</p><ul><li>只显示当前任务用到的连接和确认。</li><li>执行电脑和账号始终跟着这件事。</li><li>提交结果不明时先核对，不重复提交。</li></ul><p>每日自动执行仍需要常驻端浏览器准入、真实账号和站点规则验收。</p><button className="tf-primary" onClick={() => setInfoOpen(false)}>回到预览<ArrowRight/></button></>}
    </div></div>}
  </div>;
}

createRoot(document.getElementById("root")!).render(<App/>);
