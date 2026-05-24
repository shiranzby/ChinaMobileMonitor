# 青龙面板适配说明

## 背景

青龙面板（Qinglong）是一个定时任务管理平台，支持 Python 脚本定时执行。  
由于青龙的容器环境**不一定支持 Playwright 浏览器**，此处提供两种适配方案。

---

## 方案一：青龙环境安装 Playwright（推荐，全自动）

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

## 方案二：手动复制登录状态（Playwright 不可用时的兜底方案）

如果青龙环境无法安装 Chromium（资源受限、权限不足等），可改用此方案：

### 原理

登录状态保存在 `chinamobile_data/<手机号>/playwright_user_data/` 目录中。  
只需在**有浏览器的机器上**完成登录，然后**将整个目录复制到青龙对应路径**即可。

### 步骤

#### ① 在本地（Windows/Mac）完成登录

```bash
python chinamobile.py --login 138xxxx1234
```

登录成功后，本地会生成：
```
chinamobile_data/
  138xxxx1234/
    playwright_user_data/   ← 这就是登录状态
```

#### ② 将登录状态目录上传到青龙

**方式 A：青龙 Web 上传**

1. 打开青龙「脚本管理」
2. 新建目录 `chinamobile_data/138xxxx1234/`
3. 将本地 `playwright_user_data/` 整个文件夹上传到该目录下

**方式 B：SSH/SCP 上传**

```bash
# 在本地执行（需青龙 SSH 可达）
scp -r chinamobile_data/138xxxx1234/ root@<青龙IP>:/ql/data/scripts/chinamobile_data/138xxxx1234/
```

#### ③ 在青龙中验证

在青龙「终端」中执行：

```bash
python chinamobile.py --query 138xxxx1234
```

能正常输出查询结果即说明登录状态有效。

#### ④ 登录状态过期后

重复①~③步骤，覆盖上传新的 `playwright_user_data/` 目录即可。  
通常有效期为 7~30 天，视移动风控策略而定。

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

**Q：青龙里 playwright install 失败，磁盘空间不足？**  
A：Chromium 及其依赖约需 500MB 空间。可清理青龙镜像或换用方案二。

**Q：复制登录状态后提示「登录状态已过期」？**  
A：移动登录状态通常有有效期，过期后需重新在本地登录并重新上传。

**Q：多号码怎么配置？**  
A：在 `chinamobile_config.json` 的 `手机号` 列表中添加多个号码即可，查询时会自动并发执行。
