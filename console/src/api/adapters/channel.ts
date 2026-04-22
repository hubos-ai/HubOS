/**
 * Channel adapter — calls native HubOS backend directly.
 *
 * Native routes (under `/api/config`):
 * - GET  /api/config/channels                                   → ChannelConfig (flat map)
 * - PUT  /api/config/channels                                   → ChannelConfig
 * - GET  /api/config/channels/types                             → string[]
 * - GET  /api/config/channels/{name}                            → SingleChannelConfig
 * - PUT  /api/config/channels/{name}                            → SingleChannelConfig
 * - GET  /api/config/channels/weixin/qrcode                     → {qrcode_img, qrcode}
 * - GET  /api/config/channels/weixin/qrcode/status?qrcode=...   → status payload
 */

import { request } from "../request";
import type { ChannelConfig, SingleChannelConfig } from "../types";

export const channelAdapter = {
  listChannelTypes: () => request<string[]>("/config/channels/types"),

  listChannels: () => request<ChannelConfig>("/config/channels"),

  updateChannels: (body: ChannelConfig) =>
    request<ChannelConfig>("/config/channels", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  getChannelConfig: (channelName: string) =>
    request<SingleChannelConfig>(
      `/config/channels/${encodeURIComponent(channelName)}`,
    ),

  updateChannelConfig: (channelName: string, body: SingleChannelConfig) =>
    request<SingleChannelConfig>(
      `/config/channels/${encodeURIComponent(channelName)}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    ),

  getWeixinQrcode: () =>
    request<{ qrcode_img: string; qrcode: string }>(
      "/config/channels/weixin/qrcode",
    ),

  getWeixinQrcodeStatus: (qrcode: string) =>
    request<{ status: string; bot_token: string; base_url: string }>(
      `/config/channels/weixin/qrcode/status?qrcode=${encodeURIComponent(qrcode)}`,
    ),
};
