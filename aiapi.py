from flask import Flask, request, jsonify
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from setbrowser import *
import json
import time
import os
import re
import glob
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import base64
import logging
from logging.handlers import RotatingFileHandler

app = Flask(__name__)
lock = threading.Lock()

# 日志配置
logs_dir = 'logs'
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger('aiapi')
logger.setLevel(logging.INFO)

_log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

_app_handler = RotatingFileHandler(os.path.join(logs_dir, 'app.log'), maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
_app_handler.setLevel(logging.INFO)
_app_handler.setFormatter(_log_formatter)

_err_handler = RotatingFileHandler(os.path.join(logs_dir, 'error.log'), maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
_err_handler.setLevel(logging.ERROR)
_err_handler.setFormatter(_log_formatter)

if not logger.handlers:
    logger.addHandler(_app_handler)
    logger.addHandler(_err_handler)

def _sanitize_payload(data):
    if not isinstance(data, dict):
        return data
    sanitized = {}
    for key, value in data.items():
        try:
            if key == 'picture' or re.match(r'file\d+', str(key)):
                if isinstance(value, str):
                    sanitized[key] = f"<base64 length={len(value)}>"
                else:
                    sanitized[key] = "<binary>"
            elif isinstance(value, (bytes, bytearray)):
                sanitized[key] = f"<bytes length={len(value)}>"
            else:
                sanitized[key] = value
        except Exception:
            sanitized[key] = "<unserializable>"
    return sanitized

def _truncate_text(text, max_len=1000):
    if not isinstance(text, str):
        return text
    return text if len(text) <= max_len else (text[:max_len] + '...<truncated>')

# 初始化浏览器
driver = autoh('https://yuanbao.tencent.com/login')
driver.refresh()
print("浏览器初始化完成")
logger.info("浏览器初始化完成并已刷新")

    

def wait_for_stable_text(element, wait_time=10, timeout=999):
    """等待文本稳定"""
    class TextChecker:
        def __init__(self, element, wait_time):
            self.element = element
            self.wait_time = wait_time
            self.last_text = None
            self.stable_time = None
            self.skip_patterns = [
                r'找到\d+相关资料',
                r'正在分析',
                r'正在处理',
                r'正在生成',
                r'引用\d+篇资料作为参考'
            ]
        
        def should_skip(self, text):
            for pattern in self.skip_patterns:
                if re.search(pattern, text):
                    return True
            return False
        
        def __call__(self, driver):
            current_text = self.element.text
            print(f"当前文本内容: {current_text}")
            
            if self.should_skip(current_text):
                print("检测到中间状态文本，继续等待...")
                return False
                
            if current_text != self.last_text:
                self.last_text = current_text
                self.stable_time = time.time()
                return False
            elif self.stable_time and (time.time() - self.stable_time) >= self.wait_time:
                print(f"文本已稳定: {current_text}")
                return current_text
            return False
    
    try:
        return WebDriverWait(element.parent, timeout).until(
            TextChecker(element, wait_time)
        )
    except Exception as e:
        print(f"等待文本超时: {str(e)}")
        logger.error(f"等待文本超时: {str(e)}")
        raise TimeoutError(f"等待文本超时（{timeout}秒）")

def get_new_message(driver, timeout=999):
    """获取新消息"""
    print("等待新消息...")
    initial_messages = driver.find_elements(By.CSS_SELECTOR, '.agent-chat__bubble__content')
    known_texts = {msg.text for msg in initial_messages}
    
    class NewMessage:
        def __init__(self, known_texts):
            self.known_texts = known_texts
        
        def __call__(self, driver):
            current_messages = driver.find_elements(By.CSS_SELECTOR, '.agent-chat__bubble__content .agent-chat__conv--ai__speech_show')
            for msg in current_messages:
                if msg.text not in self.known_texts:
                    print(f"发现新消息: {msg.text}")
                    return msg
            return False
    
    try:
        return WebDriverWait(driver, timeout).until(
            NewMessage(known_texts)
        )
    except Exception:
        print("等待新消息超时")
        logger.error("等待新消息超时")
        raise TimeoutError("等待新消息超时")

def extract_references(driver):
    """展开引用来源抽屉并解析数据源"""
    references = []
    try:
        # 预处理：移除/等待遮罩层以避免点击被拦截
        try:
            driver.execute_script("""
              document.querySelectorAll('.temp-mode-guide__info,.t-dialog__mask,.t-guide,.t-popup__mask')
                .forEach(e => e.remove());
            """)
        except Exception:
            pass

        try:
            WebDriverWait(driver, 3).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, ".temp-mode-guide__info"))
            )
        except TimeoutException:
            pass

        ref_toggle = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".hyc-card-box-search-ref__content__header-wrapper"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ref_toggle)
        try:
            WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".hyc-card-box-search-ref__content__header-wrapper"))
            )
            ref_toggle.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", ref_toggle)

        # 等待抽屉与引用列表出现
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".t-drawer__body #chatReferenceList"))
        )

        items = driver.find_elements(By.CSS_SELECTOR, "#chatReferenceList .agent-dialogue-references__list .agent-dialogue-references__item")
        for item in items:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", item)
                card = item.find_element(By.CSS_SELECTOR, ".hyc-common-markdown__ref_card")
                url = (card.get_attribute("data-url") or "").strip()
                if not url:
                    links = item.find_elements(By.CSS_SELECTOR, "a[href]")
                    if links:
                        url = links[0].get_attribute("href") or ""

                title_text = ""
                # 1) 精确选择器
                title_els = item.find_elements(By.CSS_SELECTOR, ".hyc-common-markdown__ref_card-title span")
                if not title_els:
                    title_els = item.find_elements(By.CSS_SELECTOR, ".hyc-common-markdown__ref_card-title")

                if title_els:
                    el = title_els[0]
                    # 优先使用 JS 读取 textContent（避免可见性/省略号影响）
                    try:
                        title_text = (driver.execute_script("return (arguments[0].textContent || '').trim();", el) or "").strip()
                    except Exception:
                        title_text = (el.text or "").strip()
                    if not title_text:
                        title_text = (el.get_attribute("title") or "").strip()

                # 2) 兜底：从卡片本身读取 aria-label 或 title
                if not title_text:
                    title_text = (card.get_attribute("aria-label") or card.get_attribute("title") or "").strip()

                # 3) 兜底：从任意包含 title 的元素读
                if not title_text:
                    any_title = item.find_elements(By.CSS_SELECTOR, "[title]")
                    if any_title:
                        title_text = (any_title[0].get_attribute("title") or "").strip()

                if title_text or url:
                    references.append({"title": title_text, "url": url})
            except Exception as _:
                continue
    except Exception as e:
        print(f"引用来源解析失败或未找到: {str(e)}")
    return references

