import { useEffect, useState } from "react";
import {
  Modal,
  Form,
  Input,
  Button,
  Space,
  Typography,
  Empty,
  Spin,
  Select,
  Divider,
  message as antMessage,
} from "antd";
import { CheckOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { AgentSummary } from "@/api/types/agents";
import type { ProviderInfo } from "@/api/types/provider";
import { getAgentDisplayName } from "@/utils/agentDisplayName";
import type { PoolSkillSpec } from "@/api/types/skill";
import { skillApi } from "@/api/modules/skill";
import { api } from "@/api";
import styles from "../index.module.less";

const { Text } = Typography;

interface AgentModalProps {
  open: boolean;
  editingAgent: AgentSummary | null;
  form: ReturnType<typeof Form.useForm>[0];
  selectedSkills: string[];
  onSelectedSkillsChange: (skills: string[]) => void;
  onInstalledSkillsLoaded: (skills: string[]) => void;
  onSave: () => Promise<void>;
  onCancel: () => void;
}

export function AgentModal({
  open,
  editingAgent,
  form,
  selectedSkills,
  onSelectedSkillsChange,
  onInstalledSkillsLoaded,
  onSave,
  onCancel,
}: AgentModalProps) {
  const { t } = useTranslation();
  const [poolSkills, setPoolSkills] = useState<PoolSkillSpec[]>([]);
  const [installedSkills, setInstalledSkills] = useState<string[]>([]);
  const [loadingSkills, setLoadingSkills] = useState(false);

  // ── Model selector state ────────────────────────────────────────────
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [followGlobal, setFollowGlobal] = useState(true);

  // Load skills + model data when modal opens
  useEffect(() => {
    if (!open) return;

    // Skills loading
    setLoadingSkills(true);
    const fetchPool = skillApi.listSkillPoolSkills();
    const fetchInstalled = editingAgent
      ? skillApi
          .listSkills(editingAgent.id)
          .then((skills) => skills.map((s) => s.name))
      : Promise.resolve([]);

    Promise.all([fetchPool, fetchInstalled])
      .then(([pool, installed]) => {
        setPoolSkills(pool);
        setInstalledSkills(installed);
        onInstalledSkillsLoaded(installed);
        if (editingAgent) {
          onSelectedSkillsChange(installed);
        } else {
          onSelectedSkillsChange([]);
        }
      })
      .finally(() => setLoadingSkills(false));

    // Model loading
    setLoadingModels(true);
    const modelPromises: Promise<void>[] = [
      api.listProviders().then((provList) => {
        setProviders(provList);
      }),
    ];

    if (editingAgent) {
      modelPromises.push(
        api
          .getActiveModels({ scope: "agent", agent_id: editingAgent.id })
          .then((activeInfo) => {
            if (activeInfo.active_llm) {
              setSelectedProviderId(activeInfo.active_llm.provider_id);
              setSelectedModel(activeInfo.active_llm.model);
              setFollowGlobal(false);
            } else {
              setSelectedProviderId(null);
              setSelectedModel(null);
              setFollowGlobal(true);
            }
          })
          .catch(() => {
            setSelectedProviderId(null);
            setSelectedModel(null);
            setFollowGlobal(true);
          }),
      );
    } else {
      setSelectedProviderId(null);
      setSelectedModel(null);
      setFollowGlobal(true);
    }

    Promise.all(modelPromises).finally(() => setLoadingModels(false));
  }, [open, editingAgent?.id]);

  // Derived: available models for selected provider
  const availableModels = (() => {
    if (!selectedProviderId) return [];
    const prov = providers.find((p) => p.id === selectedProviderId);
    if (!prov) return [];
    return [...prov.models, ...prov.extra_models];
  })();

  const handleSaveWithModel = async () => {
    // First call the parent save handler (agent CRUD + skills)
    await onSave();

    // Then save model if editing and a provider is selected
    if (editingAgent && !followGlobal && selectedProviderId && selectedModel) {
      try {
        await api.setActiveLlm({
          provider_id: selectedProviderId,
          model: selectedModel,
          scope: "agent",
          agent_id: editingAgent.id,
        });
      } catch (err) {
        const errMsg =
          err instanceof Error
            ? err.message
            : t("taskModes.agentModelSaveFailed");
        antMessage.error(errMsg);
      }
    }
  };

  const toggleSkill = (name: string) => {
    const isInstalled = editingAgent && installedSkills.includes(name);
    if (isInstalled) return;

    if (selectedSkills.includes(name)) {
      onSelectedSkillsChange(selectedSkills.filter((s) => s !== name));
    } else {
      onSelectedSkillsChange([...selectedSkills, name]);
    }
  };

  const handleSelectAll = () => {
    const allNames = poolSkills.map((s) => s.name);
    onSelectedSkillsChange(
      Array.from(new Set([...installedSkills, ...allNames])),
    );
  };

  const handleSelectBuiltin = () => {
    const builtinNames = poolSkills
      .filter((s) => s.source === "builtin")
      .map((s) => s.name);
    onSelectedSkillsChange(
      Array.from(new Set([...installedSkills, ...builtinNames])),
    );
  };

  const handleSelectNone = () => {
    onSelectedSkillsChange(editingAgent ? [...installedSkills] : []);
  };

  return (
    <Modal
      title={
        editingAgent
          ? t("agent.editTitle", { name: getAgentDisplayName(editingAgent, t) })
          : t("agent.createTitle")
      }
      open={open}
      onCancel={onCancel}
      width={640}
      footer={null}
      destroyOnClose
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="name"
          label={t("agent.nameLabel")}
          rules={[{ required: true, message: t("agent.nameRequired") }]}
        >
          <Input placeholder={t("agent.namePlaceholder")} />
        </Form.Item>

        <Form.Item name="description" label={t("agent.descriptionLabel")}>
          <Input.TextArea
            rows={2}
            placeholder={t("agent.descriptionPlaceholder")}
          />
        </Form.Item>

        <Form.Item
          name="workspace_dir"
          label={t("agent.workspaceLabel")}
          tooltip={t("agent.workspaceTooltip")}
        >
          <Input placeholder={t("agent.workspacePlaceholder")} />
        </Form.Item>
      </Form>

      {/* ── Default Model Section ─────────────────────────────────────── */}
      {editingAgent && (
        <>
          <Divider orientation="left" style={{ marginTop: 8, marginBottom: 16 }}>
            {t("taskModes.agentModelTitle")}
          </Divider>

          {loadingModels ? (
            <div style={{ textAlign: "center", padding: "16px 0" }}>
              <Spin size="small" />
            </div>
          ) : (
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, display: "flex", gap: 8 }}>
                <Button
                  type={followGlobal ? "primary" : "default"}
                  size="small"
                  onClick={() => {
                    setFollowGlobal(true);
                    setSelectedProviderId(null);
                    setSelectedModel(null);
                  }}
                >
                  {t("taskModes.agentModelFollowGlobal")}
                </Button>
                <Button
                  type={!followGlobal ? "primary" : "default"}
                  size="small"
                  onClick={() => setFollowGlobal(false)}
                >
                  {t("taskModes.agentModelTitle")}
                </Button>
              </div>

              {!followGlobal && (
                <div style={{ display: "flex", gap: 12 }}>
                  <Select
                    style={{ flex: 1 }}
                    placeholder={t("taskModes.agentModelProviderPlaceholder")}
                    value={selectedProviderId}
                    onChange={(val) => {
                      setSelectedProviderId(val);
                      setSelectedModel(null);
                    }}
                    options={providers.map((p) => ({
                      label: p.name,
                      value: p.id,
                    }))}
                    showSearch
                    optionFilterProp="label"
                  />
                  <Select
                    style={{ flex: 1 }}
                    placeholder={t("taskModes.agentModelModelPlaceholder")}
                    value={selectedModel}
                    onChange={setSelectedModel}
                    disabled={!selectedProviderId}
                    options={availableModels.map((m) => ({
                      label: m.name || m.id,
                      value: m.id,
                    }))}
                    showSearch
                    optionFilterProp="label"
                    notFoundContent={
                      selectedProviderId
                        ? t("agent.noModels")
                        : t("taskModes.agentModelProviderPlaceholder")
                    }
                  />
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* ── Skills Section ─────────────────────────────────────────────── */}
      <Divider orientation="left" style={{ marginTop: 8, marginBottom: 16 }}>
        {t("agent.skillsLabel")}
      </Divider>

      <Space style={{ marginBottom: 8 }}>
        <Button size="small" onClick={handleSelectAll}>
          {t("agent.selectAll")}
        </Button>
        <Button size="small" onClick={handleSelectBuiltin}>
          {t("agent.selectBuiltin")}
        </Button>
        <Button size="small" onClick={handleSelectNone}>
          {t("agent.selectNone")}
        </Button>
      </Space>

      {loadingSkills ? (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <Spin />
        </div>
      ) : poolSkills.length === 0 ? (
        <Empty description={t("agent.noPoolSkills")} />
      ) : (
        <div className={styles.skillGrid}>
          {poolSkills.map((skill) => {
            const isInstalled =
              !!editingAgent && installedSkills.includes(skill.name);
            const isSelected = selectedSkills.includes(skill.name);
            return (
              <Button
                key={skill.name}
                size="small"
                type={isSelected || isInstalled ? "primary" : "default"}
                ghost={isSelected && !isInstalled}
                disabled={isInstalled}
                onClick={() => toggleSkill(skill.name)}
                icon={
                  isSelected || isInstalled ? <CheckOutlined /> : undefined
                }
                className={styles.skillTag}
              >
                <Text
                  ellipsis
                  style={{ maxWidth: 100 }}
                  disabled={isInstalled}
                >
                  {skill.name}
                </Text>
              </Button>
            );
          })}
        </div>
      )}

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <div style={{ textAlign: "right", marginTop: 24 }}>
        <Button onClick={onCancel} style={{ marginRight: 8 }}>
          {t("common.cancel")}
        </Button>
        <Button type="primary" onClick={handleSaveWithModel}>
          {editingAgent ? t("common.save") : t("common.create")}
        </Button>
      </div>
    </Modal>
  );
}
