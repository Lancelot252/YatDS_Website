# YatDS · GitHub Actions 触发服务器部署

适用于 **Linux + systemd + Nginx + HTTPS**。YatDS 是静态网站，服务器端需要 Python 3 和 Git，无需安装前端构建工具。

```text
推送或合并到 main
  → GitHub Actions 通过 HTTPS 向 /_deploy/yatds 发 POST
  → 服务器验证 Bearer token，从固定仓库拉取 main
  → 导出该提交的 site/，检查 index.html
  → 原子切换 current 链接，Nginx 开始提供新版
```

工作流收到 HTTP 200 才算成功，日志会显示实际发布的 Git 提交。快速连续推送时，服务器发布接到请求时能拉取到的最新 main。一次部署失败不会切换线上目录。

## 1. 建一个前端 GitHub 仓库

本仓库为 `Lancelot252/YatDS_Website`，已包含静态网页（`site/`）、工作流（`.github/workflows/deploy.yml`）和服务器配置模板（`server/`）。仓库为 Private，网站是否公开由 Nginx 决定。修改网页后，将 `site/` 下的文件提交并推送到 `main`。

第 6 教学周课件约 51 MiB。用 Git 命令推送时，GitHub 会提示超过 50 MiB，普通 Git 仍接受这份文件。网页上传限制为 25 MiB，普通 Git 的单文件上限为 100 MiB。[GitHub 大文件限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

## 2. 让服务器能拉取仓库

在 Linux 服务器创建专用部署用户。下面的用户、端口和路径与随包配置对应。

```sh
sudo useradd --system --user-group --create-home --home-dir /srv/yatds --shell /usr/sbin/nologin yatds-deploy
sudo chmod 755 /srv/yatds
sudo install -d -m 700 -o yatds-deploy -g yatds-deploy /srv/yatds/.ssh
sudo -H -u yatds-deploy ssh-keygen -t ed25519 -N '' -f /srv/yatds/.ssh/id_ed25519
sudo cat /srv/yatds/.ssh/id_ed25519.pub
```

把输出的 **公钥** 加到 GitHub 仓库的 **Settings → Deploy keys → Add deploy key**。保持 **Allow write access** 未勾选。私钥留在服务器。[GitHub Deploy Keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)

从 GitHub 官方文档复制以 `github.com ssh-ed25519` 开头的完整主机公钥行，保存到服务器的 `/srv/yatds/.ssh/known_hosts`。确认文件归部署用户所有，再克隆私有仓库。[GitHub SSH 主机公钥](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)

```sh
sudo chown yatds-deploy:yatds-deploy /srv/yatds/.ssh/known_hosts
sudo chmod 600 /srv/yatds/.ssh/known_hosts
sudo -H -u yatds-deploy git clone --bare --single-branch --branch main \
  git@github.com:Lancelot252/YatDS_Website.git /srv/yatds/repo.git
sudo chmod 700 /srv/yatds/repo.git
```

## 3. 启动部署接收器并配置 Nginx

将模板包中的 `server/` 上传到服务器临时目录，从包含 `server/` 的目录执行。

```sh
sudo install -D -m 644 server/deploy_receiver.py /usr/local/lib/yatds/deploy_receiver.py
sudo install -m 644 server/yatds-deploy.service /etc/systemd/system/yatds-deploy.service
openssl rand -hex 32
```

把刚生成的值保存到服务器的 `/etc/yatds-deploy.env`，格式为一行 `DEPLOY_TOKEN=你的64位十六进制值`，文件由 root 持有且仅 root 可读。稍后 GitHub 用同一个值。密钥不应写入 Git 仓库。

```sh
sudo chown root:root /etc/yatds-deploy.env
sudo chmod 600 /etc/yatds-deploy.env
sudo systemctl daemon-reload
sudo systemctl enable --now yatds-deploy
sudo systemctl status yatds-deploy
```

把 `server/nginx-yatds.conf` 的配置合并到该域名现有的 **HTTPS** `server { ... }` 内。替换该站点原有的 `root` 或 `location /` 配置时，先确认该域名只用于 YatDS。TLS 证书沿用已有证书。服务仅监听本机 `127.0.0.1:8788`，GitHub 只访问 HTTPS 443 端口。[Nginx 反向代理配置](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

```sh
sudo nginx -t
sudo systemctl reload nginx
```

GitHub 托管执行器必须能访问该域名及路径。如果只有内网能访问部署端点，需要改用能访问该端点的 self-hosted runner，并把工作流的 `runs-on` 换成该 runner 的标签。

## 4. 配置 GitHub Actions 并首次发布

在 GitHub 仓库打开 **Settings → Secrets and variables → Actions**。

| 位置 | 名称 | 值 |
| --- | --- | --- |
| Variables | `DEPLOY_URL` | `https://你的域名/_deploy/yatds` |
| Secrets | `DEPLOY_TOKEN` | 与服务器环境文件一致的值 |

工作流已在 `.github/workflows/deploy.yml`。没有设置 `DEPLOY_URL` 时，部署任务会跳过。配置两项后，在 **Actions → Deploy YatDS → Run workflow** 选择 `main` 手动运行一次。以后每次推送或合并到 `main` 都会请求部署。[工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) · [Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)

首次运行成功后，打开网站地址，检查首页、讲义、实验页和 PPTX 下载。

## 5. 排查与回滚

- **401**：检查 GitHub Secret 和服务器环境文件中的 token 是否一致。修改环境文件后运行 `sudo systemctl restart yatds-deploy`。
- **404**：检查域名和路径是否命中正确的 Nginx HTTPS 站点。首次发布之前，静态首页可能尚未存在。
- **409**：已经有部署在执行。完成后可手动重跑。
- **500/502/504**：用 `sudo journalctl -u yatds-deploy -n 80 --no-pager` 检查 Git 权限、仓库路径、部署服务和超时。提交中要有 `site/index.html`。
- **回滚**：优先在 GitHub 撤销有问题的提交并推送到 `main`，触发一次正常部署。历史发布目录位于 `/srv/yatds/releases/`。日后清理磁盘时，先用 `readlink /srv/yatds/current` 确认当前版本，再清理不再需要的历史目录。