def upload_image(driver, image_data):
    """上传图片文件"""
    print("开始上传图片...")
    try:
        temp_file = f"temp_img_{int(time.time()*1000)}.png"
        main_window = driver.current_window_handle
        
        print("点击上传按钮")
        upload_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".index__upload-item___I2o3F"))
        )
        upload_btn.click()
        time.sleep(1)

        print("定位文件输入框")
        file_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".index__input-uploader___L9wop input[type='file']"))
        )

        print("创建临时文件")
        with open(temp_file, "wb") as f:
            f.write(base64.b64decode(image_data))
        
        print("上传文件")
        file_input.send_keys(os.path.abspath(temp_file))
        #虽然这个关闭可能没啥用
        print("关闭上传窗口")
        driver.find_element(By.TAG_NAME, 'body').click()
        driver.switch_to.window(main_window)
        time.sleep(3)
        
        print("清理临时文件")
        os.remove(temp_file)
        print("图片上传完成")
        return True
    except Exception as e:
        print(f"图片上传出错: {str(e)}")
        logger.exception(f"图片上传出错: {str(e)}")
        for temp_file in glob.glob("temp_img_*.png"):
            if os.path.exists(temp_file):
                os.remove(temp_file)
        return False

def upload_files(driver, files):
    """上传多个文件"""
    print(f"准备上传 {len(files)} 个文件")
    try:
        image_types = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        
        print("打开上传界面")
        upload_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".index__upload-item___ywIAD"))
        )
        upload_btn.click()
        time.sleep(1)
        
        print("点击本地文件上传")
        local_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".index__upload-btn___wGp7B"))
        )
        local_btn.click()
        time.sleep(1)
        
        print("定位文件输入框")
        file_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".index__input-uploader___Vvv7x input[type='file']"))
        )
        
        file_paths = []
        for i, (file_key, file_data) in enumerate(files.items(), 1):
            print(f"处理文件 {i}/{len(files)}")
            original_name = request_data.get(f'filename{i}', 'file')
            ext = os.path.splitext(original_name)[1].lower()
            
            if ext in image_types:
                print(f"跳过图片文件: {original_name}")
                continue
            
            ext = ext if ext else '.bin'
            temp_file = f"temp_{int(time.time()*1000)}_{i}{ext}"
            
            print(f"创建临时文件 {temp_file}")
            with open(temp_file, "wb") as f:
                f.write(base64.b64decode(file_data))
            file_paths.append(os.path.abspath(temp_file))
        
        if file_paths:
            print("开始上传文件")
            file_input.send_keys("\n".join(file_paths))
            time.sleep(2)
            
            errors = driver.find_elements(By.CSS_SELECTOR, ".upload-error-message")
            if errors:
                print(f"上传错误: {errors[0].text}")
                raise Exception(errors[0].text)
        
        print("关闭上传窗口")
        driver.find_element(By.TAG_NAME, 'body').click()
        time.sleep(1)
        
        print("清理临时文件")
        for path in file_paths:
            if os.path.exists(path):
                os.remove(path)
        
        print("文件上传完成")
        return True
    except Exception as e:
        print(f"文件上传失败: {str(e)}")
        logger.exception(f"文件上传失败: {str(e)}")
        for temp_file in glob.glob("temp_*"):
            if os.path.exists(temp_file):
                os.remove(temp_file)
        return False

