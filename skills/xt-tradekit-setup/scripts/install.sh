#!/bin/bash
# XT TradeKit MCP server bootstrap install: clone/update the repo into ~/.xt-tradekit/app and run the repo's install.sh
set -e

REPO="${XT_TRADEKIT_REPO:-XtApis/AItradekit}"
APP_DIR="${XT_TRADEKIT_HOME:-$HOME/.xt-tradekit/app}"

command -v git >/dev/null 2>&1 || { echo "❌ git is required"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ python3 (>=3.10) is required"; exit 1; }

if [ -d "$APP_DIR/.git" ]; then
  echo "🔄 Updating existing installation: $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  echo "📥 Cloning https://github.com/$REPO → $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --depth 1 "https://github.com/$REPO.git" "$APP_DIR"
fi

bash "$APP_DIR/install.sh"
