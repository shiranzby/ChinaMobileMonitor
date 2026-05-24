#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国移动 10086 API 捕获脚本
用途：用已登录状态打开浏览器，拦截并记录所有 API 请求/响应
用法：
    python chinamobile_capture.py 18284888674          # 捕获该手机号的所有 API
    python chinamobile_capture.py 18284888674 --url  # 只打印 URL，不保存响应体（更清晰）
    python chinamobile_capture.py 18284888674 --out my_capture.json  # 指定输出文件
输出：
    chinamobile_data/<phone>/captured_apis/<timestamp>.json
    控制台实时打印所有捕获到的 API URL
"""

import asyncio
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).parent
DATA_DIR    = SCRIPT_DIR / "chinamobile_data"

def get_user_data_dir(phone: str) -> Path:
    return DATA_DIR / phone / "playwright_user_data"

def get_capture_dir(phone: str) -> Path:
    return DATA_DIR / phone / "captured_apis"

# 需要拦截的 API 路径关键词（可根据需要增删）
TARGET_KEYWORDS = [
    "getNewMarginInfo",
    "getMarginQueryInfo",
    "getMainPlan",
    "queryBalance",
    "queryOrderTimes",
    "queryFlow",
    "queryVoice",
    "getMargin",
    "serviceMargin",
    "website/uniApi",
    "website/service",
    "website/personalHome",
    "website/query",
    "balance",
    "margin",
    "plan",
    "flow",
    "voice",
    "sms",
]

def should_capture(url: str) -> bool:
    """判断该 URL 是否是需要捕获的 API"""
    url_lower = url.lower()
    # 只捕获 wx.10086.cn 域名的请求
    if "wx.10086.cn" not in url:
        return False
    # 捕获包含关键词的 API
    for kw in TARGET_KEYWORDS:
        if kw.lower() in url_lower:
            return True
    # 捕获所有 JSON 接口（路径中含 /api/ 或返回 application/json）
    if "/api/" in url_lower or "json" in url_lower:
        return True
    return False


async def capture(phone: str, url_only: bool = False, out_file: str = None):
    user_data_dir = get_user_data_dir(phone)
    if not user_data_dir.exists():
        print(f"❌ 未找到 {phone} 的登录状态")
        print(f"   请先运行: python chinamobile.py --login {phone}")
        return False

    capture_dir = get_capture_dir(phone)
    capture_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if out_file:
        out_path = Path(out_file)
    else:
        out_path = capture_dir / f"capture_{timestamp}.json"

    captured = []
    count = 0

    print("=" * 60)
    print(f"中国移动 API 捕获")
    print(f"  手机号: {phone}")
    print(f"  登录状态: {user_data_dir}")
    print(f"  输出文件: {out_path}")
    print(f"  模式: {'仅打印URL' if url_only else '保存完整请求/响应'}")
    print("=" * 60)
    print("💡 浏览器已打开，请手动操作页面以触发 API...")
    print("   📌 建议操作：")
    print("      1. 等待首页完全加载")
    print("      2. 点击「我的」、「套餐」等菜单")
    print("      3. 按 Ctrl+C 停止捕获并保存\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        async def handle_route(route, request):
            nonlocal count
            url = request.url
            if should_capture(url):
                count += 1
                method = request.method
                print(f"\n[{count}] {method} {url}")

                try:
                    response = await route.fetch()
                    status = response.status
                    headers = dict(response.headers)
                    body = await response.text()

                    # 尝试解析 JSON
                    try:
                        body_parsed = json.loads(body)
                        body_display = json.dumps(body_parsed, indent=2, ensure_ascii=False)[:500]
                    except (json.JSONDecodeError, TypeError):
                        body_parsed = None
                        body_display = body[:500]

                    entry = {
                        "seq":       count,
                        "method":    method,
                        "url":       url,
                        "status":    status,
                        "req_headers": dict(request.headers),
                        "resp_headers": headers,
                        "body_raw":  body[:10000],  # 限制 10KB
                        "body_json": body_parsed,
                        "timestamp": datetime.now().isoformat(),
                    }
                    captured.append(entry)

                    if not url_only:
                        print(f"   ✅ 已捕获 (status={status})")
                        print(f"   响应预览: {body_display}...")
                    else:
                        print(f"   ✅ 已记录")

                except Exception as e:
                    print(f"   ❌ 捕获失败: {e}")
                    captured.append({
                        "seq":     count,
                        "method":  method,
                        "url":     url,
                        "error":   str(e),
                        "timestamp": datetime.now().isoformat(),
                    })

                await route.continue_()
            else:
                await route.continue_()

        # 拦截所有请求
        await page.route("**/*", handle_route)

        # 打开首页
        print("🌐 正在打开首页...")
        try:
            await page.goto(
                "https://wx.10086.cn/website/spa/main/newHome",
                timeout=30000,
            )
            await page.wait_for_load_state("networkidle")
            print("   ✅ 首页加载完成，开始捕获...")
        except Exception as e:
            print(f"   ⚠️  首页加载超时或失败: {e}")
            print("   (浏览器已打开，仍可手动操作触发 API)")

        try:
            # 保持浏览器打开，直到用户关闭或 Ctrl+C
            await asyncio.sleep(999999)
        except asyncio.CancelledError:
            pass
        finally:
            print(f"\n📄 正在保存捕获结果 ({len(captured)} 条)...")
            out_path.write_text(
                json.dumps(captured, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print(f"   ✅ 已保存到: {out_path}")
            print(f"\n💡 下一步：")
            print(f"   1. 打开 {out_path.name} 查找余额相关 API")
            print(f"   2. 找到后告诉我 API 名称，我加到 chinamobile.py")
            await context.close()

    return True


def main():
    parser = argparse.ArgumentParser(
        description="中国移动 10086 API 捕获脚本（使用已登录状态）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python chinamobile_capture.py 18284888674                # 完整捕获
  python chinamobile_capture.py 18284888674 --url         # 只打印 URL
  python chinamobile_capture.py 18284888674 --out a.json  # 指定输出文件
""",
    )
    parser.add_argument("phone",   help="手机号（需已登录）")
    parser.add_argument("--url",   help="只打印 API URL，不保存响应体", action="store_true")
    parser.add_argument("--out",   help="指定输出 JSON 文件路径")
    args = parser.parse_args()

    if not args.phone.isdigit() or len(args.phone) < 11:
        print("错误：手机号格式不正确")
        sys.exit(1)

    try:
        asyncio.run(capture(args.phone, url_only=args.url, out_file=args.out))
    except KeyboardInterrupt:
        print("\n\n⏹  用户中断，正在保存已捕获的数据...")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