def change_model(driver, model):
    """切换模型"""
    print(f"准备切换到 {model} 模型")
    try:
        print("点击模型切换按钮")
        switch_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "[dt-button-id='model_switch']"))
        )
        switch_btn.click()
        time.sleep(1)
        
        print("选择模型")
        # 修改后的选择器，更可靠地定位模型选项
        if model.lower() == "deepseek":
            # 使用包含特定文本的元素
            model_options = driver.find_elements(By.XPATH, "//*[contains(@class, 't-dropdown__item')]")
            for option in model_options:
                if "DeepSeek" in option.text:
                    option.click()
                    break
        elif model.lower() == "hunyuan":
            model_options = driver.find_elements(By.XPATH, "//*[contains(@class, 't-dropdown__item')]")
            for option in model_options:
                if "Hunyuan" in option.text:
                    option.click()
                    break
        else:
            return False
        
        time.sleep(1)
        print("模型切换完成")
        return True
    except Exception as e:
        print(f"模型切换失败: {str(e)}")
        logger.exception(f"模型切换失败: {str(e)}")
        return False
    
def refresh_page():
    """定时刷新页面，防检测(虽然我也不知道有没有检测)"""
    if not lock.acquire(blocking=False):
        print("已有任务运行，跳过刷新")
        return
    try:
        print("执行页面刷新")
        driver.refresh()
    finally:
        lock.release()

scheduler = BackgroundScheduler()
scheduler.add_job(refresh_page, 'interval', seconds=1000)
scheduler.start()

