# 青龙面板适配说明

## 背景

青龙面板（Qinglong）是一个定时任务管理平台，支持 Python 脚本定时执行。
本脚本依赖 Playwright 浏览器，需在青龙环境中安装 Playwright 及 Chromium 方可全功能运行。

---

## 青龙环境安装 Playwright（全功能）

### 1. 在青龙「依赖管理」中安装 Python 依赖

```
playwright
requests
```

### 2. 安装 Chromium 浏览器

在青龙「SSH」或「脚本管理」→「终端」中执行：

```bash
playwright install chromium --with-deps
```

> 如提示权限不足，需在青龙宿主机上以 root 执行。

### 3. 上传脚本文件

将以下文件上传到青龙「脚本管理」：
- `chinamobile.py`
- `chinamobile_capture.py`（可选，用于抓 API）

### 4. 创建配置文件

在脚本同级目录创建 `chinamobile_config.json`，参考 `chinamobile_config.example.json` 填写。

### 5. 创建定时任务

在青龙「定时任务」中新建任务：
- **名称**：移动话费查询
- **命令**：`task chinamobile.py --query`
- **定时规则**：`30 7 * * *`（每日早 7:30 执行，按需调整）

---

## 通知推送（青龙环境）

青龙内置通知系统，可直接调用环境变量推送结果。

修改 `chinamobile.py` 的 `send_notify()` 函数，在推送逻辑中增加青龙通知调用：

```python
# 青龙通知（可选接入）
def send_notify(title, body, notify_config):
    # ... 原有推送逻辑 ...

    # 青龙通知（调用 ql 命令或环境变量）
    import subprocess
    try:
        subprocess.run(["ql", "notify", title, body], check=True)
    except Exception:
        pass
```

如不修改代码，也可在青龙定时任务的「任务输出」中查看查询结果，或依赖脚本内置的 SMTP/PushPlus 等推送渠道。

---

## 常见问题

**Q：多号码怎么配置？**
A：在 `chinamobile_config.json` 的 `手机号` 列表中添加多个号码即可，查询时会自动并发执行。

**Q：登录时提示需要验证码怎么办？**
A：首次登录需要手动输入短信验证码。可在本地完成登录后，将整个项目目录（含 `chinamobile_data/`）上传到青龙，后续查询无需再次登录。
