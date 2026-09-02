# 阿里云 Windows 服务器 · 部署步骤

目标：把平台部署到阿里云 Windows 服务器（公网 IP `120.26.23.146`），实现**开机自启、常驻后台**，
之后任何浏览器访问 `http://120.26.23.146:8000` 即可进入。

---

## 一、阿里云安全组放行 8000 端口（必须先做，否则浏览器连不上）

1. 登录 [阿里云控制台](https://home.console.aliyun.com) → 顶部搜索「**云服务器 ECS**」进入。
2. 在「实例」列表找到 IP 为 `120.26.23.146` 的实例 → 点击实例 ID 进入详情。
3. 左侧菜单 → 「**网络与安全** → **安全组**」（或实例详情页点「安全组」标签）。
4. 点当前绑定的安全组 ID 进入 → 「**入方向**」标签页 → 点「**手动添加**」。
5. 按下表填写后点「保存」：

| 字段 | 填写值 |
|---|---|
| 授权策略 | 允许 |
| 优先级 | 1 |
| 协议类型 | 自定义 TCP |
| 端口范围 | `8000/8000` |
| 授权对象 | `0.0.0.0/0`（允许所有人；若只想自己访问，填你家的出口 IP + `/32`） |
| 描述 | 智慧养老平台 |

> 说明：
> - 「授权对象」填 `0.0.0.0/0` 表示公网任何人都能访问；更安全做法是只填你自己的 IP。
> - 若以后想用 80/443 端口（域名 + HTTPS），同理放行 `80/80`、`443/443`。

---

## 二、在 Windows 服务器上部署

### 第 1 步：远程桌面登录服务器

用 Windows「远程桌面连接（mstsc）」登录：地址填 `120.26.23.146`，账号密码是你在阿里云设置的管理员账号。

### 第 2 步：安装 Python

1. 打开浏览器访问 https://www.python.org/downloads/ 下载 **Python 3.12**（Windows installer 64-bit）。
2. 安装时**务必勾选「Add python.exe to PATH」**，然后一路下一步。

> mediapipe / opencv 需要 VC++ 运行库，若后面 import 报错，安装：
> https://aka.ms/vs/17/release/vc_redist.x64.exe

### 第 3 步：拉取代码

项目推到 GitHub 后，在服务器上执行（服务器桌面开 cmd / PowerShell）：

```bat
cd C:\
git clone https://github.com/<你的用户名>/<仓库名>.git eldercare-platform
cd eldercare-platform
```

（服务器若没装 git，先装 https://git-scm.com/download/win ）

### 第 4 步：安装依赖并启动测试

```bat
cd C:\eldercare-platform
pip install -r requirements.txt
python run.py
```

看到 `Uvicorn running on http://0.0.0.0:8000` 即成功。此时另开一个本机浏览器访问
`http://localhost:8000` 应能打开登录页（admin / admin123）。

> 首次启动会自动下载姿态模型到 `models/`、初始化数据库到 `data/`，稍等即可。

### 第 5 步：放行 Windows 防火墙 8000 端口

以**管理员身份**打开 cmd，执行：

```bat
netsh advfirewall firewall add rule name="ElderCare 8000" dir=in action=allow protocol=TCP localport=8000
```

### 第 6 步：设为开机自启（后台常驻、无窗口）

**方式 A：任务计划程序（推荐）**

管理员 cmd 执行（注意把路径改成你实际的 Python 和项目路径）：

```bat
schtasks /Create /TN "ElderCarePlatform" /TR "C:\Python312\python.exe C:\eldercare-platform\run.py" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
```

> `/SC ONSTART` 表示开机即启动；`/RU SYSTEM` 以系统身份运行（无需登录桌面）。

验证 / 手动启动：

```bat
schtasks /Run /TN "ElderCarePlatform"
schtasks /Query /TN "ElderCarePlatform"
```

**方式 B：启动文件夹（需登录桌面才生效）**

把下面内容存成 `C:\Users\<你的用户名>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\ElderCarePlatform.vbs`：

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\eldercare-platform"
WshShell.Run """C:\Python312\python.exe"" ""C:\eldercare-platform\run.py""", 0, False
```

---

## 三、验证公网访问

在你自己电脑的浏览器打开：

```
http://120.26.23.146:8000
```

看到登录页即部署成功，默认账号 `admin / admin123`。

---

## 四、后续更新代码

以后本地改了代码推到 GitHub 后，在服务器上执行：

```bat
cd C:\eldercare-platform
git pull
schtasks /End /TN "ElderCarePlatform" & schtasks /Run /TN "ElderCarePlatform"
```

---

## 五、安全建议

- 上线后**立即修改默认密码** `admin123`（后台「用户管理」或「隐私安全」页）。
- 安全组「授权对象」建议从 `0.0.0.0/0` 改为你自己的出口 IP，避免被全网扫描。
- 有域名后，可加 Nginx + HTTPS（后续再配）。
