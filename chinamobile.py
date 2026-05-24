#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
中国移动 10086 自动查询脚本（多号码支持）

用法:
    python chinamobile.py --login 18212348674        # 登录并保存该号码状态
    python chinamobile.py --query                   # 查询配置文件中的所有号码
    python chinamobile.py --query 18212348674    # 查询指定号码
    python chinamobile.py --query --json            # 查询并保存原始 API 响应
    python chinamobile.py --open  18212348674    # 用该号码的登录状态打开浏览器
    python chinamobile.py --help                   # 显示帮助

配置文件: chinamobile_config.json（JSON 不支持注释，字段名即为说明）

    手机号（列表）:
        - 号码: 手机号

    输出设置（全局默认，1 显示 / 0 隐藏）:
        城市         如「四川宜宾」
        余额         如「229.31元」
        套餐名       如「动感地带青春卡68套餐」
        通用流量     已用 / 总量
        定向流量     已用 / 总量
        区域流量     已用 / 总量
        总流量       已用 / 总量
        语音已用     已用 / 总量（分钟）
        语音剩余     剩余分钟数
        短信已用     已用 / 总量（条）
        短信剩余     剩余条数
        查询时间     数据获取的时间戳
"""

import argparse
import asyncio
import concurrent.futures
import sys
import json
import os
import re
import base64
import binascii
import hashlib
import hmac as _hmac
import urllib.parse
import smtplib
import certifi
import requests
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime
from pathlib import Path
from Crypto.Cipher import AES
from playwright.async_api import async_playwright

# ============================================================
#  路径
# ============================================================
SCRIPT_DIR  = Path(__file__).parent
DATA_DIR    = SCRIPT_DIR / "chinamobile_data"
CONFIG_FILE = SCRIPT_DIR / "chinamobile_config.json"
DATA_DIR.mkdir(exist_ok=True)

# ============================================================
#  中文字段名 → 内部字段名 映射（配置文件用中文，代码用英文）
# ============================================================
FIELD_MAP = {
    "城市":         "city",
    "余额":         "balance",
    "套餐名":       "plan_name",
    "实时费用":     "realtime_fee",
    "本月账单":     "bill_total",
    "实际应缴":     "bill_real",
    "优惠合计":     "discount_total",
    "账单周期":     "bill_cycle",
    "通用流量":     "general_flow",
    "定向流量":     "special_flow",
    "区域流量":     "regional_flow",
    "总流量":       "total_flow",
    "语音已用":     "voice_used",
    "语音剩余":     "voice_remaining",
    "短信已用":     "sms_used",
    "短信剩余":     "sms_remaining",
    "查询时间":     "query_time",
}

# ============================================================
#  默认输出配置（1 = 显示，0 = 隐藏）
# ============================================================
DEFAULT_OUTPUT = {
    "city":             1,
    "balance":          1,
    "plan_name":        1,
    "realtime_fee":     0,   # 需主动开启
    "bill_total":       0,   # 需主动开启（访问账单页才触发）
    "bill_real":        0,   # 需主动开启
    "discount_total":   0,   # 需主动开启
    "bill_cycle":       0,   # 需主动开启
    "general_flow":     1,
    "special_flow":     1,
    "regional_flow":    1,
    "total_flow":       1,
    "voice_used":       1,
    "voice_remaining":  0,
    "sms_used":         1,
    "sms_remaining":    0,
    "query_time":       1,
}


# ============================================================
#  工具函数
# ============================================================
def get_phone_dir(phone: str) -> Path:
    """返回该手机号的数据目录"""
    return DATA_DIR / phone


def get_user_data_dir(phone: str) -> Path:
    """返回该手机号的 Playwright 持久化目录"""
    return get_phone_dir(phone) / "playwright_user_data"


def safe_input(prompt: str) -> str:
    """线程安全输入，绕过 asyncio stdin 冲突（Windows）"""
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except ImportError:
        pass
    print(prompt, end="", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(sys.stdin.readline)
        try:
            return future.result(timeout=600).strip()
        except concurrent.futures.TimeoutError:
            return ""


def aes_decrypt(data_hex: str) -> str:
    """AES 解密（中国移动 wx.10086.cn 使用）"""
    if not data_hex or len(data_hex) < 32:
        return ""
    try:
        if not all(c in "0123456789abcdefABCDEF" for c in data_hex[:32]):
            return data_hex
        key = b"1234123412ABCDEF"
        iv  = b"ABCDEF1234123412"
        data_bytes = binascii.unhexlify(data_hex)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(data_bytes)
        pad_len = decrypted[-1]
        if 1 <= pad_len <= 16:
            decrypted = decrypted[:-pad_len]
        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        return data_hex


def extract_balance(page_text: str) -> str:
    """从页面文本中提取余额"""
    import re
    m = re.search(r'话费余额\s*\n?\s*(\d+\.\d{2})\s*元', page_text)
    if m:
        return m.group(1) + "元"
    m = re.search(r'(\d+\.\d{2})\s*元\s*\n?\s*话费余额', page_text)
    if m:
        return m.group(1) + "元"
    return "未知"


UNIT_MAP = {
    "01": "分钟",
    "02": "条",
    "03": "MB",
    "04": "GB",
}


def smart_data_unit(val_str, unit_code: str = "04") -> str:
    """根据API返回的unit字段智能换算并带单位
    unit_code: 01=分钟 02=条 03=MB 04=GB
    """
    try:
        val = float(val_str)
    except (ValueError, TypeError):
        return str(val_str)

    unit_name = UNIT_MAP.get(unit_code, "GB")

    if unit_code == "03":
        # MB -> GB
        gb = val / 1024
        if gb >= 0.01:
            return f"{gb:.2f}GB"
        else:
            return f"{val:.0f}MB" if val == int(val) else f"{val:.2f}MB"
    elif unit_code == "04":
        return f"{val:.2f}GB" if val != int(val) else f"{int(val)}GB"
    else:
        # 01=分钟 02=条
        return f"{int(val)}{unit_name}" if val == int(val) else f"{val:.2f}{unit_name}"


def _extract_item(raw: dict) -> dict:
    """提取用量数据并保留单位"""
    return {
        "used":  raw.get("usedNum", "0"),
        "remain": raw.get("remainNum", "0"),
        "total": raw.get("sumNum", "0"),
        "unit":  raw.get("unit", "04"),
    }


def parse_margin(data: dict) -> dict:
    """解析 getNewMarginInfo 响应，提取流量/语音/短信（含单位）"""
    result = {"flow": {}, "voice": {}, "sms": {}}
    rd = data.get("data", {}).get("resultData", {})

    # 流量
    flow = rd.get("planRemianFlowInfo", {})
    if flow:
        result["flow"]["plan"] = _extract_item(flow.get("planRemian", {}))
        result["flow"]["directional"] = _extract_item(flow.get("directionalFlowInfo", {}))
        result["flow"]["other"] = _extract_item(flow.get("otherRemian", {}))
        result["flow"]["total"] = _extract_item(flow.get("totalInfo", {}))

    # 语音
    voice = rd.get("planRemianVoiceInfo", {})
    if voice:
        result["voice"]["plan"] = _extract_item(voice.get("planRemian", {}))
        result["voice"]["other"] = _extract_item(voice.get("otherRemian", {}))
        result["voice"]["total"] = _extract_item(voice.get("totalInfo", {}))

    # 短信
    sms = rd.get("planRemianMSGInfo", {})
    if sms:
        result["sms"]["plan"] = _extract_item(sms.get("notePlanRemian", {}))
        result["sms"]["total"] = _extract_item(sms.get("totalInfo", {}))

    return result


# ============================================================
#  登录（多号码）
# ============================================================
async def login(phone: str):
    """登录指定手机号，保存持久化状态到该手机号的目录"""
    print("=" * 50)
    print(f"中国移动登录 → {phone}")
    print("=" * 50)

    phone_dir      = get_phone_dir(phone)
    user_data_dir  = get_user_data_dir(phone)

    # 如果已存在，先删除旧状态（用户要求覆盖）
    if user_data_dir.exists():
        print(f"\n检测到 {phone} 已有保存状态，将覆盖...")

    phone_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        # 打开登录页
        print("\n打开登录页面...")
        await page.goto(
            "https://wx.10086.cn/website/bind/bindAccount/new",
            timeout=30000,
        )
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # 自动勾选隐私政策
        print("勾选隐私政策...")
        await page.evaluate("""() => {
            const el = document.querySelector('.checkCtl.noCheck') || document.querySelector('.checkCtl');
            if (el) { el.click(); return 'clicked'; }
            return 'NOT_FOUND';
        }""")
        await asyncio.sleep(1)

        # 自动填写手机号
        print(f"填写手机号: {phone}")
        await page.evaluate(f"""() => {{
            const inp = document.querySelector('#phone') || document.querySelector('input[type="tel"]');
            if (inp) {{
                inp.value = '{phone}';
                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                return 'filled';
            }}
            return 'NOT_FOUND';
        }}""")
        await asyncio.sleep(1)

        # 点击发送验证码
        print("发送验证码...")
        click_result = await page.evaluate("""() => {
            const candidates = [...document.querySelectorAll('button, a, span, div')].filter(e => e.offsetWidth > 0);
            for (const e of candidates) {{
                const t = (e.innerText || e.textContent || '').trim();
                if ((t === '获取验证码' || t === '发送验证码') && e.children.length < 3) {{
                    e.click();
                    return 'clicked:' + e.tagName + ':' + t;
                }}
            }}
            return 'NOT_FOUND';
        }""")
        print(f"  结果: {click_result}")

        await asyncio.sleep(3)

        # 检查发送状态
        send_error = await page.evaluate("""() => {
            const inp = document.querySelector('#code');
            if (!inp) return '';
            const ph = inp.placeholder || '';
            if (ph && (ph.includes('频繁') || ph.includes('过多') || ph.includes('稍后') || ph.includes('失败'))) return ph;
            return '';
        }""")
        if send_error:
            print(f"  发送失败: {send_error}")
            await context.close()
            return False

        # 等待用户输入验证码
        code = safe_input("\n请输入收到的验证码: ")
        if not code:
            print("未输入验证码，退出")
            await context.close()
            return False

        # 填写验证码并登录
        print("填写验证码并登录...")
        await page.evaluate(f"""() => {{
            const inp = document.querySelector('#code') || document.querySelector('input[placeholder*="验证码"]');
            if (inp) {{
                inp.removeAttribute('readonly');
                inp.value = '{code}';
                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
            }}
            const btn = document.querySelector('#loginBtn') || document.querySelector('button[type="submit"]');
            if (btn) btn.click();
            return 'done';
        }}""")

        print("等待登录完成...")
        await asyncio.sleep(5)

        # 检查是否登录成功
        current_url = page.url
        print(f"\n当前 URL: {current_url}")
        page_text = await page.evaluate("() => document.body.innerText")
        if "退出" in page_text or "余额" in page_text or "套餐" in page_text or "我的" in page_text:
            print("登录成功！")
        else:
            print("未检测到登录成功标志，但状态已保存")

        await context.close()
        print(f"\n登录完成！状态已保存到: {user_data_dir}")
        print(f"下次查询时自动复用该状态。")
        return True


# ============================================================
#  查询（多号码）
# ============================================================
async def query_single(phone: str, save_json: bool = False) -> dict:
    """查询单个手机号，返回结构化结果"""
    user_data_dir = get_user_data_dir(phone)
    if not user_data_dir.exists():
        return {"phone": phone, "error": f"未找到登录状态，请先运行: python chinamobile.py --login {phone}"}

    raw_results    = {}
    parsed_results = {}

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=True,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        # 设置 API 拦截
        target_apis = [
            "getNewMarginInfo", "getMainPlan", "getMarginQueryInfo",
            "getCustBaseInfo",
            "fareBalance", "accountFeeBalanceQuery", "getBillSum",
        ]

        async def handle_route(route, request):
            url = request.url
            api_name = None
            for name in target_apis:
                if name in url:
                    api_name = name
                    break
            if api_name:
                try:
                    response = await route.fetch()
                    body = await response.text()
                    raw_results[api_name] = body
                    # 尝试解密
                    if len(body) > 32 and all(
                        c in "0123456789abcdefABCDEF" for c in body[:32]
                    ):
                        dec = aes_decrypt(body)
                        try:
                            parsed_results[api_name] = json.loads(dec)
                        except json.JSONDecodeError:
                            parsed_results[api_name] = dec
                    else:
                        try:
                            parsed_results[api_name] = json.loads(body)
                        except json.JSONDecodeError:
                            parsed_results[api_name] = body
                except Exception:
                    pass
                await route.continue_()
            else:
                await route.continue_()

        await page.route("**/*", handle_route)

        # 访问首页
        await page.goto(
            "https://wx.10086.cn/website/spa/main/newHome", timeout=30000
        )
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(5)

        # 检查登录状态
        page_text = await page.evaluate("() => document.body.innerText")
        if "动感地带" not in page_text and "青春卡" not in page_text and "套餐" not in page_text:
            await context.close()
            return {"phone": phone, "error": "登录状态已过期，请重新运行: python chinamobile.py --login " + phone}

        # 提取余额（优先从 API，回退到页面文本）
        balance = "未知"
        fare_data = parsed_results.get("fareBalance")
        if fare_data and isinstance(fare_data, dict):
            try:
                fee_rsp = fare_data.get("data", {}).get("realFeeQryRsp", {})
                cur_total = fee_rsp.get("curFeeTotal", "")
                if cur_total:
                    balance = cur_total + "元"
            except Exception:
                pass
        if balance == "未知":
            balance = extract_balance(page_text)

        # 如果拦截不到 getMainPlan，尝试用 fetch 补充
        if "getMainPlan" not in parsed_results:
            ts = int(datetime.now().timestamp() * 1000)
            url = f"https://wx.10086.cn/website/serviceMargin/getMainPlan?t={ts}"
            result = await page.evaluate("""(args) => {
                const [url] = args;
                return (async () => {
                    try {
                        const resp = await fetch(url, {
                            method: 'GET',
                            credentials: 'include',
                            headers: {
                                'Accept': 'application/json, text/plain, */*',
                                'X-Requested-With': 'XMLHttpRequest',
                            }
                        });
                        const text = await resp.text();
                        return { status: resp.status, ok: resp.ok, body: text };
                    } catch(e) {
                        return { status: 0, ok: false, body: e.toString() };
                    }
                })();
            }""", [url])
            if result.get("ok"):
                body = result["body"]
                if len(body) > 32 and all(
                    c in "0123456789abcdefABCDEF" for c in body[:32]
                ):
                    dec = aes_decrypt(body)
                    try:
                        parsed_results["getMainPlan"] = json.loads(dec)
                    except json.JSONDecodeError:
                        pass
                else:
                    try:
                        parsed_results["getMainPlan"] = json.loads(body)
                        raw_results["getMainPlan"] = body
                    except json.JSONDecodeError:
                        pass

        await context.close()

    # 解析结果
    result = {"phone": phone, "balance": balance, "plan_name": "未知", "city": "未知"}

    plan_data = parsed_results.get("getMainPlan")
    if plan_data and isinstance(plan_data, dict):
        plan_name = (
            plan_data.get("object", {})
            .get("resultData", {})
            .get("curPlanName", "未知")
        )
        result["plan_name"] = plan_name

    # 解析城市（优先 getCustBaseInfo，回退 getMarginQueryInfo）
    city = "未知"
    cust_data = parsed_results.get("getCustBaseInfo")
    if cust_data and isinstance(cust_data, dict):
        try:
            city = cust_data.get("bean", {}).get("data", {}).get("customerAssignment", "")
            if not city:
                city = cust_data.get("bean", {}).get("data", {}).get("placeName", "")
        except Exception:
            pass
    if city == "未知":
        margin_query = parsed_results.get("getMarginQueryInfo")
        if margin_query and isinstance(margin_query, dict):
            try:
                city = margin_query.get("data", {}).get("resultData", {}).get("customerAssignment", "")
            except Exception:
                pass
    if city:
        result["city"] = city

    margin_data = parsed_results.get("getNewMarginInfo")
    if margin_data and isinstance(margin_data, dict):
        margin = parse_margin(margin_data)
        result["margin"] = margin

    # 解析实时费用（accountFeeBalanceQuery 或 fareBalance）
    fee_data = parsed_results.get("accountFeeBalanceQuery") or parsed_results.get("fareBalance")
    if fee_data and isinstance(fee_data, dict):
        try:
            d   = fee_data.get("data", {})
            qry = d.get("realFeeQryRsp", {})
            if not qry:
                qry = d   # accountFeeBalanceQuery 结构
            result["realtime_fee"] = qry.get("realFee", "")
            # 以下字段仅作信息记录，不输出
            result["_owe_fee"]     = qry.get("oweFee", "")
            result["_should_pay"]  = qry.get("shouldPay", "")
            result["_pay_type"]    = qry.get("payType", "")
        except Exception:
            pass

    # 解析本月账单（getBillSum）
    billsum_data = parsed_results.get("getBillSum")
    if billsum_data and isinstance(billsum_data, dict):
        try:
            obj   = billsum_data.get("object", {}).get("resultData", {})
            result["bill_cycle"]    = f"{obj.get('cycleBeginDate','')} ~ {obj.get('cycleEndDate','')}"
            result["bill_total"]    = obj.get("toatlBill", "")    # API 字段拼写：toatlBill
            result["bill_real"]     = obj.get("realBillSum", "")
            # 优惠合计（嵌套在 costSaveDetails 内）
            save = obj.get("costSaveDetails", {})
            result["discount_total"] = save.get("costSaveTotal", "")
        except Exception:
            pass

    # 保存 JSON
    if save_json:
        phone_dir = get_phone_dir(phone)
        query_dir = phone_dir / "query_results"
        query_dir.mkdir(parents=True, exist_ok=True)
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        out   = {
            "time":     datetime.now().isoformat(),
            "phone":    phone,
            "balance":  balance,
            "plan_name": result.get("plan_name", "未知"),
            "raw_api_responses": raw_results,
            "parsed":   parsed_results,
        }
        out_file = query_dir / f"query_{ts}.json"
        out_file.write_text(
            json.dumps(out, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        result["json_file"] = str(out_file)
        print(f"  [{phone}] 原始响应已保存: {out_file.name}")

    result["queried_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


# ============================================================
#  打开浏览器（多号码）
# ============================================================
async def open_browser(phone: str):
    """用指定手机号的登录状态打开浏览器"""
    user_data_dir = get_user_data_dir(phone)
    if not user_data_dir.exists():
        print(f"未找到 {phone} 的登录状态，请先运行: python chinamobile.py --login {phone}")
        return False

    print(f"正在为 {phone} 打开浏览器（使用已保存的登录状态）...")
    print("关闭浏览器窗口即可退出。")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()
        await page.goto(
            "https://wx.10086.cn/website/spa/main/newHome",
            timeout=30000,
        )
        # 保持浏览器打开
        await asyncio.sleep(999999)

    return True


# ============================================================
#  配置加载（中文字段 → 内部字段自动转换）
# ============================================================
def _translate_output(raw: dict) -> dict:
    """将中文 key 的输出配置翻译为内部英文 key"""
    result = {}
    for cn_key, val in raw.items():
        en_key = FIELD_MAP.get(cn_key, cn_key)
        result[en_key] = val
    return result


def load_config() -> tuple:
    """加载配置文件，返回 (phones, global_output, notify_config)"""
    if not CONFIG_FILE.exists():
        return [], {}, {}
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    phones = []
    for entry in data.get("手机号", []):
        acct = {"phone": entry.get("号码", "")}
        if "输出设置" in entry:
            acct["output"] = _translate_output(entry["输出设置"])
        phones.append(acct)
    global_output = _translate_output(data.get("输出设置", {}))

    # 通知推送配置
    notify_config = data.get("通知推送", {})

    return phones, global_output, notify_config


def save_config(phones: list, global_output: dict):
    """保存配置到 JSON 文件"""
    data = {
        "手机号": [{"号码": p["phone"]} for p in phones],
        "输出设置": {v: k for k, v in FIELD_MAP.items() if k in global_output and global_output[k]},
    }
    # 保留中文 key
    cn_output = {}
    for en_key, val in global_output.items():
        cn_key = [k for k, v in FIELD_MAP.items() if v == en_key]
        if cn_key:
            cn_output[cn_key[0]] = val
        else:
            cn_output[en_key] = val
    data["输出设置"] = cn_output
    CONFIG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_phone_to_config(phone: str):
    """向配置文件添加手机号（如不存在）"""
    phones, global_output = load_config()
    if not any(p["phone"] == phone for p in phones):
        phones.append({"phone": phone})
        save_config(phones, global_output)
        print(f"  已添加 {phone} 到配置文件")
    else:
        print(f"  {phone} 已在配置文件中")


# ============================================================
#  输出格式化
# ============================================================
def _merge_config(phone: str, phones: list, global_output: dict) -> dict:
    """合并 默认输出 + 全局配置 + 单号配置"""
    cfg = dict(DEFAULT_OUTPUT)
    cfg.update(global_output)
    for acct in phones:
        if acct["phone"] == phone and "output" in acct:
            cfg.update(acct["output"])
            break
    return cfg


def format_output(results: list, phones: list, global_output: dict) -> str:
    """查询结果 → 简洁可读文本"""
    lines = []
    for i, r in enumerate(results):
        phone = r.get("phone", "")
        cfg   = _merge_config(phone, phones, global_output)

        if "error" in r:
            lines.append(f"手机号：{phone}  [失败] {r['error']}")
            lines.append("")
            continue

        lines.append(f"手机号：{phone}")

        if cfg.get("city"):
            city = r.get("city", "未知")
            if city and city != "未知":
                lines.append(f"  城市：{city}")

        if cfg["plan_name"]:
            lines.append(f"  套餐：{r.get('plan_name', '未知')}")

        if cfg["balance"]:
            lines.append(f"  余额：{r.get('balance', '未知')}")

        if cfg.get("realtime_fee"):
            v = r.get("realtime_fee", "")
            if v:
                lines.append(f"  实时费用：{v}元")

        if cfg.get("bill_total"):
            v = r.get("bill_total", "") or r.get("bill_total_api", "")
            if v:
                lines.append(f"  本月账单：{v}元")

        if cfg.get("bill_real"):
            v = r.get("bill_real", "") or r.get("bill_real_api", "")
            if v:
                lines.append(f"  实际应缴：{v}元")

        if cfg.get("discount_total"):
            v = r.get("discount_total", "")
            if v:
                lines.append(f"  优惠合计：{v}元")

        if cfg.get("bill_cycle"):
            v = r.get("bill_cycle", "")
            if v and v != "~":
                lines.append(f"  账单周期：{v}")

        margin = r.get("margin", {})
        if margin:
            if cfg["general_flow"] and "plan" in margin.get("flow", {}):
                f = margin["flow"]["plan"]
                lines.append(f"  通用流量：已用 {smart_data_unit(f['used'], f['unit'])} / 总 {smart_data_unit(f['total'], f['unit'])}")

            if cfg["special_flow"] and "directional" in margin.get("flow", {}):
                f = margin["flow"]["directional"]
                lines.append(f"  定向流量：已用 {smart_data_unit(f['used'], f['unit'])} / 总 {smart_data_unit(f['total'], f['unit'])}")

            if cfg.get("regional_flow") and "other" in margin.get("flow", {}):
                f = margin["flow"]["other"]
                lines.append(f"  区域流量：已用 {smart_data_unit(f['used'], f['unit'])} / 总 {smart_data_unit(f['total'], f['unit'])}")

            if cfg["total_flow"] and "total" in margin.get("flow", {}):
                f = margin["flow"]["total"]
                lines.append(f"  总流量：已用 {smart_data_unit(f['used'], f['unit'])} / 总 {smart_data_unit(f['total'], f['unit'])}")

            if cfg["voice_used"] and "plan" in margin.get("voice", {}):
                v = margin["voice"]["plan"]
                lines.append(f"  语音：已用 {smart_data_unit(v['used'], v['unit'])} / 总 {smart_data_unit(v['total'], v['unit'])}")

            if cfg["voice_remaining"] and "plan" in margin.get("voice", {}):
                v = margin["voice"]["plan"]
                lines.append(f"  语音剩余：{smart_data_unit(v['remain'], v['unit'])}")

            if cfg["sms_used"] and "total" in margin.get("sms", {}):
                s = margin["sms"]["total"]
                lines.append(f"  短信：已用 {smart_data_unit(s['used'], s['unit'])} / 总 {smart_data_unit(s['total'], s['unit'])}")

            if cfg["sms_remaining"] and "total" in margin.get("sms", {}):
                s = margin["sms"]["total"]
                lines.append(f"  短信剩余：{smart_data_unit(s['remain'], s['unit'])}")

        if cfg["query_time"]:
            lines.append(f"  查询时间：{r.get('queried_at', '')}")

        if i < len(results) - 1:
            lines.append("")

    return "\n".join(lines)


# ============================================================
#  通知推送模块
# ============================================================

def _notify_smtp(title, body, cfg):
    """SMTP 邮件推送"""
    server   = cfg.get("SMTP服务器", "")
    port     = int(cfg.get("SMTP端口", 465))
    use_ssl  = str(cfg.get("SMTP_SSL", "true")).lower() == "true"
    sender   = cfg.get("发件邮箱", "")
    pwd      = cfg.get("邮箱密码或授权码", "")
    name     = cfg.get("发件人昵称", "移动查询")
    receiver = cfg.get("收件邮箱", "") or sender
    if not all([server, sender, pwd]):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = formataddr((Header(name, "utf-8").encode(), sender))
    msg["To"]      = formataddr((Header(name, "utf-8").encode(), receiver))
    msg["Subject"] = Header(title, "utf-8")
    try:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        srv = smtp_cls(server, port)
        srv.login(sender, pwd)
        srv.sendmail(sender, [r.strip() for r in receiver.split(",")], msg.as_bytes())
        srv.quit()
        print("[通知] SMTP 邮件推送成功")
    except Exception as e:
        print(f"[通知] SMTP 推送失败: {e}")


def _notify_pushplus(title, body, cfg):
    """PushPlus 微信推送"""
    token = cfg.get("PushPlus令牌", "")
    if not token:
        return
    try:
        resp = requests.post("https://www.pushplus.plus/send", json={
            "token": token, "title": title, "content": body,
            "template": "txt", "channel": "wechat",
        }, timeout=10)
        r = resp.json()
        if r.get("code") == 200:
            print("[通知] PushPlus 推送成功")
        else:
            print(f"[通知] PushPlus 推送失败: {r.get('msg', '')}")
    except Exception as e:
        print(f"[通知] PushPlus 推送失败: {e}")


def _notify_serverchan(title, body, cfg):
    """Server酱 微信推送"""
    key = cfg.get("Server酱密钥", "")
    if not key:
        return
    try:
        match = re.match(r"sctp(\d+)t", key)
        url = f"https://{match.group(1)}.push.ft07.com/send/{key}.send" if match else f"https://sctapi.ftqq.com/{key}.send"
        resp = requests.post(url, data={"text": title, "desp": body.replace("\n", "\n\n")}, timeout=10)
        r = resp.json()
        if r.get("errno") == 0 or r.get("code") == 0:
            print("[通知] Server酱 推送成功")
        else:
            print(f"[通知] Server酱 推送失败: {r.get('message', '')}")
    except Exception as e:
        print(f"[通知] Server酱 推送失败: {e}")


def _notify_bark(title, body, cfg):
    """Bark iOS 推送"""
    key = cfg.get("Bark设备码", "")
    if not key:
        return
    url = key if key.startswith("http") else f"https://api.day.app/{key}"
    try:
        resp = requests.post(url, json={"title": title, "body": body}, timeout=10)
        if resp.json().get("code") == 200:
            print("[通知] Bark 推送成功")
        else:
            print("[通知] Bark 推送失败")
    except Exception as e:
        print(f"[通知] Bark 推送失败: {e}")


def _notify_telegram(title, body, cfg):
    """Telegram Bot 推送"""
    bot_token = cfg.get("Telegram机器人Token", "")
    user_id   = cfg.get("Telegram用户ID", "")
    if not bot_token or not user_id:
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": user_id, "text": f"{title}\n\n{body}", "disable_web_page_preview": "true"},
            timeout=10,
        )
        if resp.json().get("ok"):
            print("[通知] Telegram 推送成功")
        else:
            print("[通知] Telegram 推送失败")
    except Exception as e:
        print(f"[通知] Telegram 推送失败: {e}")


def _notify_wecom_bot(title, body, cfg):
    """企业微信机器人推送"""
    key = cfg.get("企业微信机器人Key", "")
    if not key:
        return
    try:
        resp = requests.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}",
            json={"msgtype": "text", "text": {"content": f"{title}\n\n{body}"}},
            timeout=10,
        )
        if resp.json().get("errcode") == 0:
            print("[通知] 企业微信 推送成功")
        else:
            print("[通知] 企业微信 推送失败")
    except Exception as e:
        print(f"[通知] 企业微信 推送失败: {e}")


def _notify_dingtalk(title, body, cfg):
    """钉钉机器人推送"""
    token  = cfg.get("钉钉机器人Token", "")
    secret = cfg.get("钉钉机器人密钥", "")
    if not token or not secret:
        return
    try:
        ts = str(round(datetime.now().timestamp() * 1000))
        string_to_sign = f"{ts}\n{secret}"
        sign = urllib.parse.quote_plus(
            base64.b64encode(_hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest())
        )
        url = f"https://oapi.dingtalk.com/robot/send?access_token={token}&timestamp={ts}&sign={sign}"
        resp = requests.post(url, json={
            "msgtype": "text", "text": {"content": f"{title}\n\n{body}"},
        }, timeout=10)
        if not resp.json().get("errcode"):
            print("[通知] 钉钉 推送成功")
        else:
            print("[通知] 钉钉 推送失败")
    except Exception as e:
        print(f"[通知] 钉钉 推送失败: {e}")


def _notify_feishu(title, body, cfg):
    """飞书机器人推送"""
    key = cfg.get("飞书机器人Key", "")
    if not key:
        return
    try:
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/bot/v2/hook/{key}",
            json={"msg_type": "text", "content": {"text": f"{title}\n\n{body}"}},
            timeout=10,
        )
        if resp.json().get("code") == 0 or resp.json().get("StatusCode") == 0:
            print("[通知] 飞书 推送成功")
        else:
            print("[通知] 飞书 推送失败")
    except Exception as e:
        print(f"[通知] 飞书 推送失败: {e}")


def _notify_webhook(title, body, cfg):
    """自定义 Webhook 推送"""
    url    = cfg.get("自定义Webhook地址", "")
    method = cfg.get("自定义Webhook方法", "POST").upper()
    tpl    = cfg.get("自定义Webhook请求体", "")
    if not url:
        return
    try:
        req_body = tpl.replace("$title", title).replace("$content", body) if tpl else body
        headers  = {"Content-Type": "application/json"} if tpl else {}
        resp = requests.request(method, url, data=req_body, headers=headers, timeout=10)
        if resp.status_code == 200:
            print("[通知] 自定义 Webhook 推送成功")
        else:
            print(f"[通知] 自定义 Webhook 推送失败: HTTP {resp.status_code}")
    except Exception as e:
        print(f"[通知] 自定义 Webhook 推送失败: {e}")


def send_notify(title, body, notify_config):
    """统一推送入口：根据配置自动启用已填写的渠道"""
    push_cfg   = notify_config.get("推送渠道", {})
    extra_cfg  = notify_config.get("可选渠道", {})
    # 合并两级配置（可选渠道字段名不重复则无冲突）
    merged = {**extra_cfg, **push_cfg}
    # 按优先级依次推送
    _notify_smtp(title, body, merged)
    _notify_pushplus(title, body, merged)
    _notify_serverchan(title, body, merged)
    _notify_bark(title, body, merged)
    _notify_telegram(title, body, merged)
    _notify_wecom_bot(title, body, merged)
    _notify_dingtalk(title, body, merged)
    _notify_feishu(title, body, merged)
    _notify_webhook(title, body, merged)


# ============================================================
#  入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="中国移动 10086 自动查询（多号码支持）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python chinamobile.py --login 18212348674        # 登录并保存状态
  python chinamobile.py --query                   # 查询所有号码
  python chinamobile.py --query 18212348674    # 查询指定号码
  python chinamobile.py --query --json            # 查询并保存原始数据
  python chinamobile.py --open  18212348674    # 打开浏览器（该号码状态）
""")
    parser.add_argument("--login",  help="登录并保存指定手机号的状态", type=str, default=None)
    parser.add_argument("--query",  help="查询话费/流量/语音（可指定手机号）", nargs="?", const="", default=None)
    parser.add_argument("--json",   help="保存原始 API 响应到文件", action="store_true")
    parser.add_argument("--open",   help="用指定手机号的状态打开浏览器", type=str, default=None)
    args = parser.parse_args()

    # --login
    if args.login:
        phone = args.login.strip()
        if not phone.isdigit() or len(phone) < 11:
            print("错误：手机号格式不正确")
            sys.exit(1)
        print(f"\n将为手机号 {phone} 进行登录...")
        add_phone_to_config(phone)
        asyncio.run(login(phone))
        return

    # --open
    if args.open:
        phone = args.open.strip()
        if not phone.isdigit() or len(phone) < 11:
            print("错误：手机号格式不正确")
            sys.exit(1)
        asyncio.run(open_browser(phone))
        return

    # --query
    if args.query is not None:
        phones, global_output, notify_config = load_config()
        if not phones:
            print("错误：未配置任何手机号。请先运行: python chinamobile.py --login 你的手机号")
            sys.exit(1)

        # 如果指定了手机号，只查询该号
        if args.query:
            target = args.query.strip()
            if not any(p["phone"] == target for p in phones):
                print(f"警告：{target} 不在配置文件中，临时查询...")
                phones = [{"phone": target}]
            else:
                phones = [p for p in phones if p["phone"] == target]

        print("=" * 50)
        print("中国移动查询")
        print("=" * 50)

        # 多账号并发查询（I/O 密集型）
        async def query_all():
            tasks = []
            for acct in phones:
                phone = acct["phone"]
                tasks.append(query_single(phone, save_json=args.json))
            return await asyncio.gather(*tasks, return_exceptions=True)

        raw_results = asyncio.run(query_all())
        results = []
        for acct, res in zip(phones, raw_results):
            phone = acct["phone"]
            if isinstance(res, Exception):
                results.append({"phone": phone, "error": str(res)})
            else:
                results.append(res)

        print("\n" + "=" * 50)
        print("查询结果")
        print("=" * 50)
        output_text = format_output(results, phones, global_output)
        print(output_text)

        # 如果有 --json，显示保存路径
        if args.json:
            for r in results:
                if "json_file" in r:
                    print(f"\n  {r['phone']} 原始数据: {r['json_file']}")

        print("\n" + "=" * 50)

        # 通知推送
        if notify_config.get("启用") and not args.json:
            print("\n[通知] 正在推送...")
            send_notify("【移动套餐用量监控】", output_text, notify_config)

        return

    # 无参数：显示帮助
    parser.print_help()


if __name__ == "__main__":
    main()
