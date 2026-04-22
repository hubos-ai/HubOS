import {
  Drawer,
  Form,
  Input,
  InputNumber,
  Switch,
  Button,
  Select,
} from "@agentscope-ai/design";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { Alert, ConfigProvider, Spin } from "antd";
import { LinkOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { FormInstance } from "antd";
import { useCallback, useRef, useState } from "react";
import { getChannelLabel, type ChannelKey } from "./constants";
import styles from "../index.module.less";
import { useTheme } from "../../../../contexts/ThemeContext";
import { api } from "../../../../api";

const WECOM_SDK_URL =
  "https://wwcdn.weixin.qq.com/node/wework/js/wecom-aibot-sdk@0.1.0.min.js";

const WECOM_SOURCE = "hubos";

interface WecomBotInfo {
  botid: string;
  secret: string;
}

interface WecomAuthError {
  code: string;
  message: string;
  details?: unknown;
}

declare global {
  interface Window {
    WecomAIBotSDK?: {
      openBotInfoAuthWindow: (options: {
        source: string;
        onCreated?: (bot: WecomBotInfo) => void;
        onError?: (error: WecomAuthError) => void;
      }) => Promise<WecomBotInfo> | void;
    };
  }
}

const CHANNELS_WITH_ACCESS_CONTROL: ChannelKey[] = [
  "telegram",
  "dingtalk",
  "discord",
  "feishu",
  "wecom",
  "mattermost",
  "matrix",
  "weixin",
];

// Official platform developer documentation for each channel type.
// Both EN and ZH maps are kept so the button logic (which checks both) still works.
const CHANNEL_DOC_EN_URLS: Partial<Record<ChannelKey, string>> = {
  dingtalk: "https://open.dingtalk.com/document/orgapp/overview-of-development-process",
  feishu: "https://open.feishu.cn/document/home/develop-a-bot-in-3-minutes/create-an-app",
  imessage: "https://support.apple.com/guide/messages/welcome/mac",
  discord: "https://discord.com/developers/docs/getting-started",
  qq: "https://bot.q.qq.com/wiki/",
  telegram: "https://core.telegram.org/bots/tutorial",
  mqtt: "https://mqtt.org/getting-started/",
  mattermost: "https://developers.mattermost.com/integrate/reference/bot-accounts/",
  matrix: "https://spec.matrix.org/latest/client-server-api/",
  wecom: "https://developer.work.weixin.qq.com/document/path/90665",
  weixin: "https://github.com/lich0821/WeChatFerry",
  xiaoyi: "https://developer.huawei.com/consumer/cn/doc/service/openclaw-0000002518410344",
};

const CHANNEL_DOC_ZH_URLS: Partial<Record<ChannelKey, string>> = {
  dingtalk: "https://open.dingtalk.com/document/orgapp/overview-of-development-process",
  feishu: "https://open.feishu.cn/document/home/develop-a-bot-in-3-minutes/create-an-app",
  imessage: "https://support.apple.com/zh-cn/guide/messages/welcome/mac",
  discord: "https://discord.com/developers/docs/getting-started",
  qq: "https://bot.q.qq.com/wiki/",
  telegram: "https://core.telegram.org/bots/tutorial",
  mqtt: "https://mqtt.org/getting-started/",
  mattermost: "https://developers.mattermost.com/integrate/reference/bot-accounts/",
  matrix: "https://spec.matrix.org/latest/client-server-api/",
  wecom: "https://developer.work.weixin.qq.com/document/path/90665",
  weixin: "https://github.com/lich0821/WeChatFerry",
  xiaoyi: "https://developer.huawei.com/consumer/cn/doc/service/openclaw-0000002518410344",
};

const TWILIO_CONSOLE_URL = "https://console.twilio.com";

const BASE_FIELDS = [
  "enabled",
  "bot_prefix",
  "filter_tool_messages",
  "filter_thinking",
  "isBuiltin",
];

interface ChannelDrawerProps {
  open: boolean;
  activeKey: ChannelKey | null;
  activeLabel: string;
  form: FormInstance<Record<string, unknown>>;
  saving: boolean;
  rawValues: Record<string, unknown> | undefined;
  isBuiltin: boolean;
  onClose: () => void;
  onSubmit: (values: Record<string, unknown>) => void;
}

export function ChannelDrawer({
  open,
  activeKey,
  activeLabel,
  form,
  saving,
  rawValues,
  isBuiltin,
  onClose,
  onSubmit,
}: ChannelDrawerProps) {
  const { t, i18n } = useTranslation();
  const { isDark } = useTheme();
  const currentLang = i18n.language?.startsWith("zh") ? "zh" : "en";
  const label = activeKey ? getChannelLabel(activeKey, t) : activeLabel;
  const sdkLoadedRef = useRef(false);
  const { message } = useAppMessage();

  // WeChat QR code state
  const [weixinQrcodeImg, setWeixinQrcodeImg] = useState<string>("");
  const [weixinQrcodeLoading, setWeixinQrcodeLoading] = useState(false);
  const weixinPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const weixinConfirmedRef = useRef(false);

  const stopWeixinPoll = useCallback(() => {
    if (weixinPollRef.current) {
      clearInterval(weixinPollRef.current);
      weixinPollRef.current = null;
    }
  }, []);

  const handleFetchWeixinQrcode = useCallback(async () => {
    stopWeixinPoll();
    setWeixinQrcodeLoading(true);
    setWeixinQrcodeImg("");
    weixinConfirmedRef.current = false;
    try {
      const data = await api.getWeixinQrcode();
      if (data.qrcode_img) {
        setWeixinQrcodeImg(data.qrcode_img);
        // Start polling for scan confirmation
        weixinPollRef.current = setInterval(async () => {
          try {
            const s = await api.getWeixinQrcodeStatus(data.qrcode);
            if (s.status === "confirmed" && s.bot_token) {
              if (weixinConfirmedRef.current) return;
              weixinConfirmedRef.current = true;
              stopWeixinPoll();
              form.setFieldsValue({ bot_token: s.bot_token });
              setWeixinQrcodeImg("");
              message.success(t("channels.weixinLoginSuccess"));
            } else if (s.status === "expired") {
              stopWeixinPoll();
              setWeixinQrcodeImg("");
              message.warning(t("channels.weixinQrcodeExpired"));
            }
          } catch {
            // ignore poll errors
          }
        }, 2000);
      } else {
        message.error(t("channels.weixinQrcodeFailed"));
      }
    } catch {
      message.error(t("channels.weixinQrcodeFailed"));
    } finally {
      setWeixinQrcodeLoading(false);
    }
  }, [t, form, stopWeixinPoll]);

  // Dynamically load the WeCom SDK script
  const loadWecomSDK = useCallback((): Promise<void> => {
    return new Promise((resolve, reject) => {
      if (window.WecomAIBotSDK || sdkLoadedRef.current) {
        resolve();
        return;
      }
      const script = document.createElement("script");
      script.src = WECOM_SDK_URL;
      script.async = true;
      script.onload = () => {
        sdkLoadedRef.current = true;
        resolve();
      };
      script.onerror = () => reject(new Error("Failed to load WeCom SDK"));
      document.body.appendChild(script);
    });
  }, []);

  // Handle WeCom scan-to-authorize button click; source is fixed to WECOM_SOURCE
  const handleWecomAuth = useCallback(async () => {
    try {
      await loadWecomSDK();
    } catch {
      message.error(t("channels.wecomSdkLoadFailed"));
      return;
    }
    if (!window.WecomAIBotSDK) {
      message.error(t("channels.wecomSdkLoadFailed"));
      return;
    }
    const result = window.WecomAIBotSDK.openBotInfoAuthWindow({
      source: WECOM_SOURCE,
    });
    if (result && typeof result.then === "function") {
      result.then(
        (bot) => {
          if (bot?.botid) {
            form.setFieldsValue({ bot_id: bot.botid, secret: bot.secret });
            message.success(t("channels.wecomAuthSuccess"));
          }
        },
        (error: WecomAuthError) => {
          if (error?.code === "WINDOW_BLOCKED") {
            message.error(t("channels.wecomWindowBlocked"));
          } else if (error?.code === "CANCELLED") {
            message.info(t("channels.wecomCancelled"));
          } else {
            message.error(
              t("channels.wecomAuthFailed", {
                msg: error?.message || error?.code || "Unknown error",
              }),
            );
          }
        },
      );
    }
  }, [loadWecomSDK, form, t]);

  // ── Access control fields (shared across multiple channels) ──────────────

  const renderAccessControlFields = () => (
    <>
      <Form.Item
        name="dm_policy"
        label={t("channels.dmPolicy")}
        tooltip={t("channels.dmPolicyTooltip")}
      >
        <Select
          options={[
            { value: "open", label: t("channels.policyOpen") },
            { value: "allowlist", label: t("channels.policyAllowlist") },
          ]}
        />
      </Form.Item>
      <Form.Item
        name="group_policy"
        label={t("channels.groupPolicy")}
        tooltip={t("channels.groupPolicyTooltip")}
      >
        <Select
          options={[
            { value: "open", label: t("channels.policyOpen") },
            { value: "allowlist", label: t("channels.policyAllowlist") },
          ]}
        />
      </Form.Item>
      <Form.Item
        name="require_mention"
        label={t("channels.requireMention")}
        valuePropName="checked"
        tooltip={t("channels.requireMentionTooltip")}
      >
        <Switch />
      </Form.Item>
      <Form.Item
        name="allow_from"
        label={t("channels.allowFrom")}
        tooltip={t("channels.allowFromTooltip")}
      >
        <Select
          mode="tags"
          placeholder={t("channels.allowFromPlaceholder")}
          tokenSeparators={[","]}
        />
      </Form.Item>
    </>
  );

  // ── Builtin channel-specific fields ─────────────────────────────────────

  const renderBuiltinExtraFields = (key: ChannelKey) => {
    switch (key) {
      case "matrix":
        return (
          <>
            <Form.Item
              name="homeserver"
              label="Homeserver URL"
              rules={[{ required: true }]}
            >
              <Input placeholder="https://matrix.org" />
            </Form.Item>
            <Form.Item
              name="user_id"
              label="User ID"
              rules={[{ required: true }]}
            >
              <Input placeholder="@bot:matrix.org" />
            </Form.Item>
            <Form.Item
              name="access_token"
              label="Access Token"
              rules={[{ required: true }]}
            >
              <Input.Password placeholder="syt_..." />
            </Form.Item>
          </>
        );

      case "imessage":
        return (
          <>
            <Form.Item
              name="db_path"
              label="DB Path"
              rules={[{ required: true, message: "Please input DB path" }]}
            >
              <Input placeholder="~/Library/Messages/chat.db" />
            </Form.Item>
            <Form.Item
              name="poll_sec"
              label="Poll Interval (sec)"
              rules={[
                { required: true, message: "Please input poll interval" },
              ]}
            >
              <InputNumber min={0.1} step={0.1} style={{ width: "100%" }} />
            </Form.Item>
          </>
        );

      case "discord":
        return (
          <>
            <Form.Item
              name="bot_token"
              label="Bot Token"
              rules={[{ required: true }]}
            >
              <Input.Password placeholder="Discord bot token" />
            </Form.Item>
            <Form.Item name="http_proxy" label="HTTP Proxy">
              <Input placeholder="http://127.0.0.1:18118" />
            </Form.Item>
            <Form.Item name="http_proxy_auth" label="HTTP Proxy Auth">
              <Input placeholder="user:password" />
            </Form.Item>
            <Form.Item
              name="accept_bot_messages"
              label={t("channels.acceptBotMessages")}
              valuePropName="checked"
              tooltip={t("channels.acceptBotMessagesTooltip")}
            >
              <Switch />
            </Form.Item>
          </>
        );

      case "dingtalk":
        return (
          <>
            <Form.Item
              name="client_id"
              label="Client ID"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="client_secret"
              label="Client Secret"
              rules={[{ required: true }]}
            >
              <Input.Password />
            </Form.Item>
            <Form.Item
              name="message_type"
              label="Message Type"
              tooltip="markdown: regular messages; card: AI interactive card"
            >
              <Select
                options={[
                  { label: "markdown", value: "markdown" },
                  { label: "card", value: "card" },
                ]}
              />
            </Form.Item>
            <Form.Item
              noStyle
              shouldUpdate={(prev, cur) =>
                prev.message_type !== cur.message_type
              }
            >
              {({ getFieldValue }) => {
                if (getFieldValue("message_type") !== "card") return null;
                return (
                  <>
                    <Form.Item
                      name="card_template_id"
                      label="Card Template ID"
                      rules={[
                        {
                          required: true,
                          message:
                            "Please input card template id when message_type=card",
                        },
                      ]}
                    >
                      <Input placeholder="dt_card_template_xxx" />
                    </Form.Item>
                    <Form.Item
                      name="card_template_key"
                      label="Card Template Key"
                      tooltip="Must exactly match the template variable name"
                    >
                      <Input placeholder="content" />
                    </Form.Item>
                    <Form.Item
                      name="robot_code"
                      label="Robot Code"
                      tooltip="Recommended to configure explicitly for group chats"
                    >
                      <Input placeholder="robot code (default client_id)" />
                    </Form.Item>
                  </>
                );
              }}
            </Form.Item>
          </>
        );

      case "feishu":
        return (
          <>
            <Form.Item
              name="domain"
              label={t("channels.feishuRegion")}
              tooltip={t("channels.feishuRegionTooltip")}
            >
              <Select>
                <Select.Option value="feishu">
                  {t("channels.feishuChina")}
                </Select.Option>
                <Select.Option value="lark">
                  {t("channels.feishuInternational")}
                </Select.Option>
              </Select>
            </Form.Item>
            <Form.Item
              name="app_id"
              label="App ID"
              rules={[{ required: true }]}
            >
              <Input placeholder="cli_xxx" />
            </Form.Item>
            <Form.Item
              name="app_secret"
              label="App Secret"
              rules={[{ required: true }]}
            >
              <Input.Password placeholder="App Secret" />
            </Form.Item>
            <Form.Item name="encrypt_key" label="Encrypt Key">
              <Input placeholder="Optional, for event encryption" />
            </Form.Item>
            <Form.Item name="verification_token" label="Verification Token">
              <Input placeholder="Optional" />
            </Form.Item>
            <Form.Item name="media_dir" label={t("channels.weixinMediaDir")}>
              <Input placeholder="~/.hubos/media" />
            </Form.Item>
          </>
        );

      case "qq":
        return (
          <>
            <Form.Item
              name="app_id"
              label="App ID"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="client_secret"
              label="Client Secret"
              rules={[{ required: true }]}
            >
              <Input.Password />
            </Form.Item>
          </>
        );

      case "telegram":
        return (
          <>
            <Form.Item
              name="bot_token"
              label="Bot Token"
              rules={[{ required: true }]}
            >
              <Input.Password placeholder="Telegram bot token from BotFather" />
            </Form.Item>
            <Form.Item name="http_proxy" label="HTTP Proxy">
              <Input placeholder="http://127.0.0.1:18118" />
            </Form.Item>
            <Form.Item name="http_proxy_auth" label="HTTP Proxy Auth">
              <Input placeholder="user:password" />
            </Form.Item>
            <Form.Item
              name="show_typing"
              label="Show Typing"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </>
        );

      case "mqtt":
        return (
          <>
            <Form.Item
              name="host"
              label="MQTT Host"
              rules={[{ required: true }]}
            >
              <Input placeholder="127.0.0.1" />
            </Form.Item>
            <Form.Item
              name="port"
              label="MQTT Port"
              rules={[
                { required: true },
                {
                  type: "number",
                  min: 1,
                  max: 65535,
                  message: "Port must be between 1 and 65535",
                },
              ]}
            >
              <InputNumber
                min={1}
                max={65535}
                style={{ width: "100%" }}
                placeholder="1883"
              />
            </Form.Item>
            <Form.Item
              name="transport"
              label="Transport"
              rules={[{ required: true }]}
            >
              <Select>
                <Select.Option value="tcp">MQTT (tcp)</Select.Option>
                <Select.Option value="websockets">
                  WS (websockets)
                </Select.Option>
              </Select>
            </Form.Item>
            <Form.Item
              name="clean_session"
              label="Clean Session"
              valuePropName="checked"
            >
              <Switch defaultChecked />
            </Form.Item>
            <Form.Item
              name="qos"
              label="QoS"
              rules={[{ required: true }]}
            >
              <Select>
                <Select.Option value="0">At Most Once (0)</Select.Option>
                <Select.Option value="1">At Least Once (1)</Select.Option>
                <Select.Option value="2">Exactly Once (2)</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item name="username" label="MQTT Username">
              <Input placeholder="Leave blank to disable / not use" />
            </Form.Item>
            <Form.Item name="password" label="MQTT Password">
              <Input.Password placeholder="Leave blank to disable / not use" />
            </Form.Item>
            <Form.Item
              name="subscribe_topic"
              label="Subscribe Topic"
              rules={[{ required: true }]}
            >
              <Input placeholder="server/+/up" />
            </Form.Item>
            <Form.Item
              name="publish_topic"
              label="Publish Topic"
              rules={[{ required: true }]}
            >
              <Input placeholder="client/{client_id}/down" />
            </Form.Item>
            <Form.Item
              name="tls_enabled"
              label="TLS Enabled"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item name="tls_ca_certs" label="TLS CA Certs">
              <Input placeholder="Path to CA certificates file" />
            </Form.Item>
            <Form.Item name="tls_certfile" label="TLS Certfile">
              <Input placeholder="Path to client certificate file" />
            </Form.Item>
            <Form.Item name="tls_keyfile" label="TLS Keyfile">
              <Input placeholder="Path to client private key file" />
            </Form.Item>
          </>
        );

      case "mattermost":
        return (
          <>
            <Form.Item
              name="url"
              label="Mattermost URL"
              rules={[{ required: true }]}
            >
              <Input placeholder="https://mattermost.example.com" />
            </Form.Item>
            <Form.Item
              name="bot_token"
              label="Bot Token"
              rules={[{ required: true }]}
            >
              <Input.Password placeholder="Mattermost bot token" />
            </Form.Item>
            <Form.Item name="media_dir" label={t("channels.weixinMediaDir")}>
              <Input placeholder="~/.hubos/media/mattermost" />
            </Form.Item>
            <Form.Item
              name="show_typing"
              label="Show Typing"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="thread_follow_without_mention"
              label="Thread Follow Without Mention"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </>
        );

      case "voice":
        return (
          <>
            <ConfigProvider prefixCls="ant">
              <Alert
                type="info"
                showIcon
                message={t("channels.voiceSetupGuide")}
                style={{ marginBottom: 16 }}
              />
            </ConfigProvider>
            <Form.Item
              name="twilio_account_sid"
              label={t("channels.twilioAccountSid")}
              rules={[{ required: true }]}
            >
              <Input placeholder="ACxxxxxxxx" />
            </Form.Item>
            <Form.Item
              name="twilio_auth_token"
              label={t("channels.twilioAuthToken")}
              rules={[{ required: true }]}
            >
              <Input.Password />
            </Form.Item>
            <Form.Item name="phone_number" label={t("channels.phoneNumber")}>
              <Input placeholder="+15551234567" />
            </Form.Item>
            <Form.Item
              name="phone_number_sid"
              label={t("channels.phoneNumberSid")}
              tooltip={t("channels.phoneNumberSidHelp")}
            >
              <Input placeholder="PNxxxxxxxx" />
            </Form.Item>
            <Form.Item name="tts_provider" label={t("channels.ttsProvider")}>
              <Input placeholder="google" />
            </Form.Item>
            <Form.Item name="tts_voice" label={t("channels.ttsVoice")}>
              <Input placeholder="en-US-Journey-D" />
            </Form.Item>
            <Form.Item name="stt_provider" label={t("channels.sttProvider")}>
              <Input placeholder="deepgram" />
            </Form.Item>
            <Form.Item name="language" label={t("channels.language")}>
              <Input placeholder="en-US" />
            </Form.Item>
            <Form.Item
              name="welcome_greeting"
              label={t("channels.welcomeGreeting")}
            >
              <Input.TextArea rows={2} />
            </Form.Item>
          </>
        );

      case "wecom":
        return (
          <>
            <Form.Item label=" " colon={false}>
              <span
                style={{
                  display: "block",
                  marginBottom: 8,
                  fontSize: 13,
                  color: isDark ? "rgba(255,255,255,0.65)" : "rgba(0,0,0,0.45)",
                }}
              >
                {t("channels.wecomAuthHint")}
              </span>
              <Button type="primary" block onClick={handleWecomAuth}>
                {t("channels.loginWeCom")}
              </Button>
            </Form.Item>
            <Form.Item
              name="bot_id"
              label="Bot ID"
              rules={[{ required: true, message: "Please input Bot ID" }]}
            >
              <Input placeholder="Bot ID from WeCom backend" />
            </Form.Item>
            <Form.Item
              name="secret"
              label="Secret"
              rules={[{ required: true, message: "Please input Secret" }]}
            >
              <Input.Password placeholder="Secret from WeCom backend" />
            </Form.Item>
            <Form.Item name="media_dir" label={t("channels.weixinMediaDir")}>
              <Input placeholder="~/.hubos/media" />
            </Form.Item>
            <Form.Item
              name="welcome_text"
              label={t("channels.welcomeText")}
              tooltip={t("channels.welcomeTextTooltip")}
            >
              <Input placeholder={t("channels.welcomeTextPlaceholder")} />
            </Form.Item>
          </>
        );

      case "xiaoyi":
        return (
          <>
            <ConfigProvider prefixCls="ant">
              <Alert
                type="info"
                showIcon
                message={t("channels.xiaoyiSetupGuide")}
                style={{ marginBottom: 16 }}
              />
            </ConfigProvider>
            <Form.Item
              name="ak"
              label="Access Key (AK)"
              rules={[{ required: true, message: "Please input Access Key" }]}
            >
              <Input placeholder="Access Key from Huawei Developer Platform" />
            </Form.Item>
            <Form.Item
              name="sk"
              label="Secret Key (SK)"
              rules={[{ required: true, message: "Please input Secret Key" }]}
            >
              <Input.Password placeholder="Secret Key from Huawei Developer Platform" />
            </Form.Item>
            <Form.Item
              name="agent_id"
              label="Agent ID"
              rules={[{ required: true, message: "Please input Agent ID" }]}
            >
              <Input placeholder="Agent ID from XiaoYi platform" />
            </Form.Item>
            <Form.Item name="ws_url" label="WebSocket URL">
              <Input placeholder="wss://hag.cloud.huawei.com/openclaw/v1/ws/link" />
            </Form.Item>
          </>
        );

      case "weixin":
        return (
          <>
            <ConfigProvider prefixCls="ant">
              <Alert
                type="info"
                showIcon
                message={t("channels.weixinSetupGuide")}
                style={{ marginBottom: 16 }}
              />
            </ConfigProvider>
            <Form.Item label={t("channels.weixinScanLogin")}>
              <Button
                type="primary"
                block
                loading={weixinQrcodeLoading}
                onClick={handleFetchWeixinQrcode}
              >
                {t("channels.weixinGetQrcode")}
              </Button>
              {weixinQrcodeLoading && (
                <div style={{ textAlign: "center", marginTop: 12 }}>
                  <Spin />
                </div>
              )}
              {weixinQrcodeImg && !weixinQrcodeLoading && (
                <div style={{ textAlign: "center", marginTop: 12 }}>
                  <img
                    src={
                      weixinQrcodeImg.startsWith("http")
                        ? weixinQrcodeImg
                        : `data:image/png;base64,${weixinQrcodeImg}`
                    }
                    alt="WeChat QR Code"
                    style={{ width: 200, height: 200 }}
                  />
                  <div
                    style={{
                      marginTop: 8,
                      fontSize: 12,
                      color: isDark
                        ? "rgba(255,255,255,0.45)"
                        : "rgba(0,0,0,0.45)",
                    }}
                  >
                    {t("channels.weixinScanHint")}
                  </div>
                </div>
              )}
            </Form.Item>
            <Form.Item
              name="bot_token"
              label={t("channels.weixinBotToken")}
              tooltip={t("channels.weixinBotTokenTooltip")}
            >
              <Input.Password
                placeholder={t("channels.weixinBotTokenPlaceholder")}
              />
            </Form.Item>
            <Form.Item
              name="bot_token_file"
              label={t("channels.weixinBotTokenFile")}
              tooltip={t("channels.weixinBotTokenFileTooltip")}
            >
              <Input placeholder="~/.hubos/weixin_bot_token" />
            </Form.Item>
            <Form.Item name="media_dir" label={t("channels.weixinMediaDir")}>
              <Input placeholder="~/.hubos/media" />
            </Form.Item>
          </>
        );

      default:
        return null;
    }
  };

  // ── Custom channel fields (key-value editor) ─────────────────────────────

  const renderCustomExtraFields = (
    values: Record<string, unknown> | undefined,
  ) => {
    if (!values) return null;
    const extraKeys = Object.keys(values).filter(
      (k) => !BASE_FIELDS.includes(k),
    );
    if (extraKeys.length === 0) return null;

    return (
      <>
        <div style={{ marginBottom: 8, fontWeight: 500 }}>Custom Fields</div>
        {extraKeys.map((fieldKey) => {
          const value = values[fieldKey];
          return (
            <Form.Item key={fieldKey} name={fieldKey} label={fieldKey}>
              {typeof value === "boolean" ? (
                <Switch />
              ) : typeof value === "number" ? (
                <InputNumber style={{ width: "100%" }} />
              ) : (
                <Input />
              )}
            </Form.Item>
          );
        })}
      </>
    );
  };

  // ── Drawer title ─────────────────────────────────────────────────────────

  const drawerTitle = (
    <div className={styles.drawerTitle}>
      <span>
        {label
          ? `${label} ${t("channels.settings")}`
          : t("channels.channelSettings")}
      </span>
      {activeKey &&
        CHANNEL_DOC_EN_URLS[activeKey] &&
        CHANNEL_DOC_ZH_URLS[activeKey] && (
          <Button
            type="text"
            size="small"
            icon={<LinkOutlined />}
            onClick={() => {
              const finalUrl =
                currentLang === "zh"
                  ? (CHANNEL_DOC_ZH_URLS[activeKey] ?? CHANNEL_DOC_EN_URLS[activeKey]!)
                  : (CHANNEL_DOC_EN_URLS[activeKey] ?? CHANNEL_DOC_ZH_URLS[activeKey]!);
              window.open(finalUrl, "_blank", "noopener,noreferrer");
            }}
            className={styles.dingtalkDocBtn}
            style={{ color: "#FF7F16" }}
          >
            {label} Doc
          </Button>
        )}
      {activeKey === "voice" && (
        <Button
          type="text"
          size="small"
          icon={<LinkOutlined />}
          onClick={() =>
            window.open(TWILIO_CONSOLE_URL, "_blank", "noopener,noreferrer")
          }
          className={styles.dingtalkDocBtn}
          style={{ color: "#FF7F16" }}
        >
          {t("channels.voiceSetupLink")}
        </Button>
      )}
    </div>
  );

  // ── Render ───────────────────────────────────────────────────────────────

  const drawerFooter = (
    <div className={styles.formActions}>
      <Button onClick={onClose}>{t("common.cancel")}</Button>
      <Button type="primary" loading={saving} onClick={() => form.submit()}>
        {t("common.save")}
      </Button>
    </div>
  );

  return (
    <Drawer
      width={420}
      placement="right"
      title={drawerTitle}
      open={open}
      onClose={onClose}
      destroyOnClose
      footer={drawerFooter}
    >
      {activeKey && (
        <Form
          form={form}
          layout="vertical"
          onFinish={onSubmit}
        >
          <Form.Item
            name="enabled"
            label={t("common.enabled")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>

          {activeKey !== "voice" && (
            <Form.Item name="bot_prefix" label="Bot Prefix">
              <Input placeholder="@bot" />
            </Form.Item>
          )}

          {activeKey !== "console" && (
            <>
              <Form.Item
                name="filter_tool_messages"
                label={t("channels.filterToolMessages")}
                valuePropName="checked"
                tooltip={t("channels.filterToolMessagesTooltip")}
              >
                <Switch />
              </Form.Item>
              <Form.Item
                name="filter_thinking"
                label={t("channels.filterThinking")}
                valuePropName="checked"
                tooltip={t("channels.filterThinkingTooltip")}
              >
                <Switch />
              </Form.Item>
            </>
          )}

          {isBuiltin
            ? renderBuiltinExtraFields(activeKey)
            : renderCustomExtraFields(rawValues)}

          {CHANNELS_WITH_ACCESS_CONTROL.includes(activeKey) &&
            renderAccessControlFields()}
        </Form>
      )}
    </Drawer>
  );
}
