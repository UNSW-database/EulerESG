import os
from openai import OpenAI
client = OpenAI(
    # 新加坡和北京地域的API Key不同。获取API Key：https://www.alibabacloud.com/help/zh/model-studio/get-api-key
    api_key=os.getenv("QWEN_API_KEY"),
    # 以下为新加坡地域base_url，若使用北京地域的模型，需将base_url替换为https://dashscope-intl.aliyuncs.com/compatible-mode/v1
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
)
completion = client.chat.completions.create(
    model="qwen-plus", # 模型列表：https://www.alibabacloud.com/help/zh/model-studio/getting-started/models
    messages=[{"role": "user", "content": "你是谁？"}]
)
print(completion.choices[0].message.content)
