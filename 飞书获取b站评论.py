import requests
import time
import json
import random

# --- 配置区 ---
BV_ID = "BV1diNFzxEkX"  # 目标视频BV号
CHECK_INTERVAL = 10     # 实时监控频率（秒）

# B站凭证
SESSDATA = "c3a031d7%2C1779446026%2Cfc807%2Ab2CjBk8dmZ7-zH2ISzgTKPtdue5NtvuQsGtkfGNvP1AMuwOxVXEq8YwMlOEK3dTopd3LkSVmhqYTcxZjJNeXhOa1BfQjBtSUpYQ2tjcUlfVE5vcEo5Vm85OHhRbzREREc4YTNPZ1F3WjNQNlZud3NMSVFoY3hGd0x1WWstcW5mejdtSWZ1SnNpNXZnIIEC"
BILI_JCT = "93e67354ed7acf823d684362b7228da7"

# 飞书配置
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/7ad3b084-9aef-4afe-9163-6c44ace71656"
# --------------

pushed_comment_ids = set()

def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://www.bilibili.com/video/{BV_ID}",
        "Origin": "https://www.bilibili.com",
        "Cookie": f"SESSDATA={SESSDATA}; bili_jct={BILI_JCT};",
        "Accept": "application/json, text/plain, */*",
    }

def send_to_feishu(content, time_str):
    """推送消息至飞书机器人"""
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"enable_forward": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🔔 实时复盘更新"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**时间**：{time_str}\n**内容**：{content}",
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "点击查看视频"},
                            "url": f"https://www.bilibili.com/video/{BV_ID}",
                            "type": "primary",
                        }
                    ],
                },
            ],
        },
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=10)
        if resp.json().get("code") == 0:
             print(f"✅ 飞书实时推送成功")
    except Exception as e:
        print(f"❌ 飞书网络请求失败: {e}")

def get_video_info(bvid):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=10).json()
        if resp["code"] == 0:
            return resp["data"]["aid"], resp["data"]["owner"]["mid"], resp["data"]["title"]
    except: pass
    return None, None, None

def fetch_sub_replies(aid, root_id, up_mid):
    """抓取楼中楼里UP主的回复"""
    sub_results = []
    url = f"https://api.bilibili.com/x/v2/reply/reply?type=1&oid={aid}&root={root_id}&pn=1&ps=10"
    try:
        res = requests.get(url, headers=get_headers(), timeout=10).json()
        if res["code"] == 0 and res["data"].get("replies"):
            for r in res["data"]["replies"]:
                if str(r["mid"]) == str(up_mid):
                    sub_results.append(r)
    except: pass
    return sub_results

def collect_logic(replies, aid, up_mid):
    """过滤出UP主的评论（包含主评论和楼中楼）"""
    found = []
    if not replies: return found
    for r in replies:
        if str(r["mid"]) == str(up_mid):
            found.append(r)
        if r.get("rcount", 0) > 0:
            found.extend(fetch_sub_replies(aid, r["rpid"], up_mid))
    return found

def process_reply(reply, mode, send_ding=True):
    """处理并推送单条评论"""
    global pushed_comment_ids
    rpid = reply["rpid"]
    if rpid not in pushed_comment_ids:
        content = reply["content"]["message"]
        if reply["content"].get("pictures"): content += " [图]"
        ctime = reply.get("ctime", 0)
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctime))

        print(f"[{mode}] {time_str}: {content[:40]}...")
        if send_ding:
            send_to_feishu(content, time_str)
        pushed_comment_ids.add(rpid)

def start_monitor():
    aid, up_mid, title = get_video_info(BV_ID)
    if not aid:
        print("❌ 连接失败，请检查网络或BV号。")
        return

    print(f"✅ 连接成功：{title}")
    
    # --- 1. 历史评论追溯推送 ---
    print(">>> 正在同步历史评论至飞书...")
    
    # 这里的 ps 可以设置大一点（比如 49），尽量一两次请求拿完历史
    # 如果该视频评论极多，可以写个循环请求 pn=1, 2, 3...
    history_urls = [
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=2&ps=30&pn=1", # 按时间排
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&ps=30&pn=1"  # 按热度排（防止漏掉置顶）
    ]
    
    all_history_found = []
    for url in history_urls:
        try:
            res = requests.get(url, headers=get_headers(), timeout=10).json()
            if res["code"] == 0:
                replies = res.get("data", {}).get("replies", [])
                # 提取 UP 主评论
                found = collect_logic(replies, aid, up_mid)
                all_history_found.extend(found)
            time.sleep(1) # 稍微温柔一点
        except: pass

    # 去重并按时间升序排列（先推旧的，后推新的）
    # 使用 dict 辅助去重
    unique_history = {r['rpid']: r for r in all_history_found}.values()
    sorted_history = sorted(unique_history, key=lambda x: x["ctime"])

    for r in sorted_history:
        # 这里 send_ding=True 就会推送到飞书
        process_reply(r, "历史回顾", send_ding=True)
    
    print(f">>> 历史同步完成（共 {len(sorted_history)} 条）。正在进入实时监控模式...\n")

    # --- 2. 实时监控循环 (逻辑保持不变) ---
    while True:
        try:
            url = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=2&ps=10"
            resp = requests.get(url, headers=get_headers(), timeout=10)
            if resp.status_code == 200:
                res = resp.json()
                if res["code"] == 0:
                    latest = collect_logic(res.get("data", {}).get("replies", []), aid, up_mid)
                    latest.sort(key=lambda x: x["ctime"])
                    for r in latest:
                        process_reply(r, "实时更新", send_ding=True)
                elif res["code"] == -412:
                    print("⚠️ 触发拦截，建议调大 CHECK_INTERVAL")
                    time.sleep(60)
        except Exception as e:
            print(f"监控异常: {e}")

        time.sleep(CHECK_INTERVAL + random.uniform(1, 3))
   

if __name__ == "__main__":
    start_monitor()