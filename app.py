import os
import re
import requests
from urllib.parse import quote
from flask import Flask, request, abort

from linebot.v3.messaging import (
    MessagingApi, 
    ApiClient, 
    Configuration, 
    ReplyMessageRequest, 
    TextMessage,
    TemplateMessage, 
    ButtonsTemplate, 
    PostbackAction
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import (MessageEvent, TextMessageContent, PostbackEvent)

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = ""
LINE_CHANNEL_SECRET = ""

# "Configuration -> ApiClient -> MessagingApi" 初始化流程
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration) 
messaging_api = MessagingApi(api_client) 

handler = WebhookHandler(LINE_CHANNEL_SECRET)

CITY_ENDPOINTS = {    
    "台北": "https://trafficapi.pma.gov.taipei/Parking/PayBill/CarID/{CarID}/CarType/{CarType}",
    "台中": "http://tcparkingapi.taichung.gov.tw:8081/NationalParkingPayBillInquiry.Api/Parking/PayBill/CarID/{CarID}/CarType/{CarType}",    
    "台南": "https://parkingbill.tainan.gov.tw/Parking/PayBill/CarID/{CarID}/CarType/{CarType}"  
        
}

user_state = {}

def call_city_api(city_name: str, plate: str, vehicle_type: str, timeout=2) -> dict:
    """呼叫單一城市 API，回傳 {'city': city_name, 'ok': bool, 'text': str}"""
    try:
        safe_plate = quote(plate)
        url_tpl = CITY_ENDPOINTS[city_name]
        url = url_tpl.format(CarID=safe_plate, CarType=vehicle_type)
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()

        status = data.get("Status")
        message = data.get("Message", "")
        result = data.get("Result")

        if status != "SUCCESS":            
            return {"city": city_name, "ok": False, "text": f"{city_name} 查詢失敗：{status or ''} {message or ''}".strip()}

        if result is None:
            return None
        
        lines = [f"【{city_name}】",
                 f"筆數：{result.get('TotalCount', 0)}  總金額：NT$ {int(result.get('TotalAmount', 0))}"]

        bills = result.get("Bills", []) or []
        if bills:
            lines.append("— 停車單 —")
            for i, b in enumerate(bills[:100], 1):
                date = b.get("ParkingDate", "")
                limit = b.get("PayLimitDate", "")
                amt = b.get("PayAmount", b.get("Amount", 0))
                hours = b.get("ParkingHours", "")
                lines.append(f"{i}. {date} 截止:{limit} 時數:{hours} 應繳:NT$ {int(amt)}")

        reminders = result.get("Reminders", []) or []
        if reminders:
            lines.append("— 催繳單 —")
            for i, rm in enumerate(reminders[:100], 1):
                rno = rm.get("ReminderNo", "")
                rlimit = rm.get("ReminderLimitDate", "")
                rpay = rm.get("PayAmount", 0)
                extra = rm.get("ExtraCharge", 0)
                lines.append(f"{i}. 單號:{rno} 截止:{rlimit} 應繳:NT$ {rpay}（含工本費:{extra}）")

        return {"city": city_name, "ok": True, "text": "\n".join(lines)}

    except requests.exceptions.Timeout:
        return {"city": city_name, "ok": False, "text": f"【{city_name}】查詢逾時，稍後再試。"}
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "HTTP"
        return {"city": city_name, "ok": False, "text": f"【{city_name}】查詢失敗（{code}）。"}
    except ValueError:
        return {"city": city_name, "ok": False, "text": f"【{city_name}】回傳非 JSON（暫時性異常）。"}
    except Exception as e:
        return {"city": city_name, "ok": False, "text": f"【{city_name}】查詢錯誤"}

def query_parking_fees_multi(plate: str, vehicle_type: str, cities=None) -> str:
    
    if cities is None:
        cities = ["台北", "台中", "台南"]

    header = [f"車牌：{plate}", f"車種：{'汽車' if vehicle_type=='C' else '機車'}", ""]
    parts = []
    for city in cities:
        if city not in CITY_ENDPOINTS:
            parts.append(f"【{city}】尚未支援。")
            continue
        res = call_city_api(city, plate, vehicle_type)
        if not res:
            # res 是 None = 該城查無待繳 → 不顯示
            continue
        if res.get("ok"):
            parts.append(res["text"])
        else:            
            parts.append(res["text"])
    
    if not parts:
        return "\n".join(header + ["目前所有查詢城市都沒有待繳。"])
    return "\n".join(header + parts)


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(e)
        abort(400)
    return 'OK'

# 入口：查費
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    user_id = event.source.user_id
    text = event.message.text.strip()

    if text in ("查費", "查詢停車費", "查停車費","查詢"):
        user_state[user_id] = {"stage": "await_plate"}
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="請輸入車牌（可含英數/中文字/「-」，例如：ABC-1234）")]
            )
        )
        return

    # 使用者輸入車牌
    state = user_state.get(user_id)
    if state and state.get("stage") == "await_plate":
        plate = text.upper()
        # 規範：英數/中文字/「-」；其他符號為格式錯誤（對應 ERR02）
        if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff\-]+", plate):
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="車牌格式不符規範，請再輸入一次（僅允許英數/中文/「-」）。")]
                )
            )
            return

        state["plate"] = plate
        state["stage"] = "await_type"

        # 讓使用者選 C/M（汽車/機車）
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TemplateMessage(
                        alt_text="請選擇車種",
                        template=ButtonsTemplate(
                            text=f"車牌：{plate}\n請選擇車種：",
                            actions=[
                                PostbackAction(label="汽車", data="type=C"),
                                PostbackAction(label="機車", data="type=M"),
                                PostbackAction(label="取消", data="type=cancel"),
                            ]
                        )
                    )
                ]
            )
        )
        return

    # 指引
    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text="輸入「查費」開始。")]
        )
    )

# 處理車種選擇
@handler.add(PostbackEvent)
def handle_postback(event: PostbackEvent):
    user_id = event.source.user_id
    data = event.postback.data or ""
    state = user_state.get(user_id)

    if data == "type=cancel":
        user_state.pop(user_id, None)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="已取消本次查詢。")]
            )
        )
        return

    if state and state.get("stage") == "await_type" and data.startswith("type="):
        vehicle_type = data.split("=", 1)[1]  # C or M
        plate = state.get("plate")

        result_text = query_parking_fees_multi(plate, vehicle_type, cities=["台北", "台中", "台南"])
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=result_text)]
            )
        )
        user_state.pop(user_id, None)
        return

    messaging_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text="請輸入「查詢」重新開始。")]
        )
    )

if __name__ == "__main__":
    app.run(port=5000)


