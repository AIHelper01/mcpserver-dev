# MCP 容器一键起停。所有目标都是 mcp_ctl.py 的快捷入口。
# Windows 没有 make 时用：python mcp_ctl.py <target>

PY  ?= python3
CTL  = $(PY) mcp_ctl.py
SVC ?=

.DEFAULT_GOAL := help
.PHONY: help up build down restart status ps logs check doctor

help:              ## 显示可用目标
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-9s %s\n", $$1, $$2}'

up:                ## 构建并启动全部服务（up -d --build）
	@$(CTL) up

build:             ## 只构建镜像，可指定服务：make build SVC=mcp-demo
	@$(CTL) build $(SVC)

down:              ## 停止并删除容器（镜像与数据保留）
	@$(CTL) down

restart:           ## 重启全部或单个服务：make restart SVC=mcp-weather
	@$(CTL) restart $(SVC)

status:            ## 容器状态 + healthcheck 结果
	@$(CTL) status

ps:                ## docker compose ps 原始输出
	@$(CTL) ps

logs:              ## 跟随日志：make logs SVC=mcp-weather
	@$(CTL) logs $(SVC)

check:             ## 端到端体检：/health /mcp 工具调用 /sse
	@$(CTL) check

doctor:            ## 前置检查：docker、compose、.env、端口
	@$(CTL) doctor

