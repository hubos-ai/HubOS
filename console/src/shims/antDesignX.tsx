import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import styles from "./antDesignX.module.less";

type SuggestionItem = {
  value?: string;
  label?: ReactNode;
  icon?: ReactNode;
  extra?: ReactNode;
  children?: SuggestionItem[];
};

type SuggestionRenderProps = {
  open: boolean;
  onTrigger: (open: boolean) => void;
  onKeyDown: (event: KeyboardEvent<Element>) => void;
};

type SuggestionProps = {
  items?: SuggestionItem[];
  onSelect?: (value: string) => void;
  children: (props: SuggestionRenderProps) => ReactNode;
};

type FlatSuggestionItem = {
  key: string;
  value: string;
  label: ReactNode;
  icon?: ReactNode;
  extra?: ReactNode;
};

function flattenSuggestionItems(
  items: SuggestionItem[] = [],
): FlatSuggestionItem[] {
  const result: FlatSuggestionItem[] = [];

  const walk = (list: SuggestionItem[]) => {
    list.forEach((item, index) => {
      if (!item || typeof item !== "object") return;

      if (typeof item.value === "string" && item.value.trim()) {
        result.push({
          key: `${item.value}-${index}-${result.length}`,
          value: item.value,
          label: item.label ?? item.value,
          icon: item.icon,
          extra: item.extra,
        });
      }

      if (Array.isArray(item.children) && item.children.length > 0) {
        walk(item.children);
      }
    });
  };

  walk(items);
  return result;
}

export function Suggestion({
  items = [],
  onSelect,
  children,
}: SuggestionProps) {
  const flattenedItems = useMemo(
    () => flattenSuggestionItems(items),
    [items],
  );
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (!open) return;
    if (flattenedItems.length === 0) {
      setOpen(false);
      return;
    }
    if (activeIndex >= flattenedItems.length) {
      setActiveIndex(0);
    }
  }, [open, flattenedItems, activeIndex]);

  const triggerOpen = (nextOpen: boolean) => {
    const shouldOpen = nextOpen && flattenedItems.length > 0;
    setOpen(shouldOpen);
    if (shouldOpen) {
      setActiveIndex(0);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<Element>) => {
    if (!open || flattenedItems.length === 0) return;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActiveIndex((prev) => (prev + 1) % flattenedItems.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex(
          (prev) => (prev - 1 + flattenedItems.length) % flattenedItems.length,
        );
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(flattenedItems.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        setOpen(false);
        break;
      default:
        break;
    }
  };

  const renderProps: SuggestionRenderProps = {
    open,
    onTrigger: triggerOpen,
    onKeyDown: handleKeyDown,
  };

  return (
    <div className={styles.suggestionShell}>
      {children(renderProps)}
      {open && flattenedItems.length > 0 ? (
        <div className={styles.suggestionMenu} role="menu">
          {flattenedItems.map((item, index) => {
            const isActive = index === activeIndex;
            return (
              <button
                key={item.key}
                type="button"
                role="menuitem"
                title={item.value}
                aria-current={isActive ? "true" : undefined}
                data-path-key={item.value}
                className={[
                  styles.suggestionItem,
                  isActive ? styles.suggestionItemActive : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => {
                  onSelect?.(item.value);
                  setOpen(false);
                }}
              >
                {item.icon ? (
                  <span className={styles.suggestionIcon}>{item.icon}</span>
                ) : null}
                <span className={styles.suggestionLabel}>{item.label}</span>
                {item.extra ? (
                  <span className={styles.suggestionExtra}>{item.extra}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

type PanelProps = {
  header?: ReactNode;
  children?: ReactNode;
};

function SimplePanel({ header, children }: PanelProps) {
  return (
    <div className={styles.panel}>
      {header ? <div className={styles.panelHeader}>{header}</div> : null}
      <div className={styles.panelBody}>{children}</div>
    </div>
  );
}

type CodeHighlighterProps = {
  header?: ReactNode;
  children?: ReactNode;
  lang?: string;
};

function looksLikeExecutableBlock(content: string): boolean {
  const trimmed = content.trim();
  if (!trimmed) return false;

  if (/^\$?\s*(npm|pnpm|yarn|bun|node|python|python3|pip|uv|git|curl|wget|docker|kubectl|sqlcmd|psql)\b/m.test(trimmed)) {
    return true;
  }

  if (
    /(^|\n)\s*(import |export |from |const |let |var |def |class |function |async |await |return |SELECT |INSERT |UPDATE |DELETE |CREATE |ALTER |DROP )/m.test(
      trimmed,
    )
  ) {
    return true;
  }

  if (/[{}[\]();<>]/.test(trimmed)) {
    return true;
  }

  return trimmed.includes("\n") && /(^|\n)\s{2,}\S/.test(content);
}

export function CodeHighlighter({
  header,
  children,
  lang,
}: CodeHighlighterProps) {
  const content =
    typeof children === "string" ? children : String(children ?? "");
  const shouldShowHeader = Boolean(lang?.trim()) || looksLikeExecutableBlock(content);

  return (
    <SimplePanel header={shouldShowHeader ? header : undefined}>
      <pre className={styles.codeBlock}>
        <code>{content}</code>
      </pre>
    </SimplePanel>
  );
}

type MermaidProps = {
  header?: ReactNode;
  children?: ReactNode;
};

export function Mermaid({ header, children }: MermaidProps) {
  return (
    <SimplePanel header={header}>
      <pre className={styles.mermaidFallback}>
        <code>{typeof children === "string" ? children : String(children ?? "")}</code>
      </pre>
    </SimplePanel>
  );
}

type BubbleProps = {
  content?: ReactNode;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
};

function BubbleBase({ content, children, className, style }: BubbleProps) {
  return (
    <div className={className} style={style}>
      {content ?? children}
    </div>
  );
}

function BubbleList({
  items = [],
}: {
  items?: Array<BubbleProps & { key?: string | number }>;
}) {
  return (
    <div>
      {items.map((item, index) => (
        <BubbleBase
          key={item.key ?? index}
          content={item.content}
          className={item.className}
          style={item.style}
        >
          {item.children}
        </BubbleBase>
      ))}
    </div>
  );
}

export const Bubble = Object.assign(BubbleBase, {
  List: BubbleList,
});
