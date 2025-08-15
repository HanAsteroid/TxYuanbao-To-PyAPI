import requests
import json
import base64

def file_to_base64(file_path):
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

# 测试数据
test_data = {
    "sequence": "fdb3105d-3c7b-452f-9d6d-96f622fad47d",
    "text": "联网搜索 btc行情请给我一套 btc日内交易策略，内容只包含方向/开仓价/止盈价/止损价的对应数值，例如空/81,300/78,000/83,500.注意事项: 1.除了数值外不要给我返回其他额外内容;2.每次只给我返回当前你认为概率最大的一条数据; 3.开仓价/止盈价/止损价不要给一个范围，需要给一个具体的数值",
    "mode":"hunyuan"
}

try:
    response = requests.post(
        "http://127.0.0.1:8000/hunyuan",
        json=json.dumps(test_data, ensure_ascii=False),
        headers={"Content-Type": "application/json"}
    )
    print("响应状态码:", response.status_code)
    print("响应内容:", response.json())
    
except Exception as e:
    print("请求失败:", str(e))
 