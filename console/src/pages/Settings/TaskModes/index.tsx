import { useState, useEffect, useCallback } from "react";
import { Card, Form, Switch, Slider, Button, Tag, Space, Spin } from "antd";
import {
  TeamOutlined,
  ApartmentOutlined,
  RocketOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/api";
import { useAppMessage } from "@/hooks/useAppMessage";
import type { TaskModesConfig } from "@/api/types";
import styles from "./index.module.less";

/* ── Slider with inline value ──────────────────────────────────────── */

function SliderWithValue({
  value,
  min,
  max,
  step,
  onChange,
}: {
  value?: number;
  min: number;
  max: number;
  step?: number;
  onChange?: (v: number) => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <div style={{ flex: 1 }}>
        <Slider
          value={value}
          min={min}
          max={max}
          step={step ?? 1}
          onChange={onChange}
        />
      </div>
      <div style={{ minWidth: 50, textAlign: "right", lineHeight: "32px" }}>
        <span className={styles.sliderValue}>
          {value !== undefined ? String(value) : "-"}
        </span>
      </div>
    </div>
  );
}

/* ── Mode Card ─────────────────────────────────────────────────────── */

interface ModeCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  color: string;
  children: React.ReactNode;
}

function ModeCard({
  icon,
  title,
  description,
  color,
  children,
}: ModeCardProps) {
  return (
    <Card
      className={styles.modeCard}
      title={
        <Space>
          <Tag
            color={color}
            style={{ marginRight: 0, fontSize: 16, padding: "2px 8px" }}
          >
            {icon}
          </Tag>
          <span style={{ fontSize: 15 }}>{title}</span>
          <span
            style={{
              fontSize: 12,
              color: "var(--text-secondary)",
              fontWeight: 400,
            }}
          >
            {description}
          </span>
        </Space>
      }
    >
      {children}
    </Card>
  );
}

/* ── Task Modes Page ───────────────────────────────────────────────── */

export default function TaskModesPage() {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [form] = Form.useForm<TaskModesConfig>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    try {
      const config = await api.getTaskModes();
      form.setFieldsValue(config);
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : t("taskModes.loadFailed");
      message.error(errMsg);
    } finally {
      setLoading(false);
    }
  }, [form, t, message]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSave = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await api.updateTaskModes(values as TaskModesConfig);
      message.success(t("taskModes.saveSuccess"));
    } catch (err) {
      if (err instanceof Error && "errorFields" in err) return;
      const errMsg =
        err instanceof Error ? err.message : t("taskModes.saveFailed");
      message.error(errMsg);
    } finally {
      setSaving(false);
    }
  }, [form, t, message]);

  if (loading) {
    return (
      <div className={styles.page}>
        <div className={styles.centerState}>
          <Spin size="large" />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader parent={t("nav.settings")} current={t("nav.taskModes")} />

      <Form form={form} layout="vertical" className={styles.form}>
        {/* ── spawn_subagents ─────────────────────────────────────── */}
        <ModeCard
          icon={<TeamOutlined />}
          title={t("taskModes.spawnSubagents.title")}
          description={t("taskModes.spawnSubagents.desc")}
          color="blue"
        >
          <Form.Item
            label={t("taskModes.spawnSubagents.maxConcurrency")}
            name={["spawn_subagents", "max_concurrency"]}
            tooltip={t("taskModes.spawnSubagents.maxConcurrencyTip")}
          >
            <SliderWithValue min={1} max={32} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.spawnSubagents.maxSubagents")}
            name={["spawn_subagents", "max_subagents"]}
            tooltip={t("taskModes.spawnSubagents.maxSubagentsTip")}
          >
            <SliderWithValue min={1} max={25} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.spawnSubagents.timeoutSeconds")}
            name={["spawn_subagents", "timeout_seconds"]}
            tooltip={t("taskModes.timeoutTip")}
          >
            <SliderWithValue min={30} max={1800} step={10} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.allowNesting")}
            name={["spawn_subagents", "allow_nesting"]}
            valuePropName="checked"
            tooltip={t("taskModes.nestingTip")}
          >
            <Switch />
          </Form.Item>

          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) =>
              prev?.spawn_subagents?.allow_nesting !==
              cur?.spawn_subagents?.allow_nesting
            }
          >
            {({ getFieldValue }) =>
              getFieldValue(["spawn_subagents", "allow_nesting"]) ? (
                <Form.Item
                  label={t("taskModes.nestingMaxDepth")}
                  name={["spawn_subagents", "nesting_max_depth"]}
                >
                  <SliderWithValue min={1} max={5} />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </ModeCard>

        {/* ── coordinate_workflow ─────────────────────────────────── */}
        <ModeCard
          icon={<ApartmentOutlined />}
          title={t("taskModes.coordinateWorkflow.title")}
          description={t("taskModes.coordinateWorkflow.desc")}
          color="green"
        >
          <Form.Item
            label={t("taskModes.coordinateWorkflow.maxConcurrency")}
            name={["coordinate_workflow", "max_concurrency"]}
            tooltip={t("taskModes.coordinateWorkflow.maxConcurrencyTip")}
          >
            <SliderWithValue min={1} max={32} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.coordinateWorkflow.maxSteps")}
            name={["coordinate_workflow", "max_steps"]}
          >
            <SliderWithValue min={1} max={25} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.coordinateWorkflow.timeoutSeconds")}
            name={["coordinate_workflow", "timeout_seconds"]}
            tooltip={t("taskModes.timeoutTip")}
          >
            <SliderWithValue min={60} max={3600} step={30} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.coordinateWorkflow.stepTimeoutSeconds")}
            name={["coordinate_workflow", "step_timeout_seconds"]}
          >
            <SliderWithValue min={30} max={600} step={10} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.allowNesting")}
            name={["coordinate_workflow", "allow_nesting"]}
            valuePropName="checked"
            tooltip={t("taskModes.nestingTip")}
          >
            <Switch />
          </Form.Item>
        </ModeCard>

        {/* ── delegate_task ───────────────────────────────────────── */}
        <ModeCard
          icon={<RocketOutlined />}
          title={t("taskModes.delegateTask.title")}
          description={t("taskModes.delegateTask.desc")}
          color="orange"
        >
          <Form.Item
            label={t("taskModes.delegateTask.timeoutSeconds")}
            name={["delegate_task", "timeout_seconds"]}
            tooltip={t("taskModes.timeoutTip")}
          >
            <SliderWithValue min={30} max={3600} step={30} />
          </Form.Item>

          <Form.Item
            label={t("taskModes.allowNesting")}
            name={["delegate_task", "allow_nesting"]}
            valuePropName="checked"
            tooltip={t("taskModes.nestingTip")}
          >
            <Switch />
          </Form.Item>
        </ModeCard>

        {/* ── Save ────────────────────────────────────────────────── */}
        <div className={styles.saveBar}>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSave}
            loading={saving}
            size="large"
          >
            {t("common.save")}
          </Button>
        </div>
      </Form>
    </div>
  );
}
