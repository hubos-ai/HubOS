/**
 * StatusToolCard — renders pre-agent status cards
 * (Context understanding / Experience matching / Knowledge injection).
 *
 * Registered as a customToolRenderConfig entry so the AgentScope
 * chat library uses it instead of the default ToolCall card.
 */
import { OperateCard, useProviderContext } from "@agentscope-ai/chat";
import { SparkTrueLine } from "@agentscope-ai/icons";
import { LoadingOutlined } from "@ant-design/icons";

const STATUS_TOOL_NAMES = new Set([
  "Context understanding",
  "Experience matching",
  "Knowledge injection",
]);

/**
 * Check whether a tool name belongs to the pre-agent status tool set.
 */
export function isStatusToolName(name: string): boolean {
  return STATUS_TOOL_NAMES.has(name);
}

interface StatusToolCardProps {
  data: {
    content?: Array<{
      type: string;
      data?: Record<string, unknown>;
    }>;
    status?: string;
  };
}

/**
 * Extract the output string from a merged tool message.
 * content[0] = call data, content[1] = output data.
 */
function extractOutput(
  content: StatusToolCardProps["data"]["content"],
): string {
  if (!content || content.length === 0) {
    return "";
  }

  const outputData =
    content.find((item) => {
      const data = item.data;
      return Boolean(
        data &&
          (data.output !== undefined ||
            data.result !== undefined ||
            data.text !== undefined ||
            data.content !== undefined),
      );
    })?.data ?? content[content.length - 1]?.data;

  if (!outputData) return "";
  const raw =
    outputData.output ??
    outputData.result ??
    outputData.text ??
    outputData.content ??
    "";
  return typeof raw === "string" ? raw : JSON.stringify(raw);
}

function extractToolName(
  content: StatusToolCardProps["data"]["content"],
): string {
  const withName = content?.find((item) => {
    const name = item.data?.name;
    return typeof name === "string" && isStatusToolName(name);
  });
  const fallback = content?.find((item) => typeof item.data?.name === "string");
  return ((withName ?? fallback)?.data?.name as string) ?? "Status";
}

export default function StatusToolCard({ data }: StatusToolCardProps) {
  const { getPrefixCls } = useProviderContext();
  const prefixCls = getPrefixCls("operate-card");
  const toolName = extractToolName(data.content);
  const output = extractOutput(data.content);
  const done = data.status === "completed";
  const icon = done ? (
    <SparkTrueLine />
  ) : (
    <LoadingOutlined style={{ color: "#ff7f16", fontSize: 13 }} />
  );

  return (
    <OperateCard
      header={{
        icon,
        title: toolName,
      }}
      body={{
        defaultOpen: false,
        children: output ? (
          <div
            className={`${prefixCls}-tool-call-block`}
            style={{ padding: "8px 12px" }}
          >
            <span style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>
              {output}
            </span>
          </div>
        ) : undefined,
      }}
    />
  );
}
