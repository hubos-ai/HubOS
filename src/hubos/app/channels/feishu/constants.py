# -*- coding: utf-8 -*-
"""Feishu channel constants."""

# Token cache: refresh 1 min before expire
FEISHU_TOKEN_REFRESH_BEFORE_SECONDS = 60

# Max size for Feishu file upload (30MB)
FEISHU_FILE_MAX_BYTES = 30 * 1024 * 1024

# Feishu rich-text/card messages are limited to 30KB.  HubOS renders normal
# replies as post/rich-text, so use a safety margin for JSON/markdown overhead.
FEISHU_RICH_TEXT_SAFE_BYTES = 24 * 1024

# Keep generated long-reply files comfortably below Feishu's 30MB upload limit.
FEISHU_LONG_REPLY_FILE_CHUNK_BYTES = 25 * 1024 * 1024

# Dedup cache max size
FEISHU_PROCESSED_IDS_MAX = 1000

# Nickname cache max size (open_id -> name from Contact API)
FEISHU_NICKNAME_CACHE_MAX = 500

# Short suffix length for session_id (from chat_id or open_id)
FEISHU_SESSION_ID_SUFFIX_LEN = 8

# Timeout for Contact API when fetching user name by open_id (seconds)
FEISHU_USER_NAME_FETCH_TIMEOUT = 2

# Stale message threshold: drop Feishu retry deliveries older than this (ms)
FEISHU_STALE_MSG_THRESHOLD_MS = 20 * 1000

# WebSocket reconnection backoff settings
FEISHU_WS_INITIAL_RETRY_DELAY = 1.0  # seconds
FEISHU_WS_MAX_RETRY_DELAY = 60.0  # seconds
FEISHU_WS_BACKOFF_FACTOR = 2
