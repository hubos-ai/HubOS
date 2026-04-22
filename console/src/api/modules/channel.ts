/**
 * Channel module — HubOS console channels API against XClaw backend
 *
 * All API calls forwarded to channelAdapter which handles:
 * - /config/channels/* path prefix → /api/channels/*
 * - XClaw {service_running, channels: {...}} → flat ChannelConfig
 * - XClaw {success, message} PUT response → SingleChannelConfig
 */

import { channelAdapter } from "../adapters/channel";

export const channelApi = channelAdapter;
