import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import CssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import HtmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import TypeScriptWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

const monacoHost = self as typeof self & {
  MonacoEnvironment?: { getWorker: (_moduleId: string, label: string) => Worker };
};
monacoHost.MonacoEnvironment = {
  getWorker: (_moduleId, label) => {
    if (label === "json") return new JsonWorker();
    if (["css", "scss", "less"].includes(label)) return new CssWorker();
    if (["html", "handlebars", "razor"].includes(label)) return new HtmlWorker();
    if (["typescript", "javascript"].includes(label)) return new TypeScriptWorker();
    return new EditorWorker();
  },
};
loader.config({ monaco });

function editorLanguage(path: string): string {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  return {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    py: "python",
    rs: "rust",
    go: "go",
    java: "java",
    json: "json",
    css: "css",
    scss: "scss",
    html: "html",
    md: "markdown",
    yaml: "yaml",
    yml: "yaml",
    toml: "ini",
    sh: "shell",
    zsh: "shell",
  }[extension] ?? "plaintext";
}

type MonacoEditorProps = {
  path: string;
  value: string;
  onChange: (value: string) => void;
};

export function MonacoEditor({ path, value, onChange }: MonacoEditorProps) {
  return (
    <Editor
      path={path}
      language={editorLanguage(path)}
      theme="vs-dark"
      value={value}
      onChange={(next) => onChange(next ?? "")}
      loading={<div className="file-loading">正在准备代码编辑器…</div>}
      options={{
        automaticLayout: true,
        fontFamily: '"DM Mono", "SFMono-Regular", Consolas, monospace',
        fontSize: 12,
        lineHeight: 20,
        minimap: { enabled: false },
        overviewRulerBorder: false,
        overviewRulerLanes: 0,
        padding: { top: 12, bottom: 12 },
        renderLineHighlight: "line",
        scrollBeyondLastLine: false,
        smoothScrolling: true,
        tabSize: 2,
        wordWrap: "off",
      }}
    />
  );
}
