"""板端 DeepSeek 可达性测试（经 PC 中转代理）"""
import json
import urllib.request

KEY = open("/root/guitarist_app/config/deepseek_api.key").read().strip()
PROXY = "http://127.0.0.1:18080"


def try_call(proxy_url):
    handler = urllib.request.ProxyHandler({}) if not proxy_url else urllib.request.ProxyHandler(
        {"http": proxy_url, "https": proxy_url}
    )
    opener = urllib.request.build_opener(handler)
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是吉他助手小派，回答不超过一句话。"},
            {"role": "user", "content": "用一句话回应：小派，你能听见我吗？"},
        ],
        "max_tokens": 80,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY},
    )
    try:
        r = opener.open(req, timeout=25)
        body = json.loads(r.read().decode("utf-8"))
        reply = body["choices"][0]["message"]["content"]
        print("RESULT: OK  <== " + reply)
    except Exception as e:
        print("RESULT: FAIL [%s] %s" % (type(e).__name__, str(e)[:160]))


print("key_len =", len(KEY))
print("--- A) 经 PC 中转代理 ---")
try_call(PROXY)
print("--- B) 板子直连（预期失败，无 IPv4） ---")
try_call(None)
