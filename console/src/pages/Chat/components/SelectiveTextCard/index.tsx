import { Markdown, useProviderContext } from "@agentscope-ai/chat";
import { CodeHighlighter } from "@ant-design/x";
import { SparkCopyLine, SparkTrueLine } from "@agentscope-ai/icons";
import { useCallback, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

type MarkdownCodeProps = {
  className?: string;
  children?: ReactNode;
};

function normalizeMarkdownLayout(content: string): string {
  return content.replace(
    /([^\n])\s+(方案[一二三四五六七八九十A-Z]：)/g,
    "$1\n\n$2",
  );
}

const COPYABLE_LANGS = new Set([
  "bash",
  "sh",
  "shell",
  "zsh",
  "shellscript",
  "powershell",
  "ps1",
  "cmd",
  "bat",
  "python",
  "py",
  "javascript",
  "js",
  "typescript",
  "ts",
  "tsx",
  "jsx",
  "json",
  "yaml",
  "yml",
  "toml",
  "ini",
  "sql",
  "html",
  "css",
  "scss",
  "less",
  "xml",
  "java",
  "go",
  "rust",
  "rs",
  "php",
  "ruby",
  "rb",
  "kotlin",
  "swift",
  "dockerfile",
  "makefile",
]);

function looksLikeCopyableBlock(content: string, lang: string): boolean {
  const normalizedLang = lang.trim().toLowerCase();
  const trimmed = content.trim();

  if (!trimmed) return false;
  if (normalizedLang === "mermaid") return false;
  if (COPYABLE_LANGS.has(normalizedLang)) return true;

  if (!normalizedLang) {
    if (
      /^\$?\s*(npm|pnpm|yarn|bun|node|python|python3|pip|uv|git|curl|wget|docker|kubectl|npx|source|export|cat|cd|ls|cp|mv|rm)\b/m.test(
        trimmed,
      )
    ) {
      return true;
    }

    if (
      /(^|\n)\s*(import |export |from |const |let |var |def |class |function |async |await |return |SELECT |INSERT |UPDATE |DELETE |CREATE |ALTER |DROP |interface |type )/m.test(
        trimmed,
      )
    ) {
      return true;
    }

    if (/^\s*[A-Za-z_][A-Za-z0-9_]*\s*=.+/m.test(trimmed)) {
      return true;
    }

    if (/[{}[\]();<>]/.test(trimmed)) {
      return true;
    }

    if (trimmed.includes("\n") && /(^|\n)\s{2,}\S/.test(content)) {
      return true;
    }
  }

  return false;
}

function CopyOnlyHeader({ content, lang }: { content: string; lang: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const { getPrefixCls } = useProviderContext();
  const prefixCls = getPrefixCls("code-header");

  const handleCopy = useCallback(async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(content);
      } else {
        const textArea = document.createElement("textarea");
        textArea.value = content;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        textArea.style.top = "-999999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand("copy");
        textArea.remove();
      }

      clearTimeout(timer.current);
      setCopied(true);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // Keep this non-blocking; failed copy should not break rendering.
    }
  }, [content]);

  return (
    <div className={prefixCls}>
      <div className={`${prefixCls}-lang`}>{lang}</div>
      <div className={`${prefixCls}-actions`}>
        {copied ? (
          <SparkTrueLine className={`${prefixCls}-copied`} />
        ) : (
          <SparkCopyLine className={`${prefixCls}-icon`} onClick={handleCopy} />
        )}
      </div>
    </div>
  );
}

function SelectiveCodeBlock(props: MarkdownCodeProps) {
  const { className = "", children } = props;
  const content =
    typeof children === "string" ? children : String(children ?? "");
  const langMatch = className.match(/language-([A-Za-z0-9_-]+)/);
  const lang = (langMatch?.[1] ?? "").toLowerCase();
  const showCopy = looksLikeCopyableBlock(content, lang);

  return (
    <CodeHighlighter
      lang={lang}
      header={
        showCopy ? <CopyOnlyHeader content={content} lang={lang} /> : undefined
      }
    >
      {content}
    </CodeHighlighter>
  );
}

export default function SelectiveTextCard(props: any) {
  const cursor = props.data.msgStatus === "generating";
  const markdownData = useMemo(() => {
    if (typeof props.data?.content !== "string") {
      return props.data;
    }

    return {
      ...props.data,
      content: normalizeMarkdownLayout(props.data.content),
    };
  }, [props.data]);

  const components = useMemo(
    () => ({
      code: SelectiveCodeBlock,
      ...(markdownData?.components ?? {}),
    }),
    [markdownData?.components],
  );

  return (
    <Markdown
      cursor={cursor}
      {...markdownData}
      components={components}
      typing={
        markdownData.msgStatus === "generating" ? markdownData.typing : false
      }
    />
  );
}
