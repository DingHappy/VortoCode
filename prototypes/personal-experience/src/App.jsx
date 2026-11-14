import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  CircleHelp,
  Clock3,
  FileText,
  Folder,
  Laptop,
  ListChecks,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  MoreHorizontal,
  Plus,
  Search,
  Server,
  Settings,
  ShieldCheck,
  SquarePen,
  Sun,
  Users,
  X,
} from "lucide-react";
import general from "../../../docs/design/personal-experience/assets/general.png";
import research from "../../../docs/design/personal-experience/assets/research.png";
import development from "../../../docs/design/personal-experience/assets/development.png";
import calendar from "../../../docs/design/personal-experience/assets/calendar.png";
import family from "../../../docs/design/personal-experience/assets/role-family-reference.png";

const ART = { general, research, development, calendar };
const ROLES = [
  {
    id: "general",
    name: "小 V",
    title: "总助手",
    intro: "与你对话，整理事情，在需要时请你决定。",
    scope: "当前个人空间",
    actions: "沿用主会话已有的权限与确认。",
    limit: "命令、外发和不可撤回操作，仍由你确认。",
  },
  {
    id: "research",
    name: "研究助手",
    title: "研究助手",
    intro: "查公开资料，交回有来源、可核对的摘要。",
    scope: "公开资料",
    actions: "搜索、读取公开网页，整理研究结果。",
    limit: "不能读私人邮件和日程，也不能替你外发。",
  },
  {
    id: "development",
    name: "开发助手",
    title: "开发助手",
    intro: "在已登记项目的隔离副本里实现和验证。",
    scope: "指定项目",
    actions: "只在批准的一次性副本内修改、运行允许的测试。",
    limit: "不能写主工作区；推送、开 PR 和发布仍须主人确认。",
  },
  {
    id: "calendar",
    name: "日程助手",
    title: "日程助手",
    intro: "读已有安排，整理今日清单和会前资料。",
    scope: "你的日程 · 只读",
    actions: "读取已有日程，整理安排与提醒建议。",
    limit: "本阶段不新建、修改或取消日程，也不联网。",
  },
];
const SCREENS = [
  ["download", "00 · 获取与安装"],
  ["welcome", "01 · 安装完成"],
  ["where", "02 · 运行位置"],
  ["model", "03 · 准备模型"],
  ["install", "04 · 本机安装"],
  ["remote", "05 · 连接已有助手"],
  ["ready", "06 · 开始使用"],
  ["chat", "07 · 个人空间与对话"],
  ["staff", "08 · 专员"],
  ["routines", "09 · 定时工作"],
  ["library", "10 · 资料库"],
  ["settings", "11 · 设置"],
  ["approval", "12 · 主人确认"],
  ["recovery", "13 · 断连与恢复"],
  ["brand", "14 · 角色规范"],
];
const CATEGORIES = [
  ["personal", "个性化"],
  ["model", "模型与执行"],
  ["computer", "电脑与连接"],
  ["staff", "团队与权限"],
  ["permissions", "权限与自动化"],
  ["tools", "工具与集成"],
  ["about", "高级与关于"],
];
const SETUP = [
  "download",
  "welcome",
  "where",
  "model",
  "install",
  "remote",
  "ready",
];
function Avatar({ kind = "general", size = 64, label = "" }) {
  return (
    <img
      className="role-avatar"
      src={ART[kind]}
      width={size}
      height={size}
      alt={label}
    />
  );
}
function Button({ primary, children, ...props }) {
  return (
    <button className={`button ${primary ? "primary" : ""}`} {...props}>
      {children}
    </button>
  );
}
function Detail({ title, children }) {
  return (
    <details className="detail">
      <summary>
        <CircleHelp />
        {title}
        <ChevronDown />
      </summary>
      <div>{children}</div>
    </details>
  );
}
function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Row({ title, description, children }) {
  return (
    <div className="setting-row">
      <div>
        <strong>{title}</strong>
        {description && <p>{description}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}
function Status({ children, warning }) {
  return (
    <span className={`status ${warning ? "warning" : ""}`}>
      {warning ? <Clock3 /> : <Check />}
      {children}
    </span>
  );
}
function Head({ title, description, children }) {
  return (
    <header className="page-header">
      <div>
        <h1 tabIndex={-1}>{title}</h1>
        <p>{description}</p>
      </div>
      {children}
    </header>
  );
}

function Modal({ labelledby, onClose, className = "", children }) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      requestAnimationFrame(() => {
        const target =
          previousFocus?.isConnected && previousFocus !== document.body
            ? previousFocus
            : document.querySelector("h1[tabindex='-1'], main button");
        target?.focus({ preventScroll: true });
      });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`overlay ${className}`}
      aria-labelledby={labelledby}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

// Synthetic design records only; switching runtime must not show another space's approvals or staff.
function usePreviewSpaceState(space, initial) {
  const [records, setRecords] = useState({});
  return [
    records[space] ?? initial,
    (action) =>
      setRecords((previous) => ({
        ...previous,
        [space]:
          typeof action === "function"
            ? action(previous[space] ?? initial)
            : action,
      })),
  ];
}

export function App() {
  const [route, setRoute] = useState(
      new URLSearchParams(location.search).get("screen") || "where",
    ),
    [theme, setTheme] = useState(
      new URLSearchParams(location.search).get("theme") || "light",
    );
  const [space, setSpace] = useState("这台 Mac"),
    [spaceMenu, setSpaceMenu] = useState(false),
    [sidebar, setSidebar] = useState(true),
    [more, setMore] = useState(false),
    [search, setSearch] = useState("");
  const [project, setProject] = useState("个人空间"),
    [chatId, setChatId] = useState("main"),
    [chats, setChats] = usePreviewSpaceState(space, []),
    [drafts, setDrafts] = useState({}),
    [messages, setMessages] = useState({}),
    [process, setProcess] = useState(false);
  const [model, setModel] = useState(""),
    [endpoint, setEndpoint] = useState(""),
    [apiKey, setApiKey] = useState(""),
    [saved, setSaved] = useState(false),
    [consent, setConsent] = useState(false),
    [installed, setInstalled] = useState(false),
    [running, setRunning] = useState(false),
    [error, setError] = useState("");
  const [remoteName, setRemoteName] = useState("我的常驻助手"),
    [address, setAddress] = useState(""),
    [token, setToken] = useState(""),
    [checked, setChecked] = useState(false);
  const [staff, setStaff] = usePreviewSpaceState(space, ["general"]),
    [role, setRole] = useState(null),
    [request, setRequest] = useState(null),
    [audit, setAudit] = usePreviewSpaceState(space, []),
    [routineForm, setRoutineForm] = useState(false);
  const [routineName, setRoutineName] = useState("每周资料跟踪"),
    [routineRole, setRoutineRole] = useState("research"),
    [zone, setZone] = useState("Asia/Shanghai");
  const [routines, setRoutines] = usePreviewSpaceState(space, [
    {
      id: "brief",
      name: "每日早报",
      staff: "总助手",
      when: "每天 09:00",
      enabled: true,
    },
  ]);
  const [filter, setFilter] = useState("全部"),
    [documentOpen, setDocumentOpen] = useState(false),
    [version, setVersion] = useState("2"),
    [category, setCategory] = useState("personal"),
    [settingsQuery, setSettingsQuery] = useState(""),
    [name, setName] = usePreviewSpaceState(space, "小 V"),
    [savedName, setSavedName] = usePreviewSpaceState(space, "小 V"),
    [persona, setPersona] = usePreviewSpaceState(
      space,
      "简洁、清楚，有依据地汇报。不确定时直说。",
    ),
    [savedPersona, setSavedPersona] = usePreviewSpaceState(
      space,
      "简洁、清楚，有依据地汇报。不确定时直说。",
    ),
    [leaveTo, setLeaveTo] = useState(null),
    [notice, setNotice] = useState("");
  const sequence = useRef(0),
    surface = useRef(null),
    trigger = useRef(null);
  const draftKey = `${space}:${project}:${chatId}`,
    draft = drafts[draftKey] || "",
    thread = messages[draftKey] || [];
  const setDraft = (text) =>
    setDrafts((values) => ({ ...values, [draftKey]: text }));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    surface.current?.querySelector("h1")?.focus({ preventScroll: true });
  }, [route]);
  useEffect(() => {
    const searchShortcut = (event) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "k" &&
        !event.altKey &&
        !SETUP.includes(route)
      ) {
        event.preventDefault();
        setSidebar(true);
        requestAnimationFrame(() =>
          document
            .querySelector(
              route === "settings"
                ? 'input[aria-label="搜索设置"]'
                : 'input[aria-label="搜索对话"]',
            )
            ?.focus(),
        );
      }
    };
    document.addEventListener("keydown", searchShortcut);
    return () => document.removeEventListener("keydown", searchShortcut);
  }, [route]);
  function go(next) {
    if (
      route === "settings" &&
      next !== "settings" &&
      (name !== savedName || persona !== savedPersona)
    ) {
      setLeaveTo(next);
      return;
    }
    setRoute(next);
    setSpaceMenu(false);
    setDocumentOpen(false);
    setError("");
    setNotice("");
    setRole(null);
    setRoutineForm(false);
  }
  function ask(kind, payload) {
    trigger.current = document.activeElement;
    setRequest({ kind, payload, space, project });
  }
  const currentRequest =
    request && request.space === space && request.project === project;
  function decide(allowed) {
    if (!currentRequest) return;
    setAudit((items) => [...items, { kind: request.kind, allowed }]);
    if (allowed && request.kind === "staff")
      setStaff((items) => [...new Set([...items, request.payload.id])]);
    if (allowed && request.kind === "routine")
      setRoutines((items) => [
        ...items,
        {
          ...request.payload,
          id: `routine-${++sequence.current}`,
          enabled: true,
        },
      ]);
    if (allowed && request.kind === "resume")
      setRoutines((items) =>
        items.map((item) =>
          item.id === request.payload.id ? { ...item, enabled: true } : item,
        ),
      );
    setNotice(
      allowed
        ? "已批准演示请求，未执行真实动作。"
        : "已拒绝此项请求，原有设置保留。",
    );
    setRequest(null);
    setRole(null);
    setRoutineForm(false);
    requestAnimationFrame(() => {
      const target = trigger.current?.isConnected
        ? trigger.current
        : document.querySelector('.sidebar button[aria-current="page"]');
      target?.focus();
    });
  }
  function send() {
    if (!draft.trim()) return;
    setMessages((values) => ({
      ...values,
      [draftKey]: [
        ...thread,
        { role: "user", text: draft.trim() },
        {
          role: "assistant",
          text: "我会先整理已有资料，把结论、来源和待你决定的地方放在一起。\n\n这是一条设计演示回复，没有读取资料、调用模型或执行任务。",
        },
      ],
    }));
    setDraft("");
  }
  const source = (
    <div className="source-note">
      <Laptop />
      {space}
      <span>个人空间</span>
    </div>
  );
  const settingsLink = (id) => {
    setCategory(id);
    go("settings");
  };

  function setup() {
    return (
      <div
        className={`setup ${["where", "welcome"].includes(route) ? "wide" : ""}`}
      >
        <header className="setup-brand">
          <strong>VortoOne</strong>
          <Button onClick={() => go("chat")}>
            先看看界面
            <ArrowRight />
          </Button>
        </header>
        {route === "download" ? (
          <section className="download-guide">
            <Avatar size={180} />
            <p className="eyebrow">先安装，再开始你的个人空间</p>
            <h1>从这台 Mac 开始。</h1>
            <p className="lead">
              应用会引导你准备运行环境，不需要先配置终端、Docker 或数据库。
            </p>
            <div className="download-steps">
              <Row
                title="1. 获取安装包"
                description="使用当前发行版支持、与你的 Mac 匹配的签名版本。"
              >
                <ArrowDownToLine />
              </Row>
              <Row
                title="2. 放入应用程序"
                description="打开安装包，将 VortoOne 放入应用程序目录。"
              >
                <Folder />
              </Row>
              <Row
                title="3. 打开 VortoOne"
                description="先核对已有配置，再选择运行位置；资料不会自动搬动。"
              >
                <ArrowRight />
              </Row>
            </div>
            <div className="form-actions">
              <Button
                onClick={() =>
                  setNotice(
                    "设计稿不下载安装包。正式产品沿用签名、公证与安装校验。",
                  )
                }
              >
                查看 Mac 安装包
              </Button>
              <Button primary onClick={() => go("welcome")}>
                我已安装，继续设置
                <ArrowRight />
              </Button>
            </div>
            <Detail title="如果打不开或已经装过">
              <p>
                先核对应用签名、版本与安装位置。已有资料应恢复使用，不要求重新创建空间，也不直接覆盖旧配置。
              </p>
            </Detail>
          </section>
        ) : route === "welcome" ? (
          <section className="welcome">
            <Avatar size={240} />
            <p className="eyebrow">你的个人助理</p>
            <h1>
              把事情交代清楚，
              <br />
              把进展留在眼前。
            </h1>
            <p>聊天、查资料、安排工作。需要你决定时，它会回来问你。</p>
            <Button primary onClick={() => go("where")}>
              开始设置
              <ArrowRight />
            </Button>
            <small>先选择运行位置，再准备模型。不会自动开启定时工作。</small>
          </section>
        ) : (
          <>
            {route !== "where" && (
              <ol className="setup-rail" aria-label="设置进度">
                {["运行位置", "准备连接", "安装与核对", "开始使用"].map(
                  (text, index) => (
                    <li
                      key={text}
                      aria-current={
                        (["model", "remote"].includes(route)
                          ? 1
                          : route === "install"
                            ? 2
                            : 3) === index
                          ? "step"
                          : undefined
                      }
                    >
                      <span>{index + 1}</span>
                      {text}
                    </li>
                  ),
                )}
              </ol>
            )}
            {route === "where" ? (
              <>
                <p className="eyebrow">你的资料，你的运行位置</p>
                <h1>让助理在哪里运行？</h1>
                <p className="lead">
                  选择一台电脑，保存你的资料，运行你的个人助理。
                </p>
                <div className="location-cards">
                  <button onClick={() => go("remote")}>
                    <span className="location-icon">
                      <Monitor />
                      <ArrowRight />
                      <Server />
                    </span>
                    <h2>连接已有助手</h2>
                    <p>
                      助理在另一台电脑上运行。关闭这里的窗口，它仍可以继续工作。
                    </p>
                    <span className="card-action">
                      连接另一台电脑
                      <ChevronRight />
                    </span>
                  </button>
                  <button onClick={() => go("model")}>
                    <span className="location-icon">
                      <Laptop />
                    </span>
                    <h2>在这台 Mac 运行</h2>
                    <p>
                      资料保存在本机。退出 Desktop 后仍可运行；Mac
                      需开机并登录，睡眠时暂停。
                    </p>
                    <span className="card-action">
                      准备这台 Mac
                      <ChevronRight />
                    </span>
                  </button>
                </div>
                <Detail title="哪一种适合我？">
                  <p>
                    有常开的电脑，连接它；只在这台 Mac
                    使用，就从本机开始。切换运行位置不会自动移动或合并资料。
                  </p>
                </Detail>
              </>
            ) : route === "model" ? (
              <>
                <Avatar size={96} />
                <h1>先让助理有模型可用。</h1>
                <p className="lead">
                  使用已有的模型服务。安装时会单独请你确认复制配置。
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    setSaved(true);
                    setApiKey("");
                    go("install");
                  }}
                >
                  <Field
                    label="模型服务地址"
                    hint="可连接本机模型，也可使用兼容的在线服务。"
                  >
                    <input
                      value={endpoint}
                      onChange={(e) => setEndpoint(e.target.value)}
                      type="url"
                      required
                      placeholder="https://model.example/v1"
                    />
                  </Field>
                  <Field label="模型名称">
                    <input
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      required
                      placeholder="输入模型名称"
                    />
                  </Field>
                  <Field label="API Key" hint="无需鉴权的本机服务可留空。">
                    <input
                      type="password"
                      autoComplete="off"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder="仅用于这个模型服务"
                    />
                  </Field>
                  <div className="form-actions">
                    <Button type="button" onClick={() => go("where")}>
                      返回
                    </Button>
                    <Button primary type="submit">
                      保存模型配置
                      <ArrowRight />
                    </Button>
                  </div>
                </form>
                <Detail title="保存之后会发生什么？">
                  <p>
                    保存配置不表示推理已经验证。常驻助理独立管理配置，退出
                    Desktop 模型账号不会自动停用它。
                  </p>
                </Detail>
              </>
            ) : route === "install" ? (
              <>
                <Avatar size={96} />
                <h1>
                  {running
                    ? "助理已准备好。"
                    : installed
                      ? "程序已安装。"
                      : "准备这台 Mac"}
                </h1>
                <p className="lead">安装、启动和连接，每一步都由你决定。</p>
                <div className="setup-status">
                  <Row
                    title="模型配置"
                    description={
                      saved
                        ? `${model} · 已保存，未验证推理`
                        : "请先完成模型配置"
                    }
                  >
                    <Status warning={!saved}>
                      {saved ? "已保存" : "待准备"}
                    </Status>
                  </Row>
                  <Row
                    title="本机常驻助理"
                    description="Mac 保持开机、登录且未睡眠时，可以继续工作。"
                  >
                    <Status warning={!running}>
                      {running ? "正在运行" : installed ? "已停止" : "尚未安装"}
                    </Status>
                  </Row>
                </div>
                {!installed && (
                  <>
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={consent}
                        onChange={(e) => setConsent(e.target.checked)}
                      />
                      同意复制已保存的模型配置
                    </label>
                    <p className="hint">
                      初次安装保持定时工作、心跳和即时通讯关闭。
                    </p>
                  </>
                )}
                <div className="form-actions">
                  <Button onClick={() => go("where")}>重新选择位置</Button>
                  {!installed ? (
                    <Button
                      primary
                      disabled={!consent || !saved}
                      onClick={() => {
                        setInstalled(true);
                        setError("");
                      }}
                    >
                      安装本机常驻助理
                    </Button>
                  ) : !running ? (
                    <Button primary onClick={() => setRunning(true)}>
                      启动本机常驻助理
                    </Button>
                  ) : (
                    <Button
                      primary
                      onClick={() => {
                        setSpace("这台 Mac");
                        go("ready");
                      }}
                    >
                      使用这份助手
                      <ArrowRight />
                    </Button>
                  )}
                </div>
                <Detail title="如果安装没有成功">
                  <p>
                    保留已经完成的步骤，显示当前状态。重试不会自动切换运行端。
                  </p>
                  <Button
                    onClick={() => {
                      setInstalled(false);
                      setRunning(false);
                      setError(
                        "程序暂未安装成功。已保存的模型配置保留，你可以重试或重新选择位置。",
                      );
                    }}
                  >
                    查看失败示例
                  </Button>
                </Detail>
              </>
            ) : route === "remote" ? (
              <>
                <span className="setup-symbol">
                  <Server />
                </span>
                <h1>连接已有助手。</h1>
                <p className="lead">
                  使用那台电脑已有的模型和资料。连接前，先核对这份助手。
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    setChecked(true);
                    setToken("");
                  }}
                >
                  <Field label="这份助手叫什么">
                    <input
                      value={remoteName}
                      onChange={(e) => setRemoteName(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="服务地址">
                    <input
                      value={address}
                      onChange={(e) => {
                        setAddress(e.target.value);
                        setChecked(false);
                      }}
                      type="url"
                      required
                      placeholder="https://assistant.example"
                    />
                  </Field>
                  <Field
                    label="主人访问令牌"
                    hint="和模型 API Key 是两回事。真实产品由系统钥匙串保存。"
                  >
                    <input
                      type="password"
                      autoComplete="off"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                    />
                  </Field>
                  <div className="form-actions">
                    <Button type="button" onClick={() => go("where")}>
                      返回
                    </Button>
                    <Button primary type="submit">
                      核对连接
                      <ArrowRight />
                    </Button>
                  </div>
                </form>
                {checked && (
                  <div className="connection-receipt">
                    <strong>{remoteName}</strong>
                    <span>{address}</span>
                    <p>
                      即将使用这份助手的个人空间。本机资料和模型配置不会自动复制过去。
                    </p>
                    <Button
                      primary
                      onClick={() => {
                        setSpace(remoteName);
                        go("ready");
                      }}
                    >
                      使用这份助手
                    </Button>
                  </div>
                )}
                <Detail title="暂时连不上怎么办？">
                  <p>
                    保留原位置与已输入的地址，核对后重试，不自动换一份助手。
                  </p>
                  <Button
                    onClick={() => {
                      setChecked(false);
                      setError(
                        "暂时无法连接。原运行位置保留，请核对连接后重试。",
                      );
                    }}
                  >
                    查看断连示例
                  </Button>
                </Detail>
              </>
            ) : (
              <section className="ready">
                <Avatar size={220} />
                <p className="eyebrow">你的个人空间</p>
                <h1>你好，我是 {savedName}。</h1>
                <p className="lead">
                  直接交代一件事。需要额外权限或对外发送时，我会先问你。
                </p>
                <div className="ready-receipt">
                  <Laptop />
                  <div>
                    <strong>{space}</strong>
                    <span>对话和资料保存在这里</span>
                  </div>
                </div>
                <Button primary onClick={() => go("chat")}>
                  开始聊天
                  <ArrowRight />
                </Button>
                <p className="hint">
                  专员和定时工作可以以后再设置，不会自动创建。
                </p>
              </section>
            )}
            {error && (
              <div className="alert" role="alert">
                {error}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  function navigation() {
    return (
      <aside className="sidebar" aria-label="个人空间导航">
        <header>
          <strong>VortoOne</strong>
          <button
            className="icon-button"
            aria-label="收起侧栏"
            onClick={() => setSidebar(false)}
          >
            <Menu />
          </button>
        </header>
        <div className="space-switch">
          <button
            onClick={() => setSpaceMenu(!spaceMenu)}
            aria-expanded={spaceMenu}
          >
            <Laptop />
            <span>
              <strong>{space}</strong>
              <small>
                个人空间 · {route === "recovery" ? "连接中断" : "已连接"}
              </small>
            </span>
            <ChevronsUpDown />
          </button>
          {spaceMenu && (
            <div className="space-menu">
              {["这台 Mac", "我的常驻助手"].map((value) => (
                <button
                  key={value}
                  onClick={() => {
                    setSpace(value);
                    setChatId("main");
                    setProject("个人空间");
                    setDocumentOpen(false);
                    setRoute("chat");
                    setSpaceMenu(false);
                    setNotice("已切换演示空间，草稿留在原空间。");
                  }}
                >
                  {value}
                  {space === value && <Check />}
                </button>
              ))}
              <button
                onClick={() => {
                  setSpaceMenu(false);
                  go("where");
                }}
              >
                管理运行位置
                <ArrowRight />
              </button>
            </div>
          )}
        </div>
        <button
          className="new-chat"
          onClick={() => {
            const id = `side-${++sequence.current}`;
            setChats((values) => [...values, id]);
            setChatId(id);
            go("chat");
          }}
        >
          <SquarePen />
          新对话
        </button>
        <label className="sidebar-search">
          <Search />
          <input
            aria-label="搜索对话"
            placeholder="搜索对话"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <kbd>⌘ K</kbd>
        </label>
        <nav>
          {[
            ["chat", MessageSquare, "主要聊天"],
            ["staff", Users, "专员"],
            ["routines", Clock3, "定时工作"],
            ["library", BookOpen, "资料库"],
          ].map(([id, Icon, label]) => (
            <button
              key={id}
              aria-current={
                route === id || (route === "approval" && id === "chat")
                  ? "page"
                  : undefined
              }
              onClick={() => {
                if (id === "chat") setChatId("main");
                go(id);
              }}
            >
              <Icon />
              {label}
            </button>
          ))}
          <button aria-expanded={more} onClick={() => setMore(!more)}>
            <MoreHorizontal />
            更多
          </button>
          {more && (
            <div className="secondary-nav">
              <button onClick={() => settingsLink("personal")}>
                身份与记忆
              </button>
              <button onClick={() => settingsLink("tools")}>插件与连接</button>
              <button onClick={() => go("brand")}>角色规范</button>
            </div>
          )}
        </nav>
        <div className="recent">
          <p>最近对话</p>
          {chats
            .filter((id) => !search || id.includes(search))
            .map((id, index) => (
              <button
                key={id}
                onClick={() => {
                  setChatId(id);
                  go("chat");
                }}
              >
                <MessageSquare />
                旁聊 {index + 1}
              </button>
            ))}
          {!chats.length && <span>岔开的话题可以另开一段对话。</span>}
        </div>
        <footer>
          <button onClick={() => go("settings")}>
            <Settings />
            设置
          </button>
          <span>
            {route === "recovery" ? <Clock3 /> : <Check />}
            {route === "recovery" ? "当前连接未确认：" : "已连接到 "}
            {space}
          </span>
        </footer>
      </aside>
    );
  }

  function chat() {
    return (
      <section className={`chat ${thread.length ? "has-messages" : ""}`}>
        <header className="conversation-head">
          <div>
            <strong>{chatId === "main" ? "主要聊天" : "新的旁聊"}</strong>
            <span>{project}</span>
          </div>
          <button
            className="icon-button"
            aria-label="查看工作动态"
            onClick={() => setProcess(!process)}
          >
            <ListChecks />
          </button>
        </header>
        <div className="chat-body">
          {route === "approval" && (
            <button
              className="waiting-band"
              onClick={() =>
                ask("draft", {
                  title: "保存项目周报草稿",
                  description: "只保存到邮箱草稿箱，不发送给任何收件人。",
                })
              }
            >
              <Clock3 />
              <span>
                <strong>1 件事等你决定</strong>
                <small>保存项目周报草稿</small>
              </span>
              <ChevronRight />
            </button>
          )}
          {!thread.length ? (
            <div className="chat-welcome">
              <Avatar size={190} />
              <h1>今天，想先做什么？</h1>
              <p>交代一件事。进展、结果和需要你决定的地方，都会留在这里。</p>
              <div className="suggestions">
                {["整理今天的安排", "查一份公开资料", "看看项目进展"].map(
                  (text) => (
                    <button key={text} onClick={() => setDraft(text)}>
                      {text}
                      <ArrowUp />
                    </button>
                  ),
                )}
              </div>
            </div>
          ) : (
            <div className="messages">
              {thread.map((item, index) => (
                <article key={index} className={item.role}>
                  {item.role === "assistant" && <Avatar size={48} />}
                  <div>
                    {item.text.split("\n").map((line, i) => (
                      <p key={i}>{line || "\u00a0"}</p>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
        <div className="composer-area">
          <label className="project-select">
            <Folder />
            <select
              aria-label="当前项目"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option>个人空间</option>
              <option>VortoOne · 已登记项目</option>
            </select>
          </label>
          <div className="composer">
            <textarea
              aria-label="交代一件事"
              placeholder="交代一件事…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <footer>
              <button
                className="icon-button"
                aria-label="添加资料"
                onClick={() =>
                  setNotice("设计稿不读取文件；产品沿用原附件与引用入口。")
                }
              >
                <Plus />
              </button>
              <span>
                默认模型
                <ChevronDown />
              </span>
              <button
                className="send"
                aria-label="发送"
                disabled={!draft.trim()}
                onClick={send}
              >
                <ArrowUp />
              </button>
            </footer>
          </div>
          <p className="composer-note">重要操作仍会先请你确认。</p>
        </div>
        {process && (
          <aside className="work-detail">
            <header>
              <h2>工作动态</h2>
              <button
                className="icon-button"
                aria-label="收起工作动态"
                onClick={() => setProcess(false)}
              >
                <X />
              </button>
            </header>
            <Avatar size={80} />
            <strong>{savedName}</strong>
            <p>还没有正在运行的工作。</p>
            <Detail title="查看已完成的记录">
              <p>演示环境不对应真实执行。产品保留来源、结果和失败记录。</p>
            </Detail>
          </aside>
        )}
      </section>
    );
  }

  function specialists() {
    return (
      <section className="domain-page">
        <Head
          title="专员"
          description="把合适的事交给合适的助手。你仍决定它们能做什么。"
        >
          <Button onClick={() => setRole(ROLES[1])}>
            <Plus />
            提议专员
          </Button>
        </Head>
        {source}
        <div className="staff-grid">
          {ROLES.map((item) => (
            <article className="staff-card" key={item.id}>
              <Avatar kind={item.id} size={112} />
              <div>
                <h2>{item.id === "general" ? savedName : item.name}</h2>
                <span>
                  {item.id === "general"
                    ? item.title
                    : staff.includes(item.id)
                      ? "已登记"
                      : "建议专员"}
                </span>
              </div>
              <p>{item.intro}</p>
              <div className="staff-scope">
                <ShieldCheck />
                {item.scope}
              </div>
              <Button onClick={() => setRole(item)}>
                {staff.includes(item.id) ? "查看岗位" : "了解并提议"}
                <ChevronRight />
              </Button>
            </article>
          ))}
        </div>
        <Detail title="为什么不是点一下就自动创建？">
          <p>
            角色形象帮助辨认身份。权限只来自你批准的岗位登记；名字、颜色或一次派活都不能附带新权限。
          </p>
        </Detail>
      </section>
    );
  }
  function schedule() {
    return (
      <section className="domain-page">
        <Head
          title="定时工作"
          description="约好什么时候做，也能回查实际做得怎样。"
        >
          <Button onClick={() => setRoutineForm(true)}>
            <Plus />
            新建例行工作
          </Button>
        </Head>
        {source}
        <div className="list-heading">
          <span>{routines.length} 项排期</span>
          <span>{zone} · 演示时区</span>
        </div>
        <div className="routine-list">
          {routines.map((item) => (
            <div className="routine-row" key={item.id}>
              <Clock3 />
              <div>
                <strong>{item.name}</strong>
                <p>
                  {item.when} · {item.staff}
                </p>
                <small>
                  {item.enabled ? "等待下次执行" : "已暂停，后续不再启动"}
                </small>
              </div>
              <button
                className="switch"
                role="switch"
                aria-label={`${item.name}是否启用`}
                aria-checked={item.enabled}
                onClick={() =>
                  item.enabled
                    ? setRoutines((values) =>
                        values.map((row) =>
                          row.id === item.id ? { ...row, enabled: false } : row,
                        ),
                      )
                    : ask("resume", item)
                }
              >
                <span />
              </button>
            </div>
          ))}
        </div>
        <Detail title="旧作业、失败和重启如何处理？">
          <p>
            旧作业不会自动迁入专员，先预览再由主人批准。重启后未核对的工作会停下等你决定，不自动恢复。
          </p>
        </Detail>
      </section>
    );
  }
  function library() {
    return (
      <section className="domain-page">
        <Head
          title="资料库"
          description="助理交回的文档与网页，保留原始内容和历史版本。"
        />
        {source}
        <div className="tabs" role="tablist" aria-label="资料类型">
          {["全部", "文档", "网页"].map((text) => (
            <button
              key={text}
              role="tab"
              aria-selected={filter === text}
              onClick={() => setFilter(text)}
            >
              {text}
            </button>
          ))}
        </div>
        {filter === "网页" ? (
          <div className="quiet-empty">
            <BookOpen />
            <h2>还没有网页资料</h2>
            <p>让助理整理成网页，交付后会出现在这里。</p>
          </div>
        ) : (
          <button
            className="document-row"
            onClick={() => setDocumentOpen(true)}
          >
            <FileText />
            <span>
              <strong>本周资料摘要</strong>
              <small>文档 · v2 · 2.0 KB</small>
            </span>
            <span>今天</span>
            <ChevronRight />
          </button>
        )}
        {documentOpen && (
          <aside className="document-preview">
            <header>
              <h2>本周资料摘要</h2>
              <select
                aria-label="文档版本"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              >
                <option value="2">v2 · 最新</option>
                <option value="1">v1</option>
              </select>
              <button
                className="icon-button"
                aria-label="关闭预览"
                onClick={() => setDocumentOpen(false)}
              >
                <X />
              </button>
            </header>
            <div className="document-paper">
              <p className="eyebrow">资料摘要 · 第 {version} 版</p>
              <h1>把进展与问题放在一起。</h1>
              <p>这是一份合成文档，没有读取真实文件。</p>
              <h2>已经整理</h2>
              <p>对话、后台工作与需要你决定的事项，各自有清楚的入口。</p>
              <h2>接下来核对</h2>
              <p>来源是否完整；时间是否有效；结果是否符合要求。</p>
            </div>
          </aside>
        )}
      </section>
    );
  }

  function preferences() {
    return (
      <section className="preferences">
        <nav aria-label="设置分类">
          <h1>设置</h1>
          <label className="sidebar-search">
            <Search />
            <input
              aria-label="搜索设置"
              placeholder="搜索设置"
              value={settingsQuery}
              onChange={(e) => setSettingsQuery(e.target.value)}
            />
          </label>
          {CATEGORIES.filter(
            ([, title]) => !settingsQuery || title.includes(settingsQuery),
          ).map(([id, title]) => (
            <button
              key={id}
              aria-current={category === id ? "page" : undefined}
              onClick={() => {
                setCategory(id);
                setSettingsQuery("");
              }}
            >
              {title}
            </button>
          ))}
          <p>
            保存位置随每项说明，
            <br />
            不会自动同步到其它电脑。
          </p>
          <Button onClick={() => go("chat")}>
            <ArrowLeft />
            返回个人空间
          </Button>
        </nav>
        <div className="preferences-content">
          <Head
            title={CATEGORIES.find(([id]) => id === category)?.[1]}
            description="只显示当前需要调整的选项，详细说明随时可查。"
          />
          <div className="settings-source">
            当前空间<strong>{space}</strong>
            <Detail title="保存位置与连接详情">
              <p>
                界面设置保存在这台
                Mac。运行端配置属于当前个人空间。切换或断连后，旧目标写入停用。
              </p>
            </Detail>
          </div>
          {category === "personal" ? (
            <>
              <section className="settings-group">
                <h2>助理资料</h2>
                <Row
                  title="名字与形象"
                  description="在对话和工作记录中显示，自定义头像仍优先。"
                >
                  <Avatar size={64} />
                </Row>
                <Field label="助理名字">
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <Field label="希望怎样与你相处">
                  <textarea
                    value={persona}
                    onChange={(event) => setPersona(event.target.value)}
                  />
                </Field>
                <Button
                  primary
                  disabled={
                    !name.trim() ||
                    (name === savedName && persona === savedPersona)
                  }
                  onClick={() => {
                    setSavedName(name.trim());
                    setName(name.trim());
                    setSavedPersona(persona);
                    setNotice("已保存演示资料，只保存在当前空间的页面内存。");
                  }}
                >
                  保存助理资料
                </Button>
              </section>
              <section className="settings-group">
                <h2>界面</h2>
                <Row title="外观" description="选择浅色或深色。">
                  <div className="segmented">
                    {["light", "dark"].map((value) => (
                      <button
                        key={value}
                        aria-pressed={theme === value}
                        onClick={() => setTheme(value)}
                      >
                        {value === "light" ? "浅色" : "深色"}
                      </button>
                    ))}
                  </div>
                </Row>
              </section>
            </>
          ) : category === "model" ? (
            <section className="settings-group">
              <h2>模型连接</h2>
              <Row
                title={model || "还没有配置模型"}
                description={
                  saved
                    ? "配置已保存，推理和工具能力需分别核对。"
                    : "先添加一个模型服务，再决定默认模型。"
                }
              >
                <Button onClick={() => go("model")}>
                  {saved ? "修改配置" : "添加模型服务"}
                </Button>
              </Row>
              <Detail title="模型与执行器有什么区别？">
                <p>
                  模型负责理解和生成。第三方执行器沿用自己的接入和会话控制；连接执行器不等于授予岗位权限。
                </p>
              </Detail>
            </section>
          ) : category === "computer" ? (
            <>
              <section className="settings-group">
                <h2>运行位置</h2>
                <Row title={space} description="对话、资料和专员属于这份助手。">
                  <Status>当前空间</Status>
                </Row>
                <Row
                  title="本机常驻助理"
                  description={
                    running
                      ? "正在运行；Mac 睡眠时暂停。"
                      : "需由你明确安装和启动。"
                  }
                >
                  <Button onClick={() => go(saved ? "install" : "model")}>
                    管理本机运行
                  </Button>
                </Row>
                <Row
                  title="另一台电脑"
                  description="连接已有助手，原资料留在原位置。"
                >
                  <Button onClick={() => go("remote")}>连接助手</Button>
                </Row>
              </section>
              <Detail title="断连时会怎样？">
                <p>保留原位置和最后读到的记录，不把旧记录当成实时。</p>
                <Button onClick={() => go("recovery")}>查看恢复设计</Button>
              </Detail>
            </>
          ) : category === "staff" ? (
            <section className="settings-group">
              <h2>岗位与专员</h2>
              <Row
                title="已登记专员"
                description="提议和批准是两个动作，权限只来自登记岗位。"
              >
                <Button onClick={() => go("staff")}>查看专员</Button>
              </Row>
              <Row
                title="主人确认"
                description="沿用主人端确认，不要求 Desktop 硬件钥匙。"
              >
                <Status>每次核对</Status>
              </Row>
            </section>
          ) : category === "permissions" ? (
            <section className="settings-group">
              <h2>操作权限</h2>
              <Row title="读取资料" description="仅在当前会话已有资料边界内。">
                <span>沿用原规则</span>
              </Row>
              <Row
                title="命令、外发和不可撤回操作"
                description="动作前展示具体内容，由主人确认。"
              >
                <Status warning>需要你决定</Status>
              </Row>
              <Detail title="最近的演示决定">
                <p>
                  {audit.length
                    ? `${audit.length} 条记录：${audit.map((item) => (item.allowed ? "已允许" : "已拒绝")).join("、")}`
                    : "还没有作出决定。"}
                </p>
              </Detail>
            </section>
          ) : category === "tools" ? (
            <section className="settings-group">
              <h2>插件与连接</h2>
              <Row
                title="还没有连接服务"
                description="添加连接不会自动授予操作权限。"
              >
                <Button
                  onClick={() =>
                    setNotice(
                      "连接管理沿用原工具入口，本设计稿不连接真实服务。",
                    )
                  }
                >
                  <Plus />
                  添加连接
                </Button>
              </Row>
              <Detail title="执行器与会话控制">
                <p>
                  第三方 CLI／ACP 仍由原适配器接入；不新增运行循环或接管授权。
                </p>
              </Detail>
            </section>
          ) : (
            <section className="settings-group">
              <h2>VortoOne</h2>
              <Row
                title="版本与更新"
                description="由你发起，不在后台替换正在使用的版本。"
              >
                <Button onClick={() => setNotice("设计稿不下载或安装更新。")}>
                  检查更新
                </Button>
              </Row>
              <Row title="诊断信息" description="需要排查问题时再打开。">
                <Button
                  onClick={() => setNotice("设计演示，没有读取真实日志。")}
                >
                  查看诊断
                </Button>
              </Row>
              <Detail title="关于这套设计">
                <p>
                  使用主人确认的角色家族，独立实现参考界面与操作，保留项目现有许可。
                </p>
              </Detail>
            </section>
          )}
        </div>
      </section>
    );
  }
  function recovery() {
    return (
      <section className="domain-page recovery">
        <Head
          title="暂时连不上这份助手。"
          description="原来的资料和设置保留。先核对连接，再决定下一步。"
        />
        <Avatar size={180} />
        <div className="recovery-card">
          <Server />
          <div>
            <strong>{space}</strong>
            <p>最后读取：今天 09:00 · 当前状态未确认</p>
          </div>
          <Status warning>连接中断</Status>
        </div>
        <p>
          它可能仍在运行，只是这台电脑现在看不到。旧记录不能代表当前执行结果。
        </p>
        <div className="form-actions">
          <Button
            primary
            onClick={() => {
              setRoute("chat");
              setNotice("恢复成功示例；真实产品先核对原运行端与实例。");
            }}
          >
            重连原助手
          </Button>
          <Button onClick={() => go("where")}>选择其它运行位置</Button>
        </div>
        <Detail title="重启前还在运行的工作">
          <p>停下等主人核对，列出受影响的记录；不自动重跑，也不替主人恢复。</p>
        </Detail>
      </section>
    );
  }
  function brand() {
    return (
      <section className="domain-page brand-page">
        <Head
          title="VortoOne 角色家族"
          description="形象帮助辨认谁在做事，文字说明它现在处于什么状态。"
        />
        <img
          className="family-reference"
          src={family}
          alt="主人选定的总助手、研究助手、开发助手、日程助手，以及静止、思考、招呼姿态"
        />
        <div className="brand-rules">
          <h2>用在身份，不铺满界面。</h2>
          <p>
            珊瑚橙是总助手；紫色眼镜是研究；绿色耳机是开发；黄色围巾是日程。设置、表单和按钮保持中性；等待、失败和未知仍由文字与状态图标说明。
          </p>
          <p>
            自定义头像继续优先。角色图不创建岗位、不附带权限，也不表示任务已验收。
          </p>
        </div>
      </section>
    );
  }
  return (
    <div className="prototype-root">
      <div className="review-toolbar">
        <span>设计演示 · 合成内容，不安装、不联网</span>
        <select
          aria-label="切换设计页面"
          value={route}
          onChange={(e) => go(e.target.value)}
        >
          {SCREENS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <button
          className="icon-button"
          aria-label="切换浅深色"
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
        >
          {theme === "light" ? <Moon /> : <Sun />}
        </button>
      </div>
      <main
        ref={surface}
        className={`app-frame ${SETUP.includes(route) ? "setup-frame" : `workspace-frame ${route === "settings" ? "settings-frame" : ""}`} ${sidebar ? "" : "sidebar-hidden"}`}
      >
        {SETUP.includes(route) ? (
          setup()
        ) : (
          <>
            {navigation()}
            {!sidebar && (
              <button
                className="sidebar-reopen icon-button"
                aria-label="展开侧栏"
                onClick={() => setSidebar(true)}
              >
                <Menu />
              </button>
            )}
            <div className="main-surface">
              {["chat", "approval"].includes(route)
                ? chat()
                : route === "staff"
                  ? specialists()
                  : route === "routines"
                    ? schedule()
                    : route === "library"
                      ? library()
                      : route === "settings"
                        ? preferences()
                        : route === "recovery"
                          ? recovery()
                          : brand()}
            </div>
          </>
        )}
      </main>
      {leaveTo && (
        <Modal
          labelledby="leave-title"
          className="request-overlay"
          onClose={() => setLeaveTo(null)}
        >
          <section className="approval-sheet">
            <h1 id="leave-title">还有未保存的修改。</h1>
            <p>
              切换分类会保留草稿。离开设置会放弃未保存的助理资料，已经保存的内容不会撤销。
            </p>
            <footer>
              <Button onClick={() => setLeaveTo(null)}>继续编辑</Button>
              <Button
                primary
                onClick={() => {
                  setName(savedName);
                  setPersona(savedPersona);
                  setRoute(leaveTo);
                  setLeaveTo(null);
                }}
              >
                放弃草稿并离开
              </Button>
            </footer>
          </section>
        </Modal>
      )}
      {notice && (
        <div className="toast" role="status">
          <Check />
          <span>{notice}</span>
          <button
            className="icon-button"
            aria-label="关闭提示"
            onClick={() => setNotice("")}
          >
            <X />
          </button>
        </div>
      )}
      {role && (
        <Modal labelledby="role-title" onClose={() => setRole(null)}>
          <section className="sheet" onClick={(e) => e.stopPropagation()}>
            <button
              className="icon-button sheet-close"
              aria-label="关闭岗位详情"
              onClick={() => setRole(null)}
            >
              <X />
            </button>
            <Avatar kind={role.id} size={140} />
            <h1 id="role-title">{role.name}</h1>
            <p>{role.intro}</p>
            <dl>
              <dt>使用范围</dt>
              <dd>{role.scope}</dd>
              <dt>可以做什么</dt>
              <dd>{role.actions}</dd>
              <dt>限制与需要你决定的事</dt>
              <dd>{role.limit}</dd>
            </dl>
            {role.id === "development" && (
              <Field label="已登记项目">
                <select>
                  <option>VortoOne · 演示项目</option>
                </select>
              </Field>
            )}
            <footer>
              {staff.includes(role.id) ? (
                <Button onClick={() => setRole(null)}>返回专员</Button>
              ) : (
                <Button primary onClick={() => ask("staff", role)}>
                  提交专员提议
                  <ArrowRight />
                </Button>
              )}
            </footer>
          </section>
        </Modal>
      )}
      {routineForm && (
        <Modal labelledby="routine-title" onClose={() => setRoutineForm(false)}>
          <section className="sheet" onClick={(e) => e.stopPropagation()}>
            <button
              className="icon-button sheet-close"
              aria-label="关闭新建例行工作"
              onClick={() => setRoutineForm(false)}
            >
              <X />
            </button>
            <h1 id="routine-title">安排一件例行工作。</h1>
            <p>选择已登记的专员，写清交付结果，再核对排期。</p>
            <Field label="工作名称">
              <input
                value={routineName}
                onChange={(e) => setRoutineName(e.target.value)}
              />
            </Field>
            <Field label="交给谁">
              <select
                value={routineRole}
                onChange={(e) => setRoutineRole(e.target.value)}
              >
                {ROLES.slice(1).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {staff.includes(item.id) ? "" : " · 尚未登记"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="排期">
              <select>
                <option>每周五 17:00</option>
              </select>
            </Field>
            <Field label="使用时区">
              <select value={zone} onChange={(e) => setZone(e.target.value)}>
                <option>Asia/Shanghai</option>
                <option>America/New_York</option>
              </select>
            </Field>
            <p className="hint">产品应读取你已确认的时区，这里是演示值。</p>
            {!staff.includes(routineRole) && (
              <p className="alert">请先提议并批准这位专员。</p>
            )}
            <Button
              primary
              disabled={!staff.includes(routineRole) || !routineName.trim()}
              onClick={() =>
                ask("routine", {
                  name: routineName,
                  staff: ROLES.find((item) => item.id === routineRole).name,
                  when: "每周五 17:00",
                })
              }
            >
              预览并提交确认
              <ArrowRight />
            </Button>
          </section>
        </Modal>
      )}
      {request && (
        <Modal
          className="request-overlay"
          labelledby="request-title"
          onClose={() => setRequest(null)}
        >
          <section className="approval-sheet">
            <header>
              <Avatar
                kind={request.kind === "staff" ? request.payload.id : "general"}
                size={80}
              />
              <div>
                <p className="eyebrow">由你决定</p>
                <h1 id="request-title">
                  {request.kind === "staff"
                    ? `批准登记${request.payload.name}？`
                    : ["routine", "resume"].includes(request.kind)
                      ? `${request.kind === "resume" ? "重新启用" : "批准例行工作"}「${request.payload.name}」？`
                      : "保存这份邮件草稿？"}
                </h1>
              </div>
            </header>
            <p>
              {request.kind === "staff"
                ? request.payload.actions
                : ["routine", "resume"].includes(request.kind)
                  ? `${request.payload.staff} · ${request.payload.when} · ${zone}`
                  : request.payload.description}
            </p>
            <dl>
              <dt>属于哪份助手</dt>
              <dd>
                {request.space} · {request.project}
              </dd>
              <dt>操作范围</dt>
              <dd>
                {request.kind === "staff"
                  ? request.payload.scope
                  : ["routine", "resume"].includes(request.kind)
                    ? "按排期开始，结果回到原记录。"
                    : "只保存草稿，不对外发送。"}
              </dd>
              <dt>需要注意</dt>
              <dd>
                {request.kind === "staff"
                  ? request.payload.limit
                  : "权限与确认规则保持原样；过期或实例变化后需要重新核对。"}
              </dd>
            </dl>
            {!currentRequest && (
              <p className="alert">
                空间或项目已变化，这张卡不能继续批准。请回原记录重新核对。
              </p>
            )}
            <Detail title="为什么要再次确认？">
              <p>
                真实产品将提议、动作内容、轮次、有效期和运行实例绑定原卡片，先写审计再放行。此页只演示交互。
              </p>
            </Detail>
            <footer>
              <Button disabled={!currentRequest} onClick={() => decide(false)}>
                拒绝此项请求
              </Button>
              <Button
                primary
                disabled={!currentRequest}
                onClick={() => decide(true)}
              >
                {request.kind === "staff"
                  ? "批准这个岗位"
                  : ["routine", "resume"].includes(request.kind)
                    ? "批准这份排期"
                    : "批准保存草稿"}
              </Button>
            </footer>
            <button className="text-button" onClick={() => setRequest(null)}>
              暂时不决定
            </button>
          </section>
        </Modal>
      )}
    </div>
  );
}
