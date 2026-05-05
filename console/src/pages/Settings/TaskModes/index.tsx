import { useState, useEffect, useCallback } from "react";
import { Card, Form, InputNumber, Switch, Slider, Button } from "antd";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/api";
import { useAppMessage } from "@/hooks/useAppMessage";
import type { AgentsRunningConfig } from "@/api/types";
import styles from "./index.module.less";

/* ── Slider with inline value display ──────────────────────────────── */

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
          {value !== undefined
            ? value >= 1
              ? String(value)
              : value.toFixed(2)
            : "-"}
        </span>
      </div>
    </div>
  );
}

/* ── Task Modes Page ───────────────────────────────────────────────── */

export default function TaskModesPage() {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const config = await api.getAgentRunningConfig();
      form.setFieldsValue(config);
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : t("taskModes.loadFailed");
      setError(errMsg);
    } finally {
      setLoading(false);
    }
  }, [form, t]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSave = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await api.updateAgentRunningConfig(values as AgentsRunningConfig);
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

  const llmRetryEnabled = Form.useWatch("llm_retry_enabled", form) ?? true;

  if (loading) {
    return (
      <div className={styles.page}>
        <div className={styles.centerState}>
          <span className={styles.stateText}>{t("common.loading")}</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.centerState}>
          <span className={styles.stateTextError}>{error}</span>
          <Button size="small" onClick={fetchConfig} style={{ marginTop: 12 }}>
            {t("common.refresh")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader parent={t("nav.settings")} current={t("taskModes.title")} />

      <div className={styles.pageContent}>
        <div className={styles.formContainer}>
          <Form form={form} layout="vertical" className={styles.form}>
            {/* ── LLM Concurrency & Rate Limiting ──────────────────── */}
            <Card
              className={styles.formCard}
              title={t("taskModes.llmConcurrencyTitle")}
              style={{ marginTop: 16 }}
            >
              <Form.Item
                label={t("taskModes.llmMaxConcurrent")}
                name="llm_max_concurrent"
                rules={[
                  {
                    required: true,
                    message: t("taskModes.llmMaxConcurrentRequired"),
                  },
                  {
                    type: "number",
                    min: 1,
                    message: t("taskModes.llmMaxConcurrentRange"),
                  },
                ]}
                tooltip={t("taskModes.llmMaxConcurrentTooltip")}
              >
                <SliderWithValue min={1} max={20} />
              </Form.Item>

              <Form.Item
                label={t("taskModes.llmMaxQpm")}
                name="llm_max_qpm"
                rules={[
                  {
                    required: true,
                    message: t("taskModes.llmMaxQpmRequired"),
                  },
                  {
                    type: "number",
                    min: 0,
                    message: t("taskModes.llmMaxQpmRange"),
                  },
                ]}
                tooltip={t("taskModes.llmMaxQpmTooltip")}
              >
                <InputNumber
                  style={{ width: "100%" }}
                  min={0}
                  step={10}
                  placeholder={t("taskModes.llmMaxQpmPlaceholder")}
                />
              </Form.Item>

              <Form.Item
                label={t("taskModes.llmRateLimitPause")}
                name="llm_rate_limit_pause"
                rules={[
                  {
                    required: true,
                    message: t("taskModes.llmRateLimitPauseRequired"),
                  },
                  {
                    type: "number",
                    min: 1.0,
                    message: t("taskModes.llmRateLimitPauseMin"),
                  },
                ]}
                tooltip={t("taskModes.llmRateLimitPauseTooltip")}
              >
                <InputNumber
                  style={{ width: "100%" }}
                  step={0.5}
                  placeholder={t("taskModes.llmRateLimitPausePlaceholder")}
                />
              </Form.Item>

              <Form.Item
                label={t("taskModes.llmRateLimitJitter")}
                name="llm_rate_limit_jitter"
                rules={[
                  {
                    required: true,
                    message: t("taskModes.llmRateLimitJitterRequired"),
                  },
                  {
                    type: "number",
                    min: 0.0,
                    message: t("taskModes.llmRateLimitJitterMin"),
                  },
                ]}
                tooltip={t("taskModes.llmRateLimitJitterTooltip")}
              >
                <InputNumber
                  style={{ width: "100%" }}
                  step={0.5}
                  placeholder={t("taskModes.llmRateLimitJitterPlaceholder")}
                />
              </Form.Item>

              <Form.Item
                label={t("taskModes.llmAcquireTimeout")}
                name="llm_acquire_timeout"
                rules={[
                  {
                    required: true,
                    message: t("taskModes.llmAcquireTimeoutRequired"),
                  },
                  {
                    type: "number",
                    min: 10.0,
                    message: t("taskModes.llmAcquireTimeoutMin"),
                  },
                ]}
                tooltip={t("taskModes.llmAcquireTimeoutTooltip")}
              >
                <InputNumber
                  style={{ width: "100%" }}
                  step={10}
                  placeholder={t("taskModes.llmAcquireTimeoutPlaceholder")}
                />
              </Form.Item>
            </Card>

            {/* ── Retry Configuration ───────────────────────────────── */}
            <Card
              className={styles.formCard}
              title={t("taskModes.llmRetryTitle")}
              style={{ marginTop: 16 }}
            >
              <Form.Item
                name="llm_retry_enabled"
                label={t("taskModes.llmRetryEnabled")}
                valuePropName="checked"
                tooltip={t("taskModes.llmRetryEnabledTooltip")}
              >
                <Switch />
              </Form.Item>

              <div className={styles.llmRetryRow}>
                <Form.Item
                  label={t("taskModes.llmMaxRetries")}
                  name="llm_max_retries"
                  rules={[
                    {
                      required: true,
                      message: t("taskModes.llmMaxRetriesRequired"),
                    },
                    {
                      type: "number",
                      min: 1,
                      message: t("taskModes.llmMaxRetriesMin"),
                    },
                  ]}
                  tooltip={t("taskModes.llmMaxRetriesTooltip")}
                  className={styles.llmRetryField}
                >
                  <InputNumber
                    style={{ width: "100%" }}
                    min={1}
                    step={1}
                    disabled={!llmRetryEnabled}
                    placeholder={t("taskModes.llmMaxRetriesPlaceholder")}
                  />
                </Form.Item>

                <Form.Item
                  label={t("taskModes.llmBackoffBase")}
                  name="llm_backoff_base"
                  rules={[
                    {
                      required: true,
                      message: t("taskModes.llmBackoffBaseRequired"),
                    },
                    {
                      type: "number",
                      min: 0.1,
                      message: t("taskModes.llmBackoffBaseMin"),
                    },
                  ]}
                  tooltip={t("taskModes.llmBackoffBaseTooltip")}
                  className={styles.llmRetryField}
                >
                  <InputNumber
                    style={{ width: "100%" }}
                    step={0.1}
                    disabled={!llmRetryEnabled}
                    placeholder={t("taskModes.llmBackoffBasePlaceholder")}
                  />
                </Form.Item>

                <Form.Item
                  label={t("taskModes.llmBackoffCap")}
                  name="llm_backoff_cap"
                  dependencies={["llm_backoff_base"]}
                  rules={[
                    {
                      required: true,
                      message: t("taskModes.llmBackoffCapRequired"),
                    },
                    {
                      type: "number",
                      min: 0.5,
                      message: t("taskModes.llmBackoffCapMin"),
                    },
                  ]}
                  tooltip={t("taskModes.llmBackoffCapTooltip")}
                  className={styles.llmRetryField}
                >
                  <InputNumber
                    style={{ width: "100%" }}
                    step={0.5}
                    disabled={!llmRetryEnabled}
                    placeholder={t("taskModes.llmBackoffCapPlaceholder")}
                  />
                </Form.Item>
              </div>
            </Card>

            {/* ── Iteration Limit ───────────────────────────────────── */}
            <Card
              className={styles.formCard}
              title={t("taskModes.iterLimitTitle")}
              style={{ marginTop: 16 }}
            >
              <Form.Item
                label={t("taskModes.maxIters")}
                name="max_iters"
                rules={[
                  {
                    required: true,
                    message: t("taskModes.maxItersRequired"),
                  },
                  {
                    type: "number",
                    min: 1,
                    message: t("taskModes.maxItersMin"),
                  },
                ]}
                tooltip={t("taskModes.maxItersTooltip")}
              >
                <InputNumber
                  style={{ width: "100%" }}
                  min={1}
                  step={1}
                  placeholder={t("taskModes.maxItersPlaceholder")}
                />
              </Form.Item>
            </Card>
          </Form>
        </div>
      </div>

      <div className={styles.footerActions}>
        <Button
          onClick={fetchConfig}
          disabled={saving}
          style={{ marginRight: 8 }}
        >
          {t("common.reset")}
        </Button>
        <Button type="primary" onClick={handleSave} loading={saving}>
          {t("common.save")}
        </Button>
      </div>
    </div>
  );
}