@app.route('/hunyuan', methods=['POST'])
def handle_request():
    """处理请求"""
    print("收到新请求")
    if not lock.acquire(blocking=False):
        print("系统繁忙")
        logger.warning("系统繁忙：有并发请求被拒绝")
        return "系统繁忙，请稍后再试", 429
    
    try:
        global request_data
        # 同时兼容：
        # - 正确用法：客户端直接发送 JSON 对象（Postman 等）
        # - 误用方式：客户端先 json.dumps，再放入 json= 参数（会变成 JSON 字符串）
        raw_json = request.get_json(silent=True)
        if raw_json is None:
            return jsonify({"error": "请求体不是合法的 JSON"}), 400
        request_data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        if not request_data:
            print("空请求")
            return jsonify({"error": "请求数据不能为空"}), 400
        try:
            logger.info(f"/hunyuan 请求: {json.dumps(_sanitize_payload(request_data), ensure_ascii=False)}")
        except Exception:
            logger.info(f"/hunyuan 请求(无法序列化)，keys={list(request_data.keys())}")
        
        print("处理请求数据")
        response = {}
        session_id = request_data.get('sequence')
        
        current = driver.find_elements(By.CSS_SELECTOR, ".yb-recent-conv-list__item.active")
        if current and current[0].get_attribute("dt-cid") == session_id:
            print("已是当前会话")
        else:
            if session_id == "new":
                print("创建新会话")
                try:
                    new_btn = WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".yb-tencent-yuanbao-list__item .yb-tencent-yuanbao-list__logo"))
                    )
                    new_btn[0].click()
                    time.sleep(2)
                    
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".agent-chat__conv--agent-homepage-v2__greeting"))
                    )
                except Exception as e:
                    print(f"创建会话失败: {str(e)}")
                    logger.exception(f"创建会话失败: {str(e)}")
                    return jsonify({"error": f"创建会话失败: {str(e)}"}), 500
            else:
                print(f"切换到会话 {session_id}")
                try:
                    session = WebDriverWait(driver, 10).until(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, f"[dt-cid='{session_id}']"))
                    )
                    session[0].click()
                    time.sleep(2)
                except Exception as e:
                    print(f"切换会话失败: {str(e)}")
                    logger.exception(f"切换会话失败: {str(e)}")
                    return jsonify({"error": f"切换会话失败: {str(e)}"}), 500
        
        if request_data.get('mode'):
            print(f"切换模型到 {request_data['mode']}")
            if not change_model(driver, request_data['mode']):
                return jsonify({"error": "模型切换失败"}), 500
            

        if request_data.get('picture') and request_data['picture'] != "new":
            print("上传图片")
            if not upload_image(driver, request_data['picture']):
                return jsonify({"error": "图片上传失败"}), 500
        
        files = {k: v for k, v in request_data.items() if re.match(r'file\d+', k)}
        if files:
            print(f"上传 {len(files)} 个文件")
            if not upload_files(driver, files):
                return jsonify({"error": "文件上传失败"}), 500
        
        try:
            print("输入文本")
            input_box = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".ql-editor.ql-blank"))
            )
            input_box.send_keys(request_data.get('text'))
            
            print("发送消息")
            send_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "[id='yuanbao-send-btn']"))
            )
            send_btn.click()
            
            print("等待回复")
            new_msg = get_new_message(driver)
            final_text = wait_for_stable_text(new_msg)
            
            references = extract_references(driver)
            
            print("获取会话ID")
            active = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".yb-recent-conv-list__item.active"))
            )
            current_id = active.get_attribute("dt-cid")
            
            response["id"] = current_id
            response["text"] = final_text
            response["references"] = references
            print("请求处理完成")
            try:
                resp_log = {
                    "id": current_id,
                    "text": _truncate_text(final_text, 1000),
                    "references_preview": references[:5],
                    "references_total": len(references)
                }
                logger.info(f"/hunyuan 响应(200): {json.dumps(resp_log, ensure_ascii=False)}")
            except Exception:
                logger.info(f"/hunyuan 响应(200) 已返回，无法序列化日志")
            return jsonify(response)
            
        except Exception as e:
            print(f"消息发送失败: {str(e)}")
            try:
                logger.exception(f"消息发送失败: {str(e)}")
            except Exception:
                pass
            return jsonify({"error": f"消息发送失败: {str(e)}"}), 500
            
    except Exception as e:
        print(f"处理出错: {str(e)}")
        try:
            logger.exception(f"处理出错: {str(e)}")
        except Exception:
            pass
        return jsonify({"error": f"处理出错: {str(e)}"}), 500
    finally:
        lock.release()
        print("释放锁")
        logger.info("释放锁，完成一次 /hunyuan 调用")

if __name__ == '__main__':
    print("启动服务")
    app.run(host='0.0.0.0', port=8000)
