# SuperNova Staging 部署与域名切换验收记录（2026-11-21）

## 状态

- 结论：**域名迁移、公开入口、注册与登录验收通过**（用户于 2026-11-21 确认）。
- 环境：Staging；云服务器公网 IPv4 为 `117.72.196.94`；宝塔 Nginx + Docker Compose。
- 现用 Console：**https://bot.wet001.com**
- 现用 API：**https://api.wet001.com**；OpenAI 兼容 API Base URL 为 `https://api.wet001.com/v1`。
- 旧入口：`bot.ai-supernova.com`、`api.ai-supernova.com`；原域名因 ICP 备案/接入问题未能完成公网访问，暂不删除旧配置以便排查回退。
- 域名 DNS 在腾讯云 DNSPod 管理；两个新域名的 A 记录指向 `117.72.196.94`。

## 架构与转发

| 服务 | HTTPS 域名 | 服务器内部目标 |
|---|---|---|
| Console | `bot.wet001.com` | `127.0.0.1:18083` |
| API | `api.wet001.com` | `127.0.0.1:18082` |
| PostgreSQL / Redis / Knowledge Worker / Chat Import Worker | 无公网域名 | Docker 内部网络 |

现有宝塔 Nginx 承载 HTTPS；没有为上述两个应用端口开启公网访问。旧 Nginx 站点保留，不能随意覆盖服务器其他 vhost。

## 已执行并确认的检查

1. 首次 Staging 部署：`1b24426f7f81bf6c780ce2b5f0f1becc4203e00f` 为当时记录的成功部署 SHA；六个 Docker 容器曾全部显示 healthy（API、Console、两个 Worker、PostgreSQL、Redis）。
2. 公网 API：从 Windows 客户端访问 `https://api.wet001.com/health/ready` 得到 HTTP 200；响应为 `{"status":"ready","dependencies":{"postgres":"ok","redis":"ok"}}`。
3. DNS 和 TCP 443：新 API 域名解析到 `117.72.196.94`，公网 443 连接成功。
4. 用户确认 `https://bot.wet001.com` 可在浏览器打开，且已成功注册并登录。
5. 排查登录 `Failed to fetch` 时发现 Console 静态资源仍请求旧域名 `https://api.ai-supernova.com/auth/login`，属于构建期 API Base URL 未切换。
6. 服务器端 `.env` 中已设置 `SUPERNOVA_CORS_ORIGINS=https://bot.wet001.com`，但 API 容器起初仍保留旧值；之后在原 SHA 镜像下重建 API 容器，用户验证容器环境变量已更新为新域名，`/health/ready` 返回 ready。
7. GitHub Actions 的 `STAGING_API_BASE_URL` 应设为 `https://api.wet001.com`，Console 需重新构建；最终用户已确认新后台登录成功，说明这条登录路径在实际环境中已工作。**后续重新部署的具体 Git SHA 尚待读取服务器 `.last_successful_tag` 核实**。

## 部署时的坑与注意事项

- `deploy/docker/compose.staging.yaml` 中的镜像标签来自 `SUPERNOVA_IMAGE_TAG`，默认值是 `staging-latest`。直接执行 `docker compose up` 且未设置变量会尝试拉取不存在的 `api-staging-latest`。
- 手工重建 API 时，先检查 `cat /opt/supernova-staging/.last_successful_tag`，并设置 `export SUPERNOVA_IMAGE_TAG="$(cat .last_successful_tag)"`；使用 `--pull never` 避免误拉取。
- 修改服务器 `.env` 不会自动更改已创建容器的环境变量；要重新创建目标容器。
- Console 的 `VITE_API_BASE_URL` 在 Docker 镜像构建时固化，单改服务器 `.env` 不会改变前端。
- 旧域名和现有站点配置暂留，完成全链路验收后再有计划清理。不能执行 `docker compose down -v` 或影响其他站点的 Nginx 全局覆盖操作。

## 后续待完成

- 核实新版本 `.last_successful_tag`、GitHub Actions 部署记录、所有六个容器当前健康状态。
- 检查页面 Network 中所有请求均指向 `api.wet001.com`，并验收 CORS 预检。
- 继续验收 Persona 人格学习、FAQ 提取、知识审核与发布、RAG、微信聊天导入、API Key 和模型调用。
- 更新仓库中仍引用 `ai-supernova.com` 的模板、Dockerfile 默认值、CI 测试配置及旧部署文档，避免未来重新部署回退到旧域名。
- 品牌图标更换尚未实施：现有 `apps/console/public/logo.svg` 被 `apps/console/src/App.vue` 的登录页、侧栏和首页引用；favicon 应单独加入 `apps/console/public/logo.ico` 并在 `apps/console/index.html` 添加 `<link rel="icon" type="image/x-icon" href="/logo.ico">`。

> 本文记录用户已验收的事实和待核验事项，不构成 Phase 6 业务功能的全部通过证明。
