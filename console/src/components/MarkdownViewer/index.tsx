import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ComponentPropsWithoutRef, CSSProperties } from "react";
import styles from "./index.module.less";

interface MarkdownViewerProps {
  content: string;
  className?: string;
  style?: CSSProperties;
}

export function MarkdownViewer({
  content,
  className,
  style,
}: MarkdownViewerProps) {
  type MarkdownCodeProps = ComponentPropsWithoutRef<"code"> & {
    inline?: boolean;
    node?: unknown;
  };

  const rootClassName = [styles.markdownViewer, className]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={rootClassName} style={style}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ node: _node, ...props }) {
            return <a {...props} target="_blank" rel="noopener noreferrer" />;
          },
          code(rawProps) {
            const {
              inline = false,
              node: _node,
              className: codeClassName,
              children,
              ...props
            } = rawProps as MarkdownCodeProps;
            const code = String(children).replace(/\n$/, "");

            if (inline) {
              return (
                <code
                  {...props}
                  className={[styles.inlineCode, codeClassName]
                    .filter(Boolean)
                    .join(" ")}
                >
                  {code}
                </code>
              );
            }

            return (
              <pre className={styles.codeBlock}>
                <code {...props} className={codeClassName}>
                  {code}
                </code>
              </pre>
            );
          },
        }}
      >
        {content || ""}
      </ReactMarkdown>
    </div>
  );
}

export default MarkdownViewer;
