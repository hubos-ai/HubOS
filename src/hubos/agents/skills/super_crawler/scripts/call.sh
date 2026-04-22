#!/usr/bin/env bash
# super_crawler/scripts/call.sh
# 代理调用 super-crawler 的 openclaw-tools.js
#
# 用法:
#   ./call.sh <tool> '<json_params>'
#   ./call.sh list
#
# 示例:
#   ./call.sh hunter_domain '{"domain":"example.com"}'
#   ./call.sh web_crawl '{"url":"https://example.com","depth":2,"spa":true}'
#   ./call.sh hunter_verify '{"email":"ceo@example.com"}'

SUPER_CRAWLER_DIR="$HOME/projects/super-crawler"
TOOL_SCRIPT="$SUPER_CRAWLER_DIR/src/openclaw-tools.js"

if [ ! -f "$TOOL_SCRIPT" ]; then
  echo '{"success":false,"error":"super-crawler not found at '"$TOOL_SCRIPT"'"}'
  exit 1
fi

if [ $# -eq 0 ] || [ "$1" = "list" ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
  node "$TOOL_SCRIPT" tools_list
  exit 0
fi

TOOL="$1"
PARAMS="${2:-{\}}"

node "$TOOL_SCRIPT" call "$TOOL" "$PARAMS"
