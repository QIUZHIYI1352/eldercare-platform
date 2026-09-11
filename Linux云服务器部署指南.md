# Linux 云服务器部署指南（MySQL 版 · Docker 一键）

适用：腾讯云/阿里云等轻量 Linux 服务器（推荐 2核4G + Ubuntu 22.04）。
目标：任何有网络的地方都能通过 `http://服务器IP:8000` 登录使用。

## 一、买服务器时注意

1. 选 **Linux（Ubuntu 22.04）**，不要选 Windows（省授权费）。
2. 2核4G 起步（MediaPipe 识别 + MySQL 都要内存）；硬盘 40G 够用。
3. 带宽 3Mbps 以上即可（页面 + 单路视频流够用）。

## 二、开放端口（必须先做，否则外面连不上）

在云厂商控制台（安全组/防火墙）放行：

| 端口 | 用途 |
|---|---|
| 8000 | 平台 Web（对外） |
| 22 | SSH（默认已开） |

授权对象填 `0.0.0.0/0`。MySQL 3306 **不要**对外开放（容器内部自用）。

## 三、登录服务器并安装 Docker

SSH 登录后执行（Ubuntu）：

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
```

## 四、上传代码

方式 A（推荐，需要本地先 push 到 GitHub）：

```bash
git clone https://github.com/QIUZHIYI1352/eldercare-platform.git
cd eldercare-platform
```

若仓库是私有的，clone 时需要 GitHub 用户名 + Personal Access Token（不是登录密码）。

方式 B（不依赖 GitHub）：本地把整个 `E:\eldercare-platform` 目录（去掉 `.git`、`data`、`models`）压缩，用 WinSCP 等工具上传到服务器，再解压。

## 五、配置密码并启动

在项目目录创建 `.env`：

```bash
cd eldercare-platform
echo "MYSQL_ROOT_PASSWORD=你的强密码" > .env
```

启动（首次会自动建 MySQL、装依赖、下载识别模型，约 3~8 分钟）：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f web     # Ctrl+C 退出日志查看
```

看到 web 健康（healthy）后，浏览器访问 `http://服务器IP:8000`。

默认账号 `admin / admin123` —— **部署成功第一件事：登录后在「用户管理/隐私安全」里修改默认密码**。

## 六、日常操作

```bash
docker compose ps                          # 状态
docker compose logs -f web                 # 看日志
docker compose restart web                 # 重启 Web
docker compose down                        # 停止（数据保留）
docker compose up -d --build               # 更新代码后重启
docker compose exec mysql mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" eldercare > backup.sql   # 备份数据库
```

数据都在 Docker 卷 `mysql_data` 里，容器删了数据也不丢；备份请定期执行上面的 mysqldump。

## 七、摄像头（重要）

识别画面来自**服务器能访问的摄像头**：

- 云服务器没有本机摄像头，生产环境请接 RTSP 网络摄像头（海康/大华等），并配置：

```bash
docker compose --profile monitor up -d
```

启动前先改 `docker-compose.yml` 里 monitor 服务的 `MONITOR_SOURCE`（RTSP 地址）与 `MONITOR_PROCESS`（流程名）。

- 学员用自己的电脑摄像头练习是尚未开发的功能（需要浏览器端采集），当前必须站在服务器侧摄像头前练习。

## 八、以后更新代码

本地提交后：

```bash
git push
```

服务器上：

```bash
cd eldercare-platform
git pull
docker compose up -d --build
```

## 九、正式上线建议（以后再做）

- 买域名 + HTTPS（浏览器访问会提示不安全；以后学员用自己摄像头时 HTTPS 是必须）
- 关闭开放注册，账号统一由管理员创建
- 定期备份 MySQL（见第六节）
