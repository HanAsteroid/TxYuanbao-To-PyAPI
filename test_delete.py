import os
import sys
import time
import argparse

# 确保可以导入 aiapi 中的 driver 与函数
sys.path.append(os.path.dirname(__file__))
from aiapi import driver, delete_active_conversation  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402


def switch_to_cid(target_cid: str, wait: int = 10) -> bool:
    """切换到指定 dt-cid 的会话，如果为 'new' 则直接保持当前 active。"""
    if not target_cid or target_cid == 'active':
        return True
    if target_cid == 'new':
        return True
    try:
        el = WebDriverWait(driver, wait).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, f".yb-recent-conv-list__item[dt-cid='{target_cid}']"))
        )
        el.click()
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"切换到指定会话失败: {e}")
        return False


def get_active_cid(wait: int = 5) -> str:
    try:
        active = WebDriverWait(driver, wait).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".yb-recent-conv-list__item.active"))
        )
        return active.get_attribute("dt-cid") or ""
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser(description="单独测试删除当前激活会话")
    parser.add_argument("--cid", dest="cid", default="active", help="要删除前切换到的 dt-cid，可用 'active' 表示当前激活项")
    parser.add_argument("--no-delete", action="store_true", help="只打印定位信息与截图，不实际删除")
    args = parser.parse_args()

    # 前置截图
    ts = int(time.time()*1000)
    before_path = os.path.join("logs", f"before_delete_{ts}.png")
    driver.save_screenshot(before_path)
    print(f"已保存删除前截图: {before_path}")

    if not switch_to_cid(args.cid):
        print("切换会话失败，终止测试。")
        return

    active_cid = get_active_cid()
    print(f"当前激活会话: {active_cid}")

    if args.no_delete:
        print("按 --no-delete 参数跳过实际删除。")
        return

    ok = delete_active_conversation(driver)
    print(f"删除结果: {ok}")

    # 后置截图
    after_path = os.path.join("logs", f"after_delete_{ts}.png")
    driver.save_screenshot(after_path)
    print(f"已保存删除后截图: {after_path}")


if __name__ == "__main__":
    main()


